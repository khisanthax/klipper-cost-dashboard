"""DDL and migration provenance must survive failed or concurrent startup."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from core import db


class MigrationAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.migrations = self.root / 'migrations'
        self.migrations.mkdir()
        self.database = self.root / 'kcd.db'
        self.patch = patch.object(db, 'MIGRATIONS_DIR', str(self.migrations))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def connect(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def migration(self, sql):
        (self.migrations / '0009_business_orders.sql').write_text(sql, encoding='utf-8')

    def test_ledger_failure_rolls_back_ddl_and_retry_is_recoverable(self):
        self.migration('CREATE TABLE business_orders (id INTEGER PRIMARY KEY, amount INTEGER);')
        with closing(self.connect()) as conn:
            db._ensure_schema_migrations(conn)
            conn.execute("CREATE TRIGGER reject_ledger BEFORE INSERT ON schema_migrations BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                db.apply_migrations(conn)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='business_orders'").fetchone())
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)
            conn.execute('DROP TRIGGER reject_ledger')
            self.assertEqual(db.apply_migrations(conn), ['0009_business_orders'])
            self.assertEqual(db.apply_migrations(conn), ['0009_business_orders'])
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 1)

    def test_script_failure_rolls_back_all_statements_including_trigger(self):
        self.migration("CREATE TABLE business_orders (id INTEGER PRIMARY KEY); CREATE TRIGGER audit_order AFTER INSERT ON business_orders BEGIN SELECT 'semicolon; inside literal'; SELECT 1; END; INVALID SQL;")
        with closing(self.connect()) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                db.apply_migrations(conn)
            self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE name IN ('business_orders','audit_order')").fetchall())
            self.assertFalse(conn.in_transaction)

    def test_concurrent_fresh_initializers_recheck_ledger_under_writer_lock(self):
        self.migration('CREATE TABLE business_orders (id INTEGER PRIMARY KEY); INSERT INTO business_orders VALUES (1);')
        start = Barrier(2)
        def initialize(_):
            with closing(self.connect()) as conn:
                start.wait(timeout=10)
                return db.apply_migrations(conn)
        with ThreadPoolExecutor(2) as workers:
            self.assertEqual(list(workers.map(initialize, range(2))), [['0009_business_orders']] * 2)
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM business_orders').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 1)

    def test_pending_migration_never_commits_caller_transaction(self):
        self.migration('CREATE TABLE business_orders (id INTEGER PRIMARY KEY);')
        with closing(self.connect()) as conn:
            conn.execute('CREATE TABLE caller_work (id INTEGER PRIMARY KEY)')
            conn.execute('INSERT INTO caller_work VALUES (1)')
            with self.assertRaisesRegex(sqlite3.ProgrammingError, 'existing transaction'):
                db.apply_migrations(conn)
            self.assertTrue(conn.in_transaction)
            conn.rollback()
            self.assertEqual(conn.execute('SELECT count(*) FROM caller_work').fetchone()[0], 0)

    def test_existing_unrecorded_schema_is_not_silently_stamped(self):
        self.migration('CREATE TABLE business_orders (id INTEGER PRIMARY KEY);')
        with closing(self.connect()) as conn:
            conn.execute('CREATE TABLE business_orders (id INTEGER PRIMARY KEY)')
            conn.execute('INSERT INTO business_orders VALUES (9)')
            conn.commit()
            with self.assertRaises(sqlite3.OperationalError):
                db.apply_migrations(conn)
            self.assertEqual(conn.execute('SELECT id FROM business_orders').fetchone()[0], 9)
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)

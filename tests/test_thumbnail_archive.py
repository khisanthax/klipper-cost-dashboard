import base64
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from contextlib import closing

from core import db,thumbnail_archive as archive,thumbnails,backup

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8t0AAAAASUVORK5CYII=')


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'data';self.root.mkdir()
        for p in [patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'sql','KCD_SQL_ONLY_FAIL_FAST':'0'}),
                  patch.object(archive,'_source_failures',{}),
                  patch.object(db,'_db_path',lambda:str(self.root/'kcd.db')),
                  patch.object(archive,'ARCHIVE_ROOT',self.root/'thumbnails/objects'),
                  patch.object(thumbnails,'_CACHE_ROOT',str(self.root/'thumb_cache')),
                  patch.object(backup,'DATA_DIR',str(self.root)),patch.object(backup,'BACKUPS_DIR',str(self.root/'backups'))]:
            p.start();self.addCleanup(p.stop)
        from core.registration import register_printer
        register_printer('One','http://old:7125')
        self.job('job-one')

    def job(self,uid):
        with db.connect_db() as conn:
            db.apply_migrations(conn)
            db.upsert_job(conn,{'job_uid':uid,'printer':'One','filename':'part.gcode','timestamp':1700000000})

    def test_captured_at_start_survives_deleted_source_migration_and_restart(self):
        digest=archive.retain_image(PNG)
        with patch.object(archive,'fetch_image',side_effect=AssertionError('Source must not be needed')):
            archive.capture_job('job-one','One','part.gcode',live_asset=digest)
        with db.connect_db() as conn:
            conn.execute("UPDATE printers SET name='Moved',moonraker_url='http://new:7129' WHERE name='One'")
        import app
        with app.app.test_request_context():
            url=app.get_job_thumbnail_url('Moved','part.gcode','small',job_uid='job-one')
        self.assertEqual(url,'/thumbnails/'+digest)
        with app.app.test_client().get(url) as response:self.assertEqual(response.data,PNG)
        self.assertEqual(archive.get_job_asset('job-one'),digest)

    def test_shared_images_are_deduplicated_and_job_deletion_is_safe(self):
        self.job('job-two');digest=archive.retain_image(PNG)
        for uid in ['job-one','job-two']:archive.capture_job(uid,'One','part.gcode',live_asset=digest)
        with db.connect_db() as conn:conn.execute("DELETE FROM jobs WHERE job_uid='job-one'")
        self.assertIsNone(archive.get_job_asset('job-one'))
        self.assertEqual(archive.prune_unreferenced(apply=True,min_age_seconds=0),0)
        self.assertTrue(archive.asset_path(digest))
        with db.connect_db() as conn:conn.execute("DELETE FROM jobs WHERE job_uid='job-two'")
        self.assertEqual(archive.prune_unreferenced(apply=True,min_age_seconds=0),1)
        self.assertIsNone(archive.asset_path(digest))

    def test_cache_cleanup_never_removes_archive_objects(self):
        digest=archive.retain_image(PNG)
        with patch.object(thumbnails,'_MAX_FILES',1):
            for i in range(3):(archive.ARCHIVE_ROOT/(str(i)+'.png')).write_bytes(PNG)
            thumbnails._evict_cache(str(archive.ARCHIVE_ROOT))
        self.assertTrue(archive.asset_path(digest))

    def test_concurrent_job_deletion_before_reference_is_an_atomic_noop(self):
        digest=archive.retain_image(PNG)
        connect=db.connect_db
        deleted=[]
        class ConcurrentDelete:
            def __init__(self):self.connection=connect()
            def __enter__(self):self.connection.__enter__();return self
            def __exit__(self,*args):
                try:return self.connection.__exit__(*args)
                finally:self.connection.close()
            def __getattr__(self,name):return getattr(self.connection,name)
            def execute(self,sql,params=()):
                if sql.startswith('INSERT INTO job_thumbnails') and not deleted:
                    with closing(connect()) as other, other:
                        other.execute("DELETE FROM jobs WHERE job_uid='job-one'")
                    deleted.append(True)
                return self.connection.execute(sql,params)
        with patch.object(db,'connect_db',side_effect=ConcurrentDelete):
            self.assertFalse(archive._reference('job-one',digest,'archived'))
        with closing(connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM job_thumbnails').fetchone()[0],0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertTrue(archive.asset_path(digest))  # A shared/live asset must not be removed.

    def test_deleted_during_http_recovery_returns_missing_and_keeps_other_asset(self):
        import app
        digest=archive.retain_image(PNG)
        self.job('job-two');archive.capture_job('job-two','One','part.gcode',live_asset=digest)
        def fetched(*args,**kwargs):
            with db.connect_db() as conn:conn.execute("DELETE FROM jobs WHERE job_uid='job-one'")
            return digest,'archived',None
        with patch.object(archive,'fetch_image',side_effect=fetched):
            response=app.app.test_client().get('/jobs/job-one/thumbnail')
        self.assertEqual(response.status_code,404)
        self.assertEqual(response.headers['X-KCD-Thumbnail-Status'],'job_deleted')
        self.assertIsNone(archive.get_job_asset('job-one'))
        self.assertEqual(archive.get_job_asset('job-two'),digest)

    def test_unrelated_foreign_key_and_database_errors_are_not_hidden(self):
        with self.assertRaises(sqlite3.IntegrityError):
            archive._reference('job-one','f'*64,'archived')
        with patch.object(db,'connect_db',side_effect=sqlite3.OperationalError('database unavailable')):
            with self.assertRaises(sqlite3.OperationalError):
                archive._reference('job-one',None,'network_error')

    def test_exact_saved_token_recovers_expired_cache(self):
        token='a'*40
        with db.connect_db() as conn:conn.execute('UPDATE jobs SET thumbnail=? WHERE job_uid=?',(token,'job-one'))
        directory=Path(thumbnails._CACHE_ROOT)/'Old_Name';directory.mkdir(parents=True)
        path=directory/(token+'_small.png');path.write_bytes(PNG);os.utime(path,(1600000000,1600000000))
        self.assertTrue(archive.recover_cached_job('job-one','One','part.gcode',token,1700000000))
        path.unlink()
        self.assertTrue(archive.asset_path(archive.get_job_asset('job-one')))

    def test_repeated_filename_tokens_are_not_guessed(self):
        self.job('job-two');token='b'*40
        with db.connect_db() as conn:conn.execute('UPDATE jobs SET thumbnail=?',(token,))
        directory=Path(thumbnails._CACHE_ROOT)/'One';directory.mkdir(parents=True)
        (directory/(token+'_card.png')).write_bytes(PNG)
        self.assertIsNone(archive.recover_cached_job('job-one','One','part.gcode',token))

    def test_source_failures_are_distinct(self):
        class Response:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return self.data
        with patch.object(thumbnails,'resolve_moonraker_base_url',return_value='http://source'):
            with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('http://source',404,'gone',{},None)):
                self.assertEqual(archive.fetch_image('One','part.gcode')[1],'source_unavailable')
            with patch('urllib.request.urlopen',side_effect=urllib.error.URLError('offline')):
                self.assertEqual(archive.fetch_image('One','part.gcode')[1],'network_error')
            archive._source_failures.clear()  # Independent source-mode fixture; bypass the tested outage backoff.
            with patch('urllib.request.urlopen',return_value=Response(json.dumps({'result':{'thumbnails':[]}}).encode())):
                self.assertEqual(archive.fetch_image('One','part.gcode')[1],'no_slicer_thumbnail')
            with patch('urllib.request.urlopen',return_value=Response(json.dumps({'result':{'modified':1800000000,'thumbnails':[]}}).encode())):
                self.assertEqual(archive.fetch_image('One','part.gcode',ended_at=1700000000,historical=True)[1],'source_changed')

    def test_backup_restore_contains_database_references_and_image_bytes(self):
        digest=archive.retain_image(PNG);archive.capture_job('job-one','One','part.gcode',live_asset=digest)
        path=backup.create_backup_archive()
        restored=Path(self.tmp.name)/'restore'
        with tarfile.open(path) as tar:tar.extractall(restored,filter='data')
        with closing(sqlite3.connect(restored/'data/kcd.db')) as conn:
            self.assertEqual(conn.execute('SELECT asset_sha256 FROM job_thumbnails').fetchone()[0],digest)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual((restored/'data/thumbnails/objects'/(digest+'.png')).read_bytes(),PNG)

    def test_bad_image_and_path_traversal_are_rejected(self):
        with self.assertRaises(ValueError):archive.retain_image(b'<script>bad</script>')
        self.assertIsNone(archive.asset_path('../secret.json'))

    def test_backfill_dry_run_changes_nothing(self):
        before=list(archive.ARCHIVE_ROOT.glob('*'))
        self.assertEqual(archive.backfill()['candidates'],1)
        self.assertEqual(before,list(archive.ARCHIVE_ROOT.glob('*')))

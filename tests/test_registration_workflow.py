import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import db
from core.registration import register_printer, registered_clients, RegistrationConflict
from core.readiness import check_sql_only_readiness
from core.sql_only import SqlOnlyFilesystemMonitor


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patches = [patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql", "KCD_SQL_ONLY_FAIL_FAST": "0"}),
                        patch.object(db, "_db_path", lambda: str(Path(self.tmp.name)/"kcd.db"))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_fresh_and_multiple_registration_is_ready_and_file_free(self):
        with SqlOnlyFilesystemMonitor():
            register_printer("Ender5P", "http://192.168.2.130:7125")
            register_printer("MercuryTwo", "http://192.168.2.55:7129")
            self.assertTrue(check_sql_only_readiness()["ready"])
        self.assertEqual(len(registered_clients()), 2)

    def test_repeated_registration_preserves_prices_and_history(self):
        from core.printer_lifecycle import _load_setting, _save_setting
        first = register_printer("Ender5P", "http://old:7125")
        with db.connect_db() as conn:
            db.upsert_job(conn, {"printer":"Ender5P", "job_uid":"history", "filename":"part.gcode"})
            settings = _load_setting(conn, "printer_settings")
            settings["Ender5P"]["rate_per_hour"] = 9
            _save_setting(conn,"printer_settings",settings)
        again = register_printer("Ender5P", "http://old:7125")
        self.assertEqual(first["printer_id"],again["printer_id"])
        with self.assertRaises(RegistrationConflict):
            register_printer("Ender5P","http://new:7125")
        register_printer("Ender5P","http://new:7125",relocate=True)
        with db.connect_db() as conn:
            self.assertEqual(conn.execute("SELECT printer_id FROM jobs").fetchone()[0],first["printer_id"])
            self.assertEqual(_load_setting(conn,"printer_settings")["Ender5P"]["rate_per_hour"],9)

    def test_clone_location_conflict_does_not_split_or_merge_history(self):
        register_printer("Ender5P","http://host:7125",metadata={"host":"pi1","cfg_dir":"/home/pi/printer_data/config"})
        with self.assertRaises(RegistrationConflict):
            register_printer("Ender5P","http://host:7125",metadata={"host":"pi2","cfg_dir":"/home/pi/printer_data/config"})
        register_printer("MercuryTwo","http://host:7129",metadata={"host":"pi2","cfg_dir":"/home/pi/printer_data/config"})
        self.assertEqual(len(registered_clients()),2)

    def test_missing_installer_state_recovered_from_sql(self):
        from installer import utils
        register_printer("SV08","http://host:7125")
        with SqlOnlyFilesystemMonitor():
            utils.save_state("master_url","http://master:6060")
            clients = utils.get_client_registry()
        self.assertEqual(clients[0]["printer_name"],"SV08")
        self.assertTrue(clients[0]["metadata_recovered"])

    def test_invalid_existing_price_rolls_back_new_identity(self):
        from core.printer_lifecycle import _load_setting, _save_setting
        register_printer("Existing","http://host:7125")
        with db.connect_db() as conn:
            settings = _load_setting(conn,"printer_settings")
            settings["Existing"]["rate_per_hour"] = -3
            _save_setting(conn,"printer_settings",settings)
        with self.assertRaises(ValueError):
            register_printer("New","http://host:7129")
        self.assertEqual([c["printer_name"] for c in registered_clients()],["Existing"])

    def test_retirement_requires_explicit_reactivation(self):
        from core.printer_lifecycle import delete_printer
        register_printer("SV08","http://host:7125")
        delete_printer("SV08")
        with self.assertRaises(RegistrationConflict):
            register_printer("SV08","http://host:7125")
        register_printer("SV08","http://host:7125",reactivate=True)
        self.assertFalse(registered_clients()[0]["hidden"])

    def test_api_auth_validation_and_sql_dashboard_classification(self):
        import app
        with patch.object(app,"API_KEY","test-secret"):
            c=app.app.test_client()
            self.assertEqual(c.get('/api/installer/registration').status_code,403)
            self.assertEqual(c.get('/api/installer/registration',headers={"X-API-Key":"test-secret","Content-Type":"application/json"}).status_code,200)
            self.assertEqual(c.post('/job-start',json=[],headers={"X-API-Key":"test-secret"}).status_code,400)
            r=c.post('/api/installer/registration',json={"printer_name":"Fresh","moonraker_url":"http://host:7129"},headers={"X-API-Key":"test-secret"})
            self.assertEqual(r.status_code,200,r.get_json())
            self.assertTrue(r.get_json()["readiness"]["ready"])
            from core.pricing import get_configured_printers, get_discovered_printers
            self.assertIn("Fresh",get_configured_printers())
            self.assertNotIn("Fresh",get_discovered_printers())

    def test_two_ports_and_external_id_conflicts_remain_independent(self):
        register_printer("One","http://host:7127",external_id="unique-one")
        register_printer("Two","http://host:7129")
        with self.assertRaises(RegistrationConflict):
            register_printer("Two","http://host:7129",external_id="unique-one")
        self.assertEqual(len(registered_clients()),2)

    def test_live_state_survives_reload_without_json(self):
        from core import live
        old=live._jobs
        self.addCleanup(setattr,live,"_jobs",old)
        live._jobs={}
        register_printer("One","http://host:7127")
        with SqlOnlyFilesystemMonitor():
            live.start_job("One","part.gcode",start_time=1234)
            live.pause_job("One")
            live._jobs={}
            live._load_state()
            self.assertEqual(live._jobs["One"]["start_time"],1234)
            self.assertEqual(live._jobs["One"]["status"],"paused")

    def test_cancellation_failure_keeps_active_job_recoverable(self):
        import app
        from core import live
        old=live._jobs
        self.addCleanup(setattr,live,"_jobs",old)
        live._jobs={}
        register_printer("One","http://127.0.0.1:1")
        live.start_job("One","part.gcode",start_time=1234)
        with patch.object(app,"API_KEY","test-secret"),patch.object(app.storage_backend,"write_job",side_effect=RuntimeError("db unavailable")):
            with patch.dict(app.app.config,{"PROPAGATE_EXCEPTIONS":False}):
                response=app.app.test_client().post('/job-cancel',json={"printer_name":"One"},headers={"X-API-Key":"test-secret"})
        self.assertEqual(response.status_code,500)
        self.assertIn("One",live._jobs)

    def test_explicit_active_duration_does_not_subtract_pause_twice(self):
        import app
        from core import live
        old=live._jobs
        self.addCleanup(setattr,live,"_jobs",old)
        register_printer("One","http://127.0.0.1:1")
        live._jobs={"One":{"printer_name":"One","filename":"part.gcode","start_time":1000,"status":"printing","total_paused_duration":600,"pause_count":1}}
        with patch.object(app,"API_KEY","test-secret"),patch.object(app,"find_history_job_for_completion",return_value=(False,"offline",None)):
            response=app.app.test_client().post('/log-print',json={"printer":"One","filename":"part.gcode","duration_seconds":3600,"filament_mm":0,"timestamp":5200,"duration_basis":"active"},headers={"X-API-Key":"test-secret"})
        self.assertEqual(response.status_code,200)
        with db.connect_db() as conn:
            row=conn.execute("SELECT duration_seconds,paused_seconds_total,time_cost FROM jobs").fetchone()
        self.assertEqual(row[0],4200)
        self.assertEqual(row[1],600)
        self.assertAlmostEqual(row[2],1)


class DiscoveryTests(unittest.TestCase):
    def test_active_chain_excludes_alternatives_and_backups(self):
        from installer.discovery import active_config_files, discover_local
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'printer_mercurytwo_data/config'
            root.mkdir(parents=True)
            (root/'printer.cfg').write_text('[include macro.cfg]\n')
            (root/'macro.cfg').write_text('[include print_start_manual.cfg]\n')
            (root/'print_start_manual.cfg').write_text('[gcode_macro PRINT_START]\ngcode:\n    G28\n')
            (root/'print_start_auto.cfg').write_text('[gcode_macro PRINT_START]\ngcode:\n    G28\n')
            files,errors=active_config_files(root)
            self.assertFalse(errors)
            self.assertNotIn(root/'print_start_auto.cfg',files)
            candidates=discover_local(tmp,instances=[{"kind":"klipper","config":str(root/'printer.cfg'),"socket":"/tmp/two.sock"}])
            self.assertTrue(candidates[0]['active_process'])
            self.assertEqual(candidates[0]['config_dir'],str(root))

    def test_include_cycles_and_missing_files_are_reported(self):
        from installer.discovery import active_config_files
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'printer.cfg';p.write_text('[include printer.cfg]\n[include missing.cfg]\n')
            _,errors=active_config_files(tmp)
            self.assertEqual(len(errors),2)

    def test_endpoint_dedup_preserves_separate_instances(self):
        from installer.discovery import discover_endpoints
        def probe(url):
            port=url.rsplit(':',1)[1]
            return dict(url=url,config_dir='/printer'+port,connected=True,identity=('mac','moonraker'+port))
        with patch('installer.discovery.probe_endpoint',side_effect=probe):
            endpoints=discover_endpoints([7127,7129,None],hosts=['192.168.2.55','127.0.0.1','localhost'])
        self.assertEqual(len(endpoints),2)
        self.assertTrue(all(len(e['aliases'])==3 for e in endpoints))
        self.assertTrue(all(e['url'].startswith('http://192.168.2.55') for e in endpoints))

    def test_wrong_moonraker_root_is_rejected_before_writes(self):
        from installer.workflow import repair_client
        with tempfile.TemporaryDirectory() as tmp, patch('installer.workflow.master_request',return_value={"backend":"sql"}),patch('installer.workflow.probe_endpoint',return_value={"config_dir":"/other-printer"}):
            with self.assertRaisesRegex(ValueError,"different config"):
                repair_client(tmp,'Printer','http://host:7127','http://master:6060','secret',apply=True)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_macro_preview_and_repeated_apply_preserve_custom_logic(self):
        from installer.macro_plan import macro_plan
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'printer.cfg').write_text('[include macro.cfg]\n')
            body='[gcode_macro PRINT_START]\ngcode:\n    G28\n    M104 S200\n'
            (root/'macro.cfg').write_text(body)
            preview=macro_plan(tmp,'macro.cfg','PRINT_START','start')
            self.assertIn('+    KCD_JOB_START',preview['diff'])
            self.assertEqual((root/'macro.cfg').read_text(),body)
            macro_plan(tmp,'macro.cfg','PRINT_START','start',apply=True)
            macro_plan(tmp,'macro.cfg','PRINT_START','start',apply=True)
            after=(root/'macro.cfg').read_text()
            self.assertEqual(after.count('    KCD_JOB_START'),1)
            self.assertIn('    G28\n    M104 S200',after)
            self.assertTrue(list(root.glob('macro.cfg.kcd-backup-*')))

    def test_inactive_macro_cannot_be_patched(self):
        from installer.macro_plan import macro_plan
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'printer.cfg').write_text('[include manual.cfg]\n')
            for file in ['manual.cfg','auto.cfg']:
                (root/file).write_text('[gcode_macro PRINT_START]\ngcode:\n    G28\n')
            with self.assertRaisesRegex(ValueError,'active user macro'):
                macro_plan(tmp,'auto.cfg','PRINT_START','start',apply=True)

    def test_shared_pause_macro_gets_guarded_hook(self):
        from installer.macro_plan import macro_plan
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'printer.cfg';p.write_text('[gcode_macro PAUSE]\nrename_existing: PAUSE_BASE\ngcode:\n    PAUSE_BASE\n')
            result=macro_plan(tmp,'printer.cfg','PAUSE','pause',apply=True)
            text=p.read_text()
            self.assertIn("{% if 'gcode_macro KCD_JOB_PAUSE' in printer %}",text)
            self.assertIn('    PAUSE_BASE',text)
            self.assertIn('{% endif %}\n',text)
            self.assertLess(text.index('gcode:'),text.index("{% if 'gcode_macro KCD_JOB_PAUSE'"))
            self.assertFalse(macro_plan(tmp,'printer.cfg','PAUSE','pause',apply=True)['applied'])

    def test_include_overrides_select_effective_definition_in_linear_order(self):
        from installer.discovery import effective_macro_sources
        from installer.macro_plan import macro_plan
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'printer.cfg').write_text('[include mainsail.cfg]\n[include macro.cfg]\n')
            (root/'mainsail.cfg').write_text('[gcode_macro PAUSE]\ngcode:\n    PAUSE_BASE\n')
            (root/'macro.cfg').write_text('[gcode_macro PAUSE]\ngcode:\n    CUSTOM_PAUSE\n')
            active,defs=effective_macro_sources(tmp)
            self.assertEqual(active['PAUSE']['file'],str(root/'macro.cfg'))
            with self.assertRaises(ValueError):macro_plan(tmp,'mainsail.cfg','PAUSE','pause',apply=True)
            macro_plan(tmp,'macro.cfg','PAUSE','pause',apply=True)
            self.assertNotIn('KCD_JOB_PAUSE',(root/'mainsail.cfg').read_text())
            self.assertIn('CUSTOM_PAUSE',(root/'macro.cfg').read_text())

    def test_old_managed_config_gains_only_missing_helper_definitions(self):
        from installer.workflow import client_plan
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'printer.cfg').write_text('[include print_cost.cfg]\n')
            old='[gcode_macro _KCD_VARS]\nvariable_printer_name: "One"\ngcode:\n    RESPOND MSG="custom"\n'
            (root/'print_cost.cfg').write_text(old)
            plan=client_plan(tmp,'One','http://master:6060','fake-key')
            after=plan[root/'print_cost.cfg'][0]
            self.assertTrue(after.startswith(old))
            self.assertIn('[gcode_macro KCD_JOB_PAUSE]',after)
            self.assertEqual(after.count('[gcode_macro _KCD_VARS]'),1)

    def test_secret_corruption_is_not_silently_replaced(self):
        from core.storage import ensure_api_key
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"KCD_API_KEY":""}):
            p=Path(tmp)/'secret.json';p.write_text('broken')
            self.assertIsNone(ensure_api_key(str(p),tmp))
            self.assertEqual(p.read_text(),'broken')

    def test_rotation_accepts_only_unexpired_overlap(self):
        from core.auth import valid_api_key
        import time
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"KCD_API_KEY":""}):
            p=Path(tmp)/'secret.json'
            p.write_text(json.dumps({"previous_api_keys":[{"key":"old","expires_at":time.time()+30}]}))
            with patch('core.auth.SECRET_FILE',str(p)):
                self.assertTrue(valid_api_key('old','new'))
                self.assertFalse(valid_api_key('wrong','new'))
                self.assertFalse(valid_api_key('é','new'))
                p.write_text(json.dumps({"previous_api_keys":[{"key":"old","expires_at":time.time()-30}]}))
                self.assertFalse(valid_api_key('old','new'))

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class InstallerAcceptanceTests(unittest.TestCase):
    def test_fresh_legacy_settings_do_not_register_examples(self):
        from core import config,storage
        import json
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KCD_STORAGE_BACKEND':'csv'}),patch.object(config,'DATA_DIR',tmp):
            settings=Path(tmp)/'settings.json'
            for create in (lambda:storage.ensure_runtime_files(str(settings),str(Path(tmp)/'history.csv')),lambda:storage.ensure_settings_exists(str(settings),config.DEFAULT_PRICING)):
                create()
                self.assertEqual(json.loads(settings.read_text()),{})
                settings.unlink()
            settings.write_text('{"Existing":{"rate_per_hour":7}}')
            storage.ensure_settings_exists(str(settings),config.DEFAULT_PRICING)
            self.assertEqual(json.loads(settings.read_text())['Existing']['rate_per_hour'],7)

    def test_client_credential_is_bound_to_selected_master(self):
        from installer import utils
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True),patch.object(utils,'load_state',return_value=''),patch.object(utils,'_load_secret_api_key') as unrelated:
            (Path(tmp)/'kcd_job_start.sh').write_text("MASTER_URL=http://master:6060\nAPI_KEY='private fixture key'\n")
            self.assertEqual(utils._client_api_key('http://master:6060/',tmp),'private fixture key')
            self.assertEqual(utils._client_api_key('http://other:6060',tmp),'')
            self.assertEqual(utils._client_api_key('http://other:6060'),'')
            unrelated.assert_not_called()

    def test_legacy_registry_relocation_replaces_location(self):
        from installer import utils
        old=[{'printer_name':'One','type':'local'},{'printer_name':'Two','type':'local'}]
        with patch.object(utils,'get_client_registry',return_value=old),patch.object(utils,'_set_client_registry') as save:
            entry={'printer_name':'One','type':'remote','host':'fixture'}
            utils.register_client(entry)
            save.assert_called_once_with([entry,old[1]])

    def test_unhealthy_master_cannot_report_success(self):
        from installer import utils
        with tempfile.TemporaryDirectory() as tmp,patch.object(utils,'DATA_DIR',tmp),patch.dict(os.environ,{'KCD_API_KEY':'fixture-master-key'}),patch.object(utils,'_run_cmd',return_value=0),patch.object(utils,'_write_systemd_service',return_value='/fixture/service'),patch.object(utils,'_service_is_active',return_value='failed'),patch.object(utils,'_check_port_open',return_value=False),patch.object(utils,'_check_url',return_value=(False,'fixture unavailable')),patch('time.sleep'):
            self.assertFalse(utils._perform_master_install(12345,'http://localhost:12345','fixture',storage_backend='sql',reports_backend='sql'))
    def test_discovery_does_not_initialize_master_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'untouched';cfg=Path(tmp)/'config';cfg.mkdir();(cfg/'printer.cfg').write_text('')
            env={**os.environ,'KCD_DATA_DIR':str(data),'KCD_STORAGE_BACKEND':'sql','KCD_INSTALLER_SKIP_DOCKER':'1'}
            env.pop('KCD_API_KEY',None)
            result=subprocess.run([sys.executable,'install.py','discover','--config-dir',str(cfg)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(data.exists(),'Discovery created runtime state or credentials')

    def test_update_keeps_the_selected_identity(self):
        from installer import utils
        with patch.object(utils,'install_client_local') as local,patch.object(utils,'install_client_remote') as remote:
            utils.update_client_local('Ender5P');utils.update_client_remote('MercuryTwo')
            local.assert_called_once_with(printer_name='Ender5P')
            remote.assert_called_once_with(printer_name='MercuryTwo')

    def test_custom_directory_shell_commands_are_quoted(self):
        from installer.utils import _render_print_cost_cfg
        import shlex
        root='/tmp/klipper printer/config'
        cfg=_render_print_cost_cfg(root,'Fixture')
        commands=[line.split(':',1)[1].strip() for line in cfg.splitlines() if line.startswith('command:')]
        self.assertEqual(len(commands),5)
        self.assertTrue(all(shlex.split(command)[1].startswith(root+'/') for command in commands))

    @unittest.skipUnless(os.name=='posix','POSIX symlink fixture')
    def test_managed_symlink_escape_is_rejected_before_registration(self):
        from installer.workflow import client_plan
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'config';root.mkdir();(root/'printer.cfg').write_text('')
            outside=Path(tmp)/'outside';outside.write_text('preserve')
            (root/'kcd_job_start.sh').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'escapes'):
                client_plan(root,'Fixture','http://master:5000','fixture-key')
            self.assertEqual(outside.read_text(),'preserve')

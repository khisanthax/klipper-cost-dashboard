"""Exercise the real browser installer and SQL Master against disposable Moonraker."""

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "browser-artifacts" / "web-admin"


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Moonraker(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/server/info":
            result = {"klippy_connected": True}
        elif self.path == "/machine/system_info":
            result = {"system_info": {}}
        elif self.path == "/server/files/roots":
            result = [{"name": "config", "path": str(self.server.config_dir)}]
        elif self.path == "/printer/objects/query?print_stats":
            result = {"status": {"print_stats": {"state": self.server.print_state}}}
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"result": result}).encode())


def await_http(url, process, expected=200):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Fixture process exited during startup")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == expected:
                    return
        except HTTPError as error:
            if error.code == expected:
                return
        except URLError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Fixture HTTP readiness timed out")


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kcd-web-admin-") as directory:
        root = Path(directory)
        config = root / "config"
        config.mkdir()
        printer_cfg = config / "printer.cfg"
        original = "# Disposable printer configuration\n"
        printer_cfg.write_text(original, encoding="utf-8")
        moon = ThreadingHTTPServer(("127.0.0.1", 0), Moonraker)
        moon.config_dir = config.resolve()
        moon.print_state = "standby"
        threading.Thread(target=moon.serve_forever, daemon=True).start()
        moon_url = f"http://127.0.0.1:{moon.server_port}"
        master_port, admin_port = available_port(), available_port()
        master_url, admin_url = f"http://127.0.0.1:{master_port}", f"http://127.0.0.1:{admin_port}"
        data = root / "master-data"
        key = "browser-fixture-master-key"
        password = "separate-browser-fixture-admin-password"
        master_env = dict(os.environ, KCD_API_KEY=key, KCD_STORAGE_BACKEND="sql", KCD_DATA_DIR=str(data))
        targets = root / "targets.json"
        targets.write_text(json.dumps({"master": master_url, "targets": {"local": {
            "name": "WebFixture", "config_dir": str(config), "moonraker": moon_url,
        }}}), encoding="utf-8")
        processes = []
        try:
            subprocess.run([
                sys.executable, "-c",
                "from core.registration import register_printer; import sys; register_printer('Existing', sys.argv[1])",
                "http://127.0.0.1:1",
            ], cwd=ROOT, env=master_env, check=True)
            processes.append(subprocess.Popen(
                [sys.executable, "-m", "kcd.server", "--port", str(master_port)], cwd=ROOT, env=master_env,
            ))
            await_http(master_url + "/health", processes[0])
            admin_env = dict(master_env, KCD_DATA_DIR=str(root / "admin-data"), KCD_WEB_ADMIN_PASSWORD=password)
            processes.append(subprocess.Popen(
                [sys.executable, "-m", "installer.web_admin", "--targets", str(targets), "--port", str(admin_port),
                 "--backup-data", str(data)],
                cwd=ROOT, env=admin_env,
            ))
            await_http(admin_url, processes[1], expected=401)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(http_credentials={"username": "admin", "password": password})
                context.tracing.start(screenshots=True, snapshots=True)
                page = context.new_page()
                page.set_default_timeout(45000)
                try:
                    for width in (390, 768, 1440):
                        page.set_viewport_size({"width": width, "height": 900})
                        page.goto(admin_url)
                        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                        page.screenshot(path=str(ARTIFACTS / f"installer-{width}.png"))
                    preview = page.get_by_role("button", name="Preview installation or repair")
                    preview.focus()
                    preview.press("Enter")
                    page.get_by_role("heading", name="Review WebFixture").wait_for()
                    assert key not in page.content()
                    assert printer_cfg.read_text(encoding="utf-8") == original
                    moon.print_state = "printing"
                    page.get_by_role("button", name="Apply reviewed installation").click()
                    page.get_by_role("alert").wait_for()
                    assert not (config / "print_cost.cfg").exists()
                    moon.print_state = "standby"
                    preview.click()
                    page.get_by_role("button", name="Apply reviewed installation").click()
                    page.get_by_role("heading", name="Installation verified for WebFixture").wait_for()
                    assert "[include print_cost.cfg]" in printer_cfg.read_text(encoding="utf-8")
                    assert (config / "kcd_job_cancel.sh").is_file()
                    page.get_by_role("link", name="Verify Master and registered clients").click()
                    page.get_by_text("WebFixture", exact=True).wait_for()
                    with closing(sqlite3.connect(data / "kcd.db")) as conn:
                        assert conn.execute("SELECT count(*) FROM printers WHERE name='WebFixture'").fetchone()[0] == 1
                    page.get_by_role("button", name="Create backup").click()
                    page.get_by_role("status").filter(has_text="Backup created").wait_for()
                    archives = list((data / "backups").glob("kcd_backup_*.tar.gz"))
                    assert len(archives) == 1
                    with tarfile.open(archives[0]) as archive:
                        restored = root / "restored.db"
                        with archive.extractfile("data/kcd.db") as database:
                            restored.write_bytes(database.read())
                    with closing(sqlite3.connect(restored)) as conn:
                        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                        assert conn.execute("SELECT count(*) FROM printers WHERE name='WebFixture'").fetchone()[0] == 1
                    print("Web admin browser passed: responsive, keyboard, read-only preview, active rejection and SQL registration")
                except Exception:
                    print(page.locator("body").inner_text(timeout=2000))
                    page.screenshot(path=str(ARTIFACTS / "failure.png"))
                    raise
                finally:
                    context.tracing.stop(path=str(ARTIFACTS / "trace.zip"))
                    browser.close()
        finally:
            for process in reversed(processes):
                process.terminate()
                process.wait(timeout=15)
            moon.shutdown()
            moon.server_close()


if __name__ == "__main__":
    main()

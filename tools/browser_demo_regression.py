"""Exercise the isolated public demo with two disposable browser contexts."""
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "browser-artifacts" / "demo"


def wait_until_ready(server, base_url):
    for _ in range(160):
        if server.poll() is not None:
            raise RuntimeError("Demo server exited before startup")
        try:
            with urlopen(base_url + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("Demo server did not become ready")


def exercise(base_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        with browser.new_context(viewport={"width": 1280, "height": 800}) as first, \
             browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True) as second:
            first.tracing.start(screenshots=True, snapshots=True)
            try:
                desktop = first.new_page()
                mobile = second.new_page()
                for page in (desktop, mobile):
                    page.set_default_timeout(30_000)
                    page.goto(base_url + "/")
                    expect(page.get_by_role("heading", name="Explore Klipper Cost Dashboard")).to_be_visible()
                    button = page.get_by_role("button", name="Start demo")
                    button.focus()
                    button.press("Enter")
                    expect(page.get_by_label("Demo workspace")).to_be_visible()

                telemetry = first.request.get(base_url + "/api/printer-status")
                observations = list(telemetry.json()["printers"].values())
                assert len(observations) == 3
                assert all(item["simulated"] and not item["identity_verified"] for item in observations)
                assert any(item["state"] == "printing" for item in observations)

                for path in ("/api/installer/registration", "/download-csv", "/printer-diagnostics",
                             "/jobs/fake/thumbnail", "/thumbnails/fake"):
                    assert first.request.get(base_url + path).status == 403, path
                assert first.request.post(base_url + "/job-cancel", data={"printer_name": "Demo"}).status == 403

                desktop.goto(base_url + "/projects")
                create = desktop.locator("form").filter(has=desktop.locator('input[value="create_project"]'))
                create.get_by_label("Project name", exact=True).fill("Visitor A test project")
                create.get_by_role("button", name="Create", exact=True).click()
                expect(desktop.get_by_text("Visitor A test project", exact=True).first).to_be_visible()
                mobile.goto(base_url + "/projects")
                expect(mobile.get_by_text("Visitor A test project", exact=True)).to_have_count(0)
                mobile.screenshot(path=str(ARTIFACTS / "visitor-b-mobile.png"), full_page=True)

                desktop.on("dialog", lambda dialog: dialog.accept())
                desktop.get_by_role("button", name="Reset demo").click()
                expect(desktop.get_by_label("Demo workspace")).to_be_visible()
                desktop.goto(base_url + "/projects")
                expect(desktop.get_by_text("Visitor A test project", exact=True)).to_have_count(0)
                expect(desktop.get_by_text("Cosplay Helmet", exact=True).first).to_be_visible()
                desktop.screenshot(path=str(ARTIFACTS / "visitor-a-reset.png"), full_page=True)
            finally:
                first.tracing.stop(path=str(ARTIFACTS / "trace.zip"))
        browser.close()


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="kcd-demo-browser-") as directory:
        scratch = Path(directory)
        demo = scratch / "demo"
        demo.mkdir()
        env = {
            **os.environ,
            "KCD_RUNTIME_MODE": "demo",
            "KCD_STORAGE_BACKEND": "sql",
            "KCD_REPORTS_BACKEND": "sql",
            "KCD_DEMO_DATA_ROOT": str(demo),
            "KCD_DEMO_COOKIE_SECURE": "0",
            "KCD_DATA_DIR": str(scratch / "normal-data"),
            "KCD_API_KEY": "",
        }
        subprocess.run(
            [sys.executable, "tools/create_demo_seed.py", "--data-root", str(demo)],
            check=True, timeout=90, env=env, cwd=ROOT,
        )
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        with (ARTIFACTS / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "kcd.server", "--port", str(port), "--host", "127.0.0.1"],
                env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                wait_until_ready(server, base_url)
                exercise(base_url)
                assert not (scratch / "normal-data").exists(), "Demo accessed normal storage"
            finally:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
    print("Demo browser isolation, reset, simulation and restricted routes passed")


if __name__ == "__main__":
    main()

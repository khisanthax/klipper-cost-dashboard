"""Real Reports forms and monthly values on the disposable dashboard fixture."""
from contextlib import closing
from pathlib import Path
import sqlite3


def exercise_reports(page, base, data, artifacts):
    database = Path(data) / "kcd.db"
    fixtures = [
        ("sep", "Idle", "2019-09-15T12:00:00+00:00", "completed", 1, 10, 30, "estimated", 1, 2, 3),
        ("cancel", "Idle", "2019-10-01T12:00:00+00:00", "canceled", .5, 20, 60, "estimated", 0, .9, .9),
        ("end-day", "Idle", "2019-10-31T23:59:59+00:00", "completed", 2, 5, 10, "recorded", 2, .1, 2.1),
        ("failed", "Idle", "2019-10-20T12:00:00+00:00", "failed", 1, None, None, None, 3, None, None),
        ("unknown", "Idle", "2019-10-21T12:00:00+00:00", "printing", None, None, None, None, None, None, None),
        ("other", "Completed", "2019-10-15T12:00:00+00:00", "completed", 100, 100, 300, "estimated", 100, 100, 200),
    ]
    try:
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute('PRAGMA foreign_keys=ON')
            for uid, printer, ended, status, hours, meters, grams, kind, time_cost, material_cost, total in fixtures:
                printer_id = conn.execute("SELECT id FROM printers WHERE name=?", (printer,)).fetchone()[0]
                conn.execute("""INSERT INTO jobs
                    (job_uid,printer_id,filename,status,ended_at,created_at,updated_at,duration_hours,
                     filament_meters,filament_grams,filament_weight_kind,time_cost,material_cost,total_cost)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("report-browser-"+uid, printer_id, "report-2019.gcode", status, ended, ended, ended,
                     hours, meters, grams, kind, time_cost, material_cost, total))
        page.goto(base + "/reports")
        filters = page.locator("form").filter(has=page.locator("select[name=printer]"))
        filters.get_by_label("Printer", exact=True).select_option("Idle")
        filters.get_by_label("Start Date", exact=True).fill("2019-09-01")
        filters.get_by_label("End Date", exact=True).fill("2019-10-31")
        with page.expect_navigation():
            filters.get_by_role("button", name="Apply", exact=True).click()
        assert "printer=Idle" in page.url
        table = page.get_by_role("table", name="Monthly print outcomes")
        october = table.get_by_role("row").filter(has=page.get_by_role("rowheader", name="2019-10", exact=False))
        cells = october.locator("th,td")
        assert cells.count() == 12
        assert "4" == cells.nth(1).inner_text().splitlines()[0]
        assert [cells.nth(index).inner_text() for index in (2, 3, 4, 5)] == ["1", "1", "1", "1"]
        assert "33.3%" in cells.nth(6).inner_text()
        assert "-66.7 pp" in cells.nth(6).inner_text()
        for index, amount in ((7, "3.50"), (8, "70.00 g"), (9, "$5.00"), (10, "$1.00"), (11, "$3.00")):
            assert cells.nth(index).inner_text().startswith(amount), (index, cells.nth(index).inner_text())
        assert "2 missing" in cells.nth(11).inner_text()
        assert "Change unavailable" in cells.nth(11).inner_text()
        detail=cells.nth(8).locator('[data-filament-details]')
        detail.locator('summary').click()
        assert '25' in detail.inner_text() and ' m' in detail.inner_text()
        detail.locator('summary').click()
        recent = page.locator("#report-recent-jobs")
        assert recent.get_by_role("cell", name="Unpriced", exact=True).count() == 2
        assert "Completed" not in recent.inner_text()
        for width, height, zoom, label in ((1440, 1000, 1, "desktop"), (768, 1024, 1, "tablet"), (390, 844, 1, "mobile"), (1440, 1000, 2, "zoom200")):
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("z=>document.body.style.zoom=z", zoom)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
            page.screenshot(path=str(artifacts / ("reports-metrics-"+label+".png")), full_page=True)
        page.evaluate("document.body.style.zoom=1")
        page.set_viewport_size({"width": 390, "height": 844})
        scroll = table.locator("..")
        scroll.focus(); page.keyboard.press("ArrowRight")
        page.wait_for_function("document.querySelector('table caption').parentElement.parentElement.scrollLeft > 0")
        # Cache must refresh after a historical edit that deliberately preserves updated_at.
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute("UPDATE jobs SET total_cost=4.1 WHERE job_uid='report-browser-end-day'")
        page.reload()
        table = page.get_by_role("table", name="Monthly print outcomes")
        october = table.get_by_role("row").filter(has=page.get_by_role("rowheader", name="2019-10", exact=False))
        assert october.locator("td").last.inner_text().startswith("$5.00")
    finally:
        page.goto("about:blank")
        page.evaluate("document.body.style.zoom=1")
        page.set_viewport_size({"width": 1440, "height": 1000})
        with closing(sqlite3.connect(database)) as conn, conn:
            # Match production connections: fixture cleanup must cascade image
            # references, including callbacks that finished during navigation.
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute("DELETE FROM jobs WHERE job_uid LIKE 'report-browser-%'")
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()

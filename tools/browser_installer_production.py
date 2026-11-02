"""Explicit read-only production reference checks; never submit forms."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',required=True);args=parser.parse_args()
    artifacts=Path('browser-artifacts/installer-production');artifacts.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch();context=browser.new_context();context.tracing.start(screenshots=True,snapshots=True)
        page=context.new_page()
        # Enforce the read-only boundary at the browser network layer too.
        context.route('**/*',lambda route:route.continue_() if route.request.method in ('GET','HEAD','OPTIONS') else route.abort())
        try:
            for path,label in [('/settings/printers','printers'),('/printer-diagnostics','diagnostics')]:
                response=page.goto(args.base_url.rstrip('/')+path,wait_until='domcontentloaded');assert response.status==200
                text=page.locator('body').inner_text()
                for name in ('Ender5P','MercuryTwo','SV07','SV08'):assert name in text,(label,name)
                page.screenshot(path=str(artifacts/(label+'.png')),full_page=True)
            print('Production read-only browser reference passed: four identities present in Settings and Diagnostics; browser writes blocked')
        finally:
            context.tracing.stop(path=str(artifacts/'trace.zip'));browser.close()


if __name__=='__main__':main()

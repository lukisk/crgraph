"""Regenerates docs/images from a demo report (see CONTRIBUTING.md). Needs Google Chrome.

    python docs/screenshots.py <report.html> <out-dir>
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

report, out = Path(sys.argv[1]).resolve().as_uri(), sys.argv[2]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1440, "height": 860}, device_scale_factor=2, color_scheme="light")
    pg.goto(report); pg.wait_for_timeout(1500)
    pg.click('button[data-depth="file"]'); pg.wait_for_timeout(1200)
    pg.mouse.click(650, 518); pg.wait_for_timeout(1200)  # the QuickBuy.kt box
    pg.screenshot(path=f"{out}/report.png")
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.click("#btn-story"); pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{out}/story.png")
    pg.click("#btn-story"); pg.wait_for_timeout(800)
    pg.emulate_media(color_scheme="dark"); pg.reload(); pg.wait_for_timeout(1500)
    pg.set_viewport_size({"width": 1440, "height": 760})
    pg.click('button[data-depth="module"]'); pg.wait_for_timeout(1000)
    pg.screenshot(path=f"{out}/report-dark.png")
    b.close()

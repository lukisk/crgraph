"""Regenerates docs/images from the demo report with its review (see CONTRIBUTING.md). Needs Google Chrome.

    python docs/screenshots.py <report.html> <out-dir>
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

report, out = Path(sys.argv[1]).resolve().as_uri(), sys.argv[2]
READY = "window.__crgraph && window.__crgraph.cy().nodes().length > 0"
SELECT_QUICKBUY = "window.__crgraph.cy().nodes().filter(n => n.id().endsWith('/QuickBuy.kt')).emit('tap')"
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1440, "height": 860}, device_scale_factor=2, color_scheme="light")
    pg.goto(report); pg.wait_for_function(READY); pg.wait_for_timeout(800)
    pg.click('button[data-depth="file"]'); pg.wait_for_timeout(1200)
    pg.evaluate(SELECT_QUICKBUY); pg.wait_for_timeout(800)
    pg.screenshot(path=f"{out}/report.png")
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.click("#btn-story"); pg.wait_for_timeout(1500)
    pg.evaluate("window.scrollTo(0, 0); document.getElementById('story').scrollTop = 0")
    pg.locator("#story section").nth(1).scroll_into_view_if_needed()
    pg.screenshot(path=f"{out}/story.png")
    pg.click("#btn-review"); pg.wait_for_timeout(600)  # the ✦ verdict button: the review in the story view
    box = pg.locator("#story-review").bounding_box()
    pg.screenshot(path=f"{out}/review.png", clip={"x": box["x"] - 24, "y": box["y"] - 16,
                                                  "width": box["width"] + 48, "height": box["height"] + 24})
    pg.click("#btn-story"); pg.wait_for_timeout(800)
    pg.emulate_media(color_scheme="dark"); pg.reload(); pg.wait_for_function(READY); pg.wait_for_timeout(800)
    pg.set_viewport_size({"width": 1440, "height": 760})
    pg.click('button[data-depth="module"]'); pg.wait_for_timeout(1000)
    pg.screenshot(path=f"{out}/report-dark.png")
    b.close()

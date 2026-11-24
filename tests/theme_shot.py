import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = "file://" + os.path.join(HERE, "theme-preview.html")
OUT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "downloads"))
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1260, "height": 900}, device_scale_factor=2)
    pg.goto(PAGE)
    pg.wait_for_timeout(700)
    frames = pg.query_selector_all(".frame")
    print("frames:", len(frames))
    for f in frames:
        key = f.get_attribute("data-key")
        path = os.path.join(OUT, key + ".png")
        f.screenshot(path=path)
        box = f.bounding_box()
        print(key, box, os.path.getsize(path))
    b.close()

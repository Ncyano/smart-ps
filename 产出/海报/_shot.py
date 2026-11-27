"""把 产出/海报/*.html 截成 PNG。用法：python _shot.py [html] [scale]"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
name = sys.argv[1] if len(sys.argv) > 1 else "poster"
scale = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
src = HERE / f"{name}.html"
out = HERE / f"{name}.png"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1080, "height": 1400}, device_scale_factor=scale)
    pg.goto(src.resolve().as_uri(), wait_until="networkidle")
    pg.wait_for_timeout(700)
    pg.screenshot(path=str(out), full_page=True)
    b.close()

from PIL import Image
im = Image.open(out)
print(f"{out.name}  {im.size[0]}x{im.size[1]}  {out.stat().st_size/1024:.0f}KB")

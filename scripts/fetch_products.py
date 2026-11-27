"""按品牌名从 360 图片搜索抓候选商品图，标题命中品牌名 + 四角近白才保留。

用法：python scripts/fetch_products.py "示例品牌" slug 10
"""
import io
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
import numpy as np
from PIL import Image

OUT = Path("产出/海报/products/_cand")
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}


def search(q: str, pages: int = 2):
    items = []
    with httpx.Client(timeout=25, headers=UA, follow_redirects=True) as c:
        for p in range(pages):
            r = c.get(f"https://image.so.com/j?q={quote(q)}&src=srp&sn={p * 30}&pn=30")
            if r.status_code != 200:
                continue
            for it in r.json().get("list", []):
                items.append((it.get("title", ""), it.get("img") or it.get("thumb")))
    return items


def whiteness(im: Image.Image) -> float:
    w, h = im.size
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    bw, bh = max(int(w * 0.10), 4), max(int(h * 0.10), 4)
    corners = [a[:bh, :bw], a[:bh, -bw:], a[-bh:, :bw], a[-bh:, -bw:]]
    return float(np.mean([(c.min(axis=2) > 235).mean() for c in corners]))


def main():
    q, slug = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    OUT.mkdir(parents=True, exist_ok=True)
    items = search(q)
    print(f"{q}: {len(items)} 条结果")
    kept = 0
    with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
        for i, (title, url) in enumerate(items):
            if not url:
                continue
            try:
                r = c.get(url)
                if r.status_code != 200 or len(r.content) < 15000:
                    continue
                im = Image.open(io.BytesIO(r.content))
                im.load()
                if min(im.size) < 240:
                    continue
                im = im.convert("RGB")
                wr = whiteness(im)
                if wr < 0.5:
                    continue
                p = OUT / f"{slug}_{i:02d}_w{int(wr * 100)}.png"
                im.save(p)
                kept += 1
                print(f"  keep {p.name} {im.size} 白底{wr:.2f} | {title[:40]}")
                if kept >= n:
                    break
            except Exception:
                continue
    print(f"保留 {kept} 张")


if __name__ == "__main__":
    main()

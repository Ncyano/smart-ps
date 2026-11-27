# -*- coding: utf-8 -*-
"""06 尺码表：一款一图一表。

上一版是三款数据只配两张（甚至一张）上身图，图和表对不上。
这一版按素材的做法：每个款号配它自己的上身图，紧挨着自己的尺码表。

尺码数据仍取自素材原图，未改动数值。
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "06-尺码表"
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1080, 1350

DATA = [
    {"name": "811 泡芙款", "code": "ZYH811", "colors": "三色可选 · 尺码 S–L",
     "shot": OUT / "_model_811.png", "cap": "短款 · 横向充绒格 · 立领",
     "tags": ["短款", "立领", "通勤"],
     "note": "S–L 逐码 +25g，充绒量随码递增；下摆及腰，适合配高腰裤。",
     "rows": [("S", "230g", "80–110"), ("M", "255g", "110–130"), ("L", "275g", "130–165")]},
    {"name": "338 花苞款", "code": "338", "colors": "六色可选 · 尺码 S/M",
     "shot": OUT / "_model_338.png", "cap": "短款 · 宽充绒格 · 立领",
     "tags": ["短款", "收口下摆", "日常"],
     "note": "只有 S / M 两码，收口下摆更显腰线，内搭厚毛衣建议选 M。",
     "rows": [("S", "150g", "80–125"), ("M", "157g", "125–165")]},
    {"name": "168 经典款", "code": "168", "colors": "九色可选 · 尺码 S–XXL",
     "shot": OUT / "_model_168.png", "cap": "中长款 · 常规充绒格 · 连帽",
     "tags": ["中长款", "连帽", "男女同款"],
     "note": "S–XXL 六个码，衣长盖过臀部，男女同款按胸围选即可。",
     "rows": [("S", "128g", "80–120"), ("M", "134g", "120–130"), ("L", "141g", "130–150"),
              ("XL", "147g", "150–175"), ("XXL", "153g", "175–190")]},
]


def spark(d):
    vals = [(sz, int(g.rstrip("g"))) for sz, g, _ in d["rows"]]
    top = max(v for _, v in vals)
    cols = "".join(
        f"<div class='col'><div class='bar' style='height:{max(8, round(v / top * 52))}px'></div>"
        f"<span>{sz}</span></div>"
        for sz, v in vals
    )
    return f"<div class='spark'>{cols}</div>"


def rows_html(d):
    return "".join(
        f"<tr><td class='sz'>{s}</td><td class='g'>{g}</td><td class='w'>{w}</td></tr>"
        for s, g, w in d["rows"]
    )


# ---------- A：浅色，三行，一行一款 ----------
HTML_A = f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:{W}px;height:{H}px;font-family:"Microsoft YaHei",sans-serif;
  background:#f4f1ea;color:#26221c;padding:38px 44px 24px;display:flex;flex-direction:column;overflow:hidden}}
.kick{{font-size:15px;letter-spacing:5px;color:#a8863f;font-weight:700}}
h1{{font-size:44px;font-weight:800;margin-top:8px;line-height:1.16}}
.sub{{font-size:16px;color:#7d7365;margin-top:8px;letter-spacing:.5px}}
.rows{{margin-top:18px;flex:1;min-height:0;display:flex;flex-direction:column;justify-content:space-between;gap:12px}}
.row{{flex:none;height:356px;display:flex;gap:20px;background:#fff;border-radius:14px;padding:14px;
  border:1px solid #e8e0d0}}
.shot{{width:248px;flex:none;border-radius:10px;overflow:hidden;background:#e9e3d6;position:relative}}
.shot img{{width:100%;height:100%;object-fit:cover;object-position:50% 0%}}
.shot .no{{position:absolute;left:10px;top:10px;background:rgba(20,16,10,.62);color:#fff;
  font-size:12px;letter-spacing:1px;padding:3px 9px;border-radius:99px}}
.info{{flex:1;display:flex;flex-direction:column;justify-content:center;padding:4px 8px 2px 0}}
.bh{{display:flex;align-items:baseline;gap:12px}}
.bh h2{{font-size:25px;font-weight:800;letter-spacing:.5px}}
.bh .code{{font-size:12px;color:#a8863f;letter-spacing:1px}}
.bh .cl{{margin-left:auto;font-size:13px;color:#8d8474}}
.cap{{font-size:13px;color:#9a9081;margin-top:5px}}
table{{width:100%;border-collapse:collapse;table-layout:fixed;margin-top:10px}}
col.a{{width:18%}} col.b{{width:30%}} col.c{{width:52%}}
th{{font-size:13px;color:#9a9081;font-weight:600;text-align:left;padding-bottom:7px}}
th.r{{text-align:right}}
td{{font-size:17px;padding:8px 0;border-top:1px solid #e6dfd1}}
td.sz{{font-weight:800}}
td.w{{text-align:right;color:#5f584c}}
.foot{{margin-top:14px;font-size:11.5px;color:#948b7b;line-height:1.6}}
</style></head><body>
<div class="kick">2026 冬季 · 羽绒服选款</div>
<h1>三款版型，尺码与充绒量对照</h1>
<div class="sub">按体重区间选码 · 偏宽松版型建议选小一码 · 每款对应各自的充绒量</div>
<div class="rows">
{''.join(f'''<div class="row">
  <div class="shot"><img src="{d['shot'].as_uri()}"><span class="no">款号 {d['code']}</span></div>
  <div class="info">
    <div class="bh"><h2>{d['name']}</h2><div class="code">{d['code']}</div><div class="cl">{d['colors']}</div></div>
    <div class="cap">{d['cap']}</div>
    <table><colgroup><col class="a"><col class="b"><col class="c"></colgroup>
      <thead><tr><th>尺码</th><th>充绒量</th><th class="r">建议体重（斤）</th></tr></thead>
      <tbody>{rows_html(d)}</tbody></table>
  </div>
</div>''' for d in DATA)}
</div>
<div class="foot">充绒量为单件实测值 · 建议体重为参考区间 · 以商品详情页为准 · 上身图为示意</div>
</body></html>"""


# ---------- B：深色，三列，一列一款 ----------
HTML_B = f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:{W}px;height:{H}px;font-family:"Microsoft YaHei",sans-serif;background:#101820;
  color:#f2efe8;padding:44px 44px 26px;display:flex;flex-direction:column}}
.kick{{font-size:15px;letter-spacing:6px;color:#c8a96a;font-weight:700}}
h1{{font-size:44px;font-weight:800;margin-top:10px;line-height:1.18;letter-spacing:1px}}
.sub{{margin-top:10px;font-size:16px;color:#9aa7b4}}
.grid{{margin-top:20px;flex:1;display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}
.card{{background:#17212b;border:1px solid rgba(200,169,106,.28);border-radius:14px;
  overflow:hidden;display:flex;flex-direction:column}}
.pic{{position:relative;height:424px;background:#1b2530}}
.pic img{{width:100%;height:100%;object-fit:cover;object-position:50% 8%}}
.pic .no{{position:absolute;left:12px;top:12px;background:rgba(0,0,0,.55);color:#f0d9a8;
  font-size:12px;letter-spacing:1px;padding:3px 9px;border-radius:99px;border:1px solid rgba(240,217,168,.4)}}
.body{{padding:14px 16px 16px;display:flex;flex-direction:column;flex:1}}
.body h2{{font-size:23px;font-weight:800;letter-spacing:.5px}}
.body .cl{{font-size:12.5px;color:#8b98a6;margin-top:4px}}
table{{width:100%;border-collapse:collapse;table-layout:fixed;margin-top:12px}}
col.a{{width:22%}} col.b{{width:30%}} col.c{{width:48%}}
th{{font-size:12.5px;color:#8b98a6;font-weight:600;text-align:left;padding-bottom:8px}}
th.r{{text-align:right}}
td{{font-size:16px;padding:7px 0;border-top:1px solid rgba(255,255,255,.09)}}
td.sz{{font-weight:800;color:#f0d9a8}}
td.w{{text-align:right;color:#d3dbe4}}
.spark{{margin-top:12px;display:flex;gap:6px;align-items:flex-end;height:70px}}
.spark .col{{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:5px}}
.spark .bar{{width:100%;border-radius:3px 3px 0 0;background:linear-gradient(180deg,#f0d9a8,#b4904c)}}
.spark span{{font-size:10.5px;color:#8b98a6;letter-spacing:.5px}}
.note{{margin-top:10px;font-size:13px;color:#9aa7b4;line-height:1.7}}
.tags{{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;padding-top:14px}}
.tags span{{font-size:12.5px;color:#c8a96a;border:1px solid rgba(200,169,106,.42);
  border-radius:99px;padding:4px 11px;letter-spacing:.5px}}
.bar{{margin-top:16px;display:flex;align-items:center;gap:18px;background:#17212b;
  border:1px solid rgba(200,169,106,.24);border-radius:12px;padding:14px 20px}}
.bar b{{font-size:15px;color:#c8a96a;letter-spacing:1px;flex:none}}
.bar span{{font-size:14px;color:#c2ccd6}}
.foot{{margin-top:12px;font-size:11.5px;color:#7b8896;line-height:1.6}}
</style></head><body>
<div class="kick">冬季羽绒 · 一款一表</div>
<h1>三款版型，尺码与充绒量</h1>
<div class="sub">模特身高 165cm / 体重 52kg，日常穿 M 码；版型偏宽松可下选一码</div>
<div class="grid">
{''.join(f'''<div class="card">
  <div class="pic"><img src="{d['shot'].as_uri()}"><span class="no">款号 {d['code']}</span></div>
  <div class="body">
    <h2>{d['name']}</h2><div class="cl">{d['cap']} · {d['colors']}</div>
    <table><colgroup><col class="a"><col class="b"><col class="c"></colgroup>
      <thead><tr><th>尺码</th><th>充绒量</th><th class="r">建议体重（斤）</th></tr></thead>
      <tbody>{rows_html(d)}</tbody></table>
    {spark(d)}
    <div class="note">{d['note']}</div>
    <div class="tags">{''.join(f'<span>{t}</span>' for t in d['tags'])}</div>
  </div>
</div>''' for d in DATA)}
</div>
<div class="bar"><b>怎么选</b><span>① 按平时穿的胸围对尺码　② 体重落在区间内取大不取小　③ 想穿厚内搭，往上加一码</span></div>
<div class="foot">充绒量为单件实测值 · 建议体重为参考区间 · 以商品详情页为准 · 上身图为示意</div>
</body></html>"""


def main():
    (OUT / "_A.html").write_text(HTML_A, encoding="utf-8")
    (OUT / "_B.html").write_text(HTML_B, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, out in (("_A", "1.png"), ("_B", "2.png")):
            pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
            pg.goto((OUT / f"{name}.html").as_uri())
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(OUT / out))
            pg.close()
        b.close()
    print("saved", OUT)


if __name__ == "__main__":
    main()

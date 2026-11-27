# -*- coding: utf-8 -*-
"""02 成分检测海报：AI 产品图 + 真实检测报告图 + HTML 精确排版。

上一版失败原因：只做了抽象表格线与白瓶，既没有产品也没有检测证据。
这一版把素材里的三个必备元素补齐——真实产品包装、纸质报告（带红章）、检测项清单。
"""
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "_assets"
OUT = HERE / "02-成分检测海报"
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1080, 1448

PROD = "_prod_cut.png"
REPORT_SRC = ASSETS / "Close_up_photo_of_an_official__2026-11-26T02-28-54.png"
REPORT = ASSETS / "_report_cut.png"
if not REPORT.exists():
    _im = Image.open(REPORT_SRC).convert("RGB")
    _w, _h = _im.size
    _im.crop((0, 0, _w, int(_h * 0.885))).save(REPORT)


def cut_white(src: Path, dst: Path, thr: int = 238):
    """纯白背景去背（产品图本身就是白底棚拍）。"""
    im = Image.open(src).convert("RGB")
    px = im.load()
    w, h = im.size
    out = Image.new("RGBA", (w, h))
    op = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r > thr and g > thr and b > thr:
                op[x, y] = (r, g, b, 0)
            else:
                # 边缘做一点过渡，避免锯齿
                m = min(r, g, b)
                a = 255 if m < thr - 18 else int(255 * (thr - m) / 18)
                op[x, y] = (r, g, b, max(0, min(255, a)))
    out.save(dst)
    return dst


ITEMS = [
    ("有机磷类", "未检出"), ("拟除虫菊酯", "未检出"), ("重金属", "未检出"), ("微生物", "合格"),
]


def item_row(color: str):
    return "".join(
        f"<div class='it'><div class='dot' style='border-color:{color}'>"
        f"<svg viewBox='0 0 24 24' width='26' height='26' fill='none' stroke='{color}' stroke-width='2'>"
        f"<path d='M5 13l4 4L19 7'/></svg></div>"
        f"<b>{k}</b><span>{v}</span></div>"
        for k, v in ITEMS
    )


HTML_A = f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:{W}px;height:{H}px;font-family:"Microsoft YaHei",sans-serif;
  background:linear-gradient(180deg,#eaf3fb 0%,#f7fbfe 42%,#eef5f4 100%);color:#14304d;
  padding:52px 54px;display:flex;flex-direction:column;position:relative;overflow:hidden}}
.brand{{text-align:center;font-size:30px;font-weight:800;letter-spacing:8px;color:#1b4b7a}}
.brand i{{display:block;font-style:normal;font-size:16px;letter-spacing:5px;color:#7d93a9;
  font-weight:400;margin-top:8px}}
h1{{text-align:center;font-size:66px;font-weight:800;margin-top:22px;letter-spacing:2px;
  line-height:1.1;color:#0f2f4f}}
h1 em{{font-style:normal;color:#0a7d55}}
.sub{{text-align:center;font-size:24px;color:#3f627f;margin-top:14px;letter-spacing:1px}}
.items{{display:flex;justify-content:space-between;margin-top:30px;padding:0 6px}}
.it{{display:flex;flex-direction:column;align-items:center;gap:7px;width:23%}}
.dot{{width:62px;height:62px;border-radius:50%;border:2px solid;display:flex;
  align-items:center;justify-content:center;background:#fff}}
.it b{{font-size:17px;font-weight:700;color:#204a6d}}
.it span{{font-size:15px;color:#0a7d55;font-weight:700;letter-spacing:1px}}
.stage{{position:relative;flex:1;margin-top:14px;display:flex;align-items:center;
  justify-content:center}}
.report{{position:absolute;left:-10px;top:50%;transform:translateY(-50%) rotate(-2.2deg);width:66%;
  border-radius:8px;box-shadow:0 16px 34px rgba(20,48,77,.22)}}
.prod{{position:relative;width:56%;margin-left:auto;margin-right:-6px;z-index:2;
  filter:drop-shadow(0 18px 26px rgba(20,48,77,.26))}}
.badge{{position:absolute;right:20px;top:-6px;z-index:3;background:#fff;border-radius:16px;
  padding:14px 18px;box-shadow:0 8px 20px rgba(20,48,77,.14);text-align:center}}
.badge b{{display:block;font-size:26px;color:#0a7d55;font-weight:800}}
.badge span{{font-size:13px;color:#6b8299}}
.hook{{margin-top:22px;background:#0f2f4f;color:#fff;border-radius:12px;padding:15px 22px;
  font-size:17px;letter-spacing:1px;line-height:1.6}}
.hook b{{color:#8fe3c0}}
</style></head><body>
<div class="brand">VITA NATURALS<i>科学营养 · 每日所需</i></div>
<h1>不含农残 <em>放心喝</em></h1>
<div class="sub">经第三方权威检测 · 多项农药残留未检出</div>
<div class="items">{item_row("#0a7d55")}</div>
<div class="stage">
  <img class="report" src="{REPORT.as_uri()}">
  <img class="prod" src="{(OUT / PROD).as_uri()}">
  <div class="badge"><b>SGS</b><span>检测报告</span></div>
</div>
<div class="hook">检测报告编号 GZF25-0058295-02 · <b>各项指标均符合国家标准</b></div>
</body></html>"""

HTML_B = f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:{W}px;height:{H}px;font-family:"Microsoft YaHei",sans-serif;
  background:linear-gradient(165deg,#fbf7ef 0%,#f3efe4 55%,#e9efe6 100%);color:#2f2a22;
  padding:52px 54px;display:flex;flex-direction:column;position:relative;overflow:hidden}}
.kick{{font-size:19px;letter-spacing:6px;color:#9a7b3f;font-weight:700}}
h1{{font-size:58px;font-weight:800;margin-top:14px;line-height:1.18;letter-spacing:1px}}
h1 em{{font-style:normal;color:#3f6b46}}
.sub{{margin-top:12px;font-size:21px;color:#6f6657;letter-spacing:1px}}
.panel{{margin-top:26px;background:#fff;border-radius:18px;padding:26px 30px;
  box-shadow:0 10px 28px rgba(60,50,30,.10)}}
.panel .ph{{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:800;color:#3f6b46}}
.panel .items{{display:grid;grid-template-columns:1fr 1fr;gap:14px 26px;margin-top:18px}}
.it{{display:flex;align-items:center;gap:10px}}
.dot{{width:38px;height:38px;border-radius:50%;border:2px solid #3f6b46;display:flex;
  align-items:center;justify-content:center;flex:none}}
.it b{{font-size:18px;font-weight:700}}
.it span{{margin-left:auto;font-size:15px;color:#3f6b46;font-weight:700;
  background:#eef5ee;padding:4px 10px;border-radius:99px}}
.stage{{flex:1;position:relative;margin-top:22px}}
.report{{position:absolute;left:-8px;top:50%;transform:translateY(-50%) rotate(1.6deg);width:62%;border-radius:6px;
  box-shadow:0 14px 30px rgba(60,50,30,.20)}}
.prod{{position:absolute;right:-4px;top:50%;transform:translateY(-50%);width:54%;z-index:2;
  filter:drop-shadow(0 16px 22px rgba(60,50,30,.22))}}
.stamp{{position:absolute;right:26px;top:-18px;z-index:3;transform:rotate(-8deg);
  border:6px double #b23a2e;color:#b23a2e;border-radius:50%;width:132px;height:132px;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  font-weight:800;letter-spacing:1px;opacity:.92}}
.stamp b{{font-size:26px}}
.stamp span{{font-size:14px;margin-top:2px}}
.foot{{margin-top:18px;font-size:15px;color:#8c8375;line-height:1.7;letter-spacing:.5px}}
</style></head><body>
<div class="kick">第三方检测 · 成分透明</div>
<h1>每一项都写清楚<br><em>不给宝宝吃农残</em></h1>
<div class="sub">抽检批次可溯源，检测报告随箱可查</div>
<div class="panel">
  <div class="ph"><svg viewBox="0 0 24 24" width="22" height="22" fill="none"
    stroke="#3f6b46" stroke-width="2"><path d="M12 3l7 4v5c0 5-3 8-7 9-4-1-7-4-7-9V7z"/></svg>
    检测结论：四项均合格</div>
  <div class="items">{item_row("#3f6b46")}</div>
</div>
<div class="stage">
  <img class="report" src="{REPORT.as_uri()}">
  <img class="prod" src="{(OUT / PROD).as_uri()}">
  <div class="stamp"><b>合格</b><span>QC PASSED</span></div>
</div>
<div class="foot">检测依据 GB 2763、GB 2762 等国家标准 · 报告编号 GZF25-0058295-02<br>
本图为产品成分检测信息展示，不作为疾病预防或治疗依据</div>
</body></html>"""


def main():
    src = sorted(ASSETS.glob("A_single_daily_multivitamin*.png"))[0]
    cut_white(src, OUT / PROD)
    (OUT / "_A.html").write_text(HTML_A, encoding="utf-8")
    (OUT / "_B.html").write_text(HTML_B, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name in ("_A", "_B"):
            pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
            pg.goto((OUT / f"{name}.html").as_uri())
            pg.wait_for_timeout(450)
            pg.screenshot(path=str(OUT / f"{name[-1]}.png"))
            pg.close()
        b.close()
    print("saved", OUT)


if __name__ == "__main__":
    main()

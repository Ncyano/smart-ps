"""真实引擎补测：多候选是否真出多张（充值后补验）。

上一轮 `AccountOverdueError 403` 只验证了错误路径，成功路径（3 张真出 3 张、
并发拆批、下载落盘、评分）从未被真实跑过。本脚本就是补这一课。

用法：
    python scripts/real_seedream_test.py            # 默认 2 张，省钱
    python scripts/real_seedream_test.py -n 3       # 3 张
    python scripts/real_seedream_test.py -n 1       # 先探活，最便宜

判据（全部满足才算通过）：
    1. HTTP 200，batch.status 不是 failed
    2. produced == requested
    3. rounds == requested / per_request（证明是拆批并发，不是一次 n 张）
    4. 每个 final_*.png 可解码、宽高 ≥ 512
    5. 各候选 MD5 互不相同（防止引擎返回同一张）
    6. cost_cny 与张数对得上
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "http://127.0.0.1:8765"

MATERIAL = ROOT / "p图素材_例子/产品工厂素材/f830c578640841cb94ebca6fe372aff2.png"


def data_url(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    return f"data:image/{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def pick_exemplar() -> Path | None:
    """随便找一张「成图」当风格参考，没有就不传（不阻塞主判据）。"""
    for d in (ROOT / "p图素材_例子", ROOT / "产出/需求样张/_assets"):
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() in {".png", ".jpg", ".jpeg"} and p.is_file() and p.stat().st_size > 200_000:
                return p
    return None


def post(payload: dict, timeout: int = 300) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{API}/api/batches?sync=1",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:  # 400/500 也要能读到 body
        return exc.code, json.loads(exc.read().decode() or "{}")


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--num", type=int, default=2)
    args = ap.parse_args()

    if not MATERIAL.exists():
        print(f"素材不存在：{MATERIAL}")
        return 2
    ex = pick_exemplar()

    payload = {
        "instruction": "生成一张电商主图场景：产品置于浅色台面，柔和侧光，留出上方文案空间",
        "engine": "seedream",
        "candidates": args.num,
        "materials": [{"name": MATERIAL.name, "data_url": data_url(MATERIAL)}],
        "exemplars": ([{"name": ex.name, "data_url": data_url(ex)}] if ex else []),
    }

    print(f"素材 {MATERIAL.name}  例图 {ex.name if ex else '无'}  要 {args.num} 张")
    code, body = post(payload)
    print(f"HTTP {code}")

    if code != 200:
        print(json.dumps(body, ensure_ascii=False, indent=2)[:1200])
        print("\n[FAIL] 请求未成功")
        return 1

    bid = body["id"]
    print(f"批次 {bid}  引擎 {body['engine']}  {body['elapsed_s']}s  费用 ¥{body['cost_cny']}")
    print(f"requested={body['requested']} produced={body['produced']} "
          f"rounds={body['recipe']['rounds']} per_request={body['recipe']['per_request']}")
    if body.get("gen_errors"):
        print("拆批错误：" + "；".join(body["gen_errors"]))
    if body.get("engine_note"):
        print("引擎提示：" + body["engine_note"])

    checks: list[tuple[str, bool, str]] = []
    checks.append(("状态非 failed", body.get("status") != "failed", str(body.get("status"))))
    checks.append(("张数对得上", body["produced"] == body["requested"],
                   f"{body['produced']}/{body['requested']}"))
    expect_rounds = -(-body["requested"] // max(1, body["recipe"]["per_request"]))
    checks.append(("确实拆批", body["recipe"]["rounds"] == expect_rounds,
                   f"{body['recipe']['rounds']} 轮 / 应 {expect_rounds} 轮"))

    hashes, sizes = [], []
    from PIL import Image
    for c in body.get("candidates", []):
        # final_url 形如 /files/batches/<id>/final/final_1.png
        p = ROOT / "data" / c["final_url"].split("/files/", 1)[1]
        ok = p.exists()
        if ok:
            with Image.open(p) as im:
                sizes.append(im.size)
                ok = min(im.size) >= 512
        hashes.append(md5(p) if p.exists() else "MISSING")
        print(f"  #{c['index']} {c['verdict']:<4} total={c['total']:.3f} "
              f"fid={c['scores']['fidelity']:.3f} style={c['scores']['style']:.3f} "
              f"size={sizes[-1] if sizes else '?'} md5={hashes[-1]}")

    checks.append(("每张可解码且 ≥512", len(sizes) == body["produced"] and all(
        min(s) >= 512 for s in sizes), str(sizes)))
    checks.append(("候选互不相同", len(set(hashes)) == len(hashes), f"{len(set(hashes))}/{len(hashes)} 唯一"))
    checks.append(("费用与张数一致", abs(body["cost_cny"] - 0.12 * body["produced"]) < 0.001,
                   f"¥{body['cost_cny']} vs ¥{0.12 * body['produced']:.2f}"))

    print("\n" + "=" * 54)
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    bad = [n for n, ok, _ in checks if not ok]
    print("=" * 54)
    if bad:
        print(f"未通过：{', '.join(bad)}")
        return 1
    print(f"全部通过，产物在 data/batches/{bid}/final/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

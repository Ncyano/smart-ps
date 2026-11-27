"""素材授权闸门的离线测试（不联网、不花钱）。

这一层不产出分数，只决定「这批图能不能对外」。所以要测的是：
- 五个声明值各自落到哪个等级
- 别名能否识别（设计师不会记得 owned / third_party 怎么拼）
- 无效值不能悄悄当"可交付"
- 内部参考必须有可见的警告与说明文件

用法：python scripts/test_rights.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.app import rights as R  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


def main() -> int:
    print("\n【1】五个声明的等级映射")
    cases = [
        (R.OWNED, "ok", True, "可正式交付"),
        (R.LICENSED, "ok", True, "可正式交付"),
        (R.THIRD_PARTY, "internal", False, "仅内部参考"),
        (R.SAMPLE, "internal", False, "仅内部参考"),
        (R.UNKNOWN, "unconfirmed", None, "未声明"),
    ]
    for declared, level, can, label in cases:
        r = R.resolve(declared)
        check(f"{declared:<12} → {level}",
              r.level == level and r.can_deliver is can,
              f"得到 level={r.level} can={r.can_deliver}")
        check(f"{declared:<12} 标签为「{label}」", r.label == label, r.label)

    print("\n【2】别名与大小写")
    for raw, want in [("own", R.OWNED), ("OWNED", R.OWNED), ("真实", R.OWNED),
                      ("授权", R.LICENSED), ("第三方", R.THIRD_PARTY),
                      ("demo", R.SAMPLE), ("示例", R.SAMPLE), ("  licensed  ", R.LICENSED)]:
        r = R.resolve(raw)
        check(f"「{raw}」→ {want}",
              r.level == R.resolve(want).level,
              f"得到 level={r.level} declared={r.declared}")

    print("\n【3】异常输入不能变成「可交付」")
    r = R.resolve("随便写的值")
    check("无法识别的值 → unconfirmed", r.level == "unconfirmed", r.level)
    check("并标记 invalid", r.invalid is True, str(r.invalid))
    check("can_deliver 为 None（不是 True）", r.can_deliver is None, str(r.can_deliver))

    r = R.resolve(None)
    check("None → unknown", r.level == "unconfirmed", r.level)
    r = R.resolve("")
    check("空字符串 → unknown", r.level == "unconfirmed", r.level)

    print("\n【4】警告文案")
    ok = R.resolve(R.OWNED)
    check("可交付时不产生警告", ok.warning == "", ok.warning)

    tp = R.resolve(R.THIRD_PARTY)
    check("第三方 → 警告含「仅内部参考」", "仅内部参考" in tp.warning, tp.warning)
    check("第三方 → 警告提到会写说明文件", R.EXPORT_NOTICE in tp.warning, tp.warning)

    un = R.resolve(R.UNKNOWN)
    check("未声明 → 警告含「未确认」", "未确认" in un.warning, un.warning)
    check("未声明 → 警告给出可选值", "owned" in un.warning, un.warning)

    print("\n【5】批次读取：老批次没有这个字段")
    old_batch = {"id": "20260101-000000-aaaaaa", "candidates": []}
    g = R.for_batch(old_batch)
    check("老批次 → 按 unknown 处理", g.level == "unconfirmed", g.level)

    new_batch = {"id": "x", "rights": {"declared": R.SAMPLE}}
    check("新批次读得出声明", R.for_batch(new_batch).level == "internal", "")

    print("\n【6】导出闸门")
    b = {"id": "20261127-000000-abcdef", "created_at": "2026-11-27", "rights": {"declared": R.THIRD_PARTY}}
    gate = R.gate(b)
    check("internal_only=True", gate["internal_only"] is True, "")
    check("带 notice_file 名", gate["notice_file"] == R.EXPORT_NOTICE, gate["notice_file"])
    check("warning 非空", bool(gate["warning"]), "")

    b_owned = {"id": "y", "rights": {"declared": R.OWNED}}
    g2 = R.gate(b_owned)
    check("自有素材 → 不写说明、无警告",
          g2["internal_only"] is False and g2["warning"] == "" and g2["notice_file"] == "",
          str(g2))

    print("\n【7】说明文件内容")
    txt = R.notice_text(b, R.for_batch(b))
    for must in ("不可对外投放", "批次", b["id"], "替换为自有产品素材", "示例口径"):
        check(f"含「{must}」", must in txt, txt[:80])
    check("不是空文件", len(txt) > 120, f"{len(txt)} 字")

    print("\n" + "=" * 62)
    if FAIL == 0:
        print(f"全部通过（{PASS} 条）")
    else:
        print(f"失败 {FAIL} 条 / 共 {PASS + FAIL} 条")
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

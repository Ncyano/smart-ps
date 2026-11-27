"""项目知识库检索（零依赖，纯标准库）。

为什么不用向量 RAG：
  1. 项目约定"只依赖标准库"，装 embedding 模型会破坏可移植性
  2. 知识库只有几万字，BM25 的关键词召回已经够用，而且可解释
  3. 按「二级/三级标题」切块，天然就是语义单元，比定长切片准

用法：
    python scripts/kb_search.py "错位"
    python scripts/kb_search.py "倒影 羽化" -n 8
    python scripts/kb_search.py "RANSAC" --full        # 打印完整段落
    python scripts/kb_search.py --list                 # 列出全部章节
    python scripts/kb_search.py "quad" --json          # 机器可读

中文检索用「字符二元组 + 单字」做 term，英文用词本身；
打分用 BM25（k1=1.5, b=0.75）。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "knowledge"
# 除知识库外，顺带索引这些对理解项目有用的大文档
EXTRA = [
    ROOT / "项目方案.md",
    ROOT / "README.md",
    ROOT / "p图素材_例子" / "_分类清单.md",
    ROOT / "产出" / "需求样张" / "说明.md",
]

CJK = re.compile(r"[\u4e00-\u9fff]")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-.]*")
HEAD = re.compile(r"^(#{1,4})\s+(.*)$")


def encode(text: str) -> list[str]:
    """英文取词（小写）；中文取单字 + 相邻二元组。"""
    low = text.lower()
    terms = [w for w in WORD.findall(low) if len(w) > 1]
    chars = [c for c in low if CJK.match(c)]
    terms.extend(chars)
    terms.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
    return terms


def split_chunks(path: Path) -> list[dict]:
    """按 markdown 标题切块；代码块与表格保持在所属小节内。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    chunks: list[dict] = []
    cur = {"file": path, "heading": path.stem, "start": 1, "lines": []}

    for i, line in enumerate(lines, 1):
        m = HEAD.match(line)
        # 一级标题不切（整个文件就是一章），二/三/四级切
        if m and len(m.group(1)) >= 2:
            if any(x.strip() for x in cur["lines"]):
                chunks.append(cur)
            cur = {"file": path, "heading": m.group(2).strip(), "start": i, "lines": []}
        else:
            cur["lines"].append(line)

    if any(x.strip() for x in cur["lines"]):
        chunks.append(cur)

    out = []
    for c in chunks:
        text = "\n".join(c["lines"]).strip()
        if len(text) < 30:
            continue
        out.append({
            "file": c["file"],
            "rel": str(c["file"].relative_to(ROOT)).replace("\\", "/"),
            "heading": c["heading"],
            "start": c["start"],
            "text": text,
        })
    return out


def build_index():
    files = []
    if KB_DIR.exists():
        files.extend(sorted(KB_DIR.glob("*.md")))
    files.extend(p for p in EXTRA if p.exists())

    chunks = []
    for f in files:
        chunks.extend(split_chunks(f))
    if not chunks:
        print("没有找到可索引的文档，请检查 knowledge/ 目录。", file=sys.stderr)
        sys.exit(1)

    for c in chunks:
        tf = Counter(encode(c["heading"] * 3 + "\n" + c["text"]))
        c["tf"] = tf
        c["len"] = sum(tf.values())

    df = Counter()
    for c in chunks:
        df.update(set(c["tf"]))
    n = len(chunks)
    avgdl = sum(c["len"] for c in chunks) / n
    idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
    return chunks, idf, avgdl


def search(query: str, chunks, idf, avgdl, top_k=5, k1=1.5, b=0.75):
    q_terms = Counter(encode(query))
    # 去掉纯单字里过于泛的（的了是在和与等）
    stop = set("的了是在和与等这那有为以对及或就都也很更还")
    q_terms = {t: w for t, w in q_terms.items() if not (len(t) == 1 and t in stop)}
    if not q_terms:
        return []

    scored = []
    for c in chunks:
        s = 0.0
        for t, qw in q_terms.items():
            f = c["tf"].get(t)
            if not f:
                continue
            denom = f + k1 * (1 - b + b * c["len"] / avgdl)
            s += qw * idf.get(t, 0.0) * f * (k1 + 1) / denom
        if s > 0:
            # 标题命中加权：同样的分数，标题里出现的更相关
            head_terms = set(encode(c["heading"]))
            hits = sum(1 for t in q_terms if t in head_terms)
            s *= 1 + 0.35 * hits
            scored.append((s, c))

    scored.sort(key=lambda x: -x[0])
    return scored[:top_k]


def snippet(text: str, limit=180) -> str:
    body = re.sub(r"\s+", " ", text).strip()
    return body[:limit] + ("…" if len(body) > limit else "")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="项目知识库检索")
    ap.add_argument("query", nargs="*", help="关键词，多个用空格分隔")
    ap.add_argument("-n", "--num", type=int, default=5, help="返回条数（默认 5）")
    ap.add_argument("--full", action="store_true", help="打印完整段落")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--list", action="store_true", help="列出全部章节")
    args = ap.parse_args()

    chunks, idf, avgdl = build_index()

    if args.list:
        cur = None
        for c in chunks:
            if c["rel"] != cur:
                cur = c["rel"]
                print(f"\n{cur}")
            print(f"  L{c['start']:<5} {c['heading']}")
        print(f"\n共 {len(chunks)} 个章节，{len(idf)} 个索引词")
        return

    q = " ".join(args.query).strip()
    if not q:
        ap.print_help()
        return

    hits = search(q, chunks, idf, avgdl, args.num)
    if not hits:
        print(f"没有匹配「{q}」的内容。试试 --list 看目录。")
        return

    if args.json:
        print(json.dumps([
            {"score": round(s, 3), "file": c["rel"], "heading": c["heading"],
             "line": c["start"], "text": c["text"]}
            for s, c in hits
        ], ensure_ascii=False, indent=2))
        return

    print(f"\n检索「{q}」  →  {len(hits)} 条（共索引 {len(chunks)} 个章节）\n")
    for i, (s, c) in enumerate(hits, 1):
        print(f"[{i}] {s:5.2f}  {c['rel']}  ›  {c['heading']}   (L{c['start']})")
        body = c["text"] if args.full else snippet(c["text"])
        for line in body.splitlines():
            print("     " + line)
        print()


if __name__ == "__main__":
    main()

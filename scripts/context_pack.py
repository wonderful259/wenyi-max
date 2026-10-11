#!/usr/bin/env python3
"""为某一章生成翻译上下文包（思路移植自 wenyi 的批次上下文注入）。

用法：
  python3 context_pack.py <project_dir> ch08

产出 state/context/chNN.md，按「稳定信息在前」排列：
  1. 风格指南（state/style-guide.md，含用户确认过的文本画像）
  2. 全书概览（state/book-overview.md）
  3. 本章梗概 + 下一章梗概（state/digests/chNN.md；下一章只作伏笔参考）
  4. 本章相关术语（glossary.jsonl 中原文出现在本章的条目 + 所有人物条目）；
     其中 phrase / sentence / fixed 类（锁定短语、重复句）单列，要求逐字照用
  5. 上一章末尾译文（衔接代词、称谓、语气）

翻译子代理读这一个文件 + chNN-en.md 即可开工，不必自己拼上下文。
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import Store, md_name, pad_width, parse_chapter_arg, strip_tags  # noqa: E402

TAIL_SEGMENTS = 8


def _read(path: str) -> str:
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def load_glossary(path: str) -> list[dict]:
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _appears(term: str, text: str) -> bool:
    term = term.strip()
    if not term:
        return False
    flags = 0 if any(ch.isupper() for ch in term) else re.I
    return re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text, flags) is not None


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    project_dir = sys.argv[1]
    ci = parse_chapter_arg(sys.argv[2])
    if ci is None:
        print("章节参数应为 ch08 这样的形式")
        return 2

    store = Store(project_dir)
    manifest = store.load_manifest()
    n = len(manifest["chapters"])
    width = pad_width(n)
    state = store.state_dir
    chapter = store.load_chapter(ci)
    source_text = "\n".join(strip_tags(s["source"]) for s in chapter["segments"])

    parts: list[str] = [f"# 翻译上下文包 · ch{ci:0{width}d}《{chapter.get('title') or ''}》\n"]

    style = _read(os.path.join(state, "style-guide.md"))
    parts.append("## 一、风格指南（用户已确认，必须遵守）\n\n" + (style or "（缺失！先完成分析阶段）"))

    overview = _read(os.path.join(state, "book-overview.md"))
    if overview:
        parts.append("## 二、全书概览\n\n" + overview)

    digest = _read(os.path.join(state, "digests", f"ch{ci:0{width}d}.md"))
    nxt = _read(os.path.join(state, "digests", f"ch{ci + 1:0{width}d}.md")) if ci + 1 < n else ""
    if digest or nxt:
        block = "## 三、章节梗概\n\n"
        if digest:
            block += f"**本章：** {digest}\n\n"
        if nxt:
            block += f"**下一章（仅供理解伏笔，不得提前透露）：** {nxt}\n"
        parts.append(block)

    glossary = load_glossary(os.path.join(state, "glossary.jsonl"))
    relevant = []
    for e in glossary:
        names = [e.get("en", "")] + list(e.get("aliases") or [])
        if any(_appears(nm, source_text) for nm in names if nm) or e.get("category") == "person":
            relevant.append(e)
    phrases = [e for e in relevant if e.get("category") in ("phrase", "sentence", "fixed")]
    relevant = [e for e in relevant if e not in phrases]
    if phrases:
        rows = ["| 原文说法 | 锁定译法 | 备注 |", "|---|---|---|"]
        for e in phrases:
            rows.append(f"| {e.get('en', '')} | {e.get('zh', '')} | {e.get('note', '')} |")
        parts.append(f"## 四（甲）、锁定短语与重复句（{len(phrases)} 条，逐字照用）\n\n"
                     "原文在别处用过同一说法，读者靠「同一个说法」认出呼应。本章再出现时必须用锁定译法，"
                     "锁定译法本身不要拆开改写（前后语序可调）；句法上确实套不进去时，在最终回复里写明用了什么变体，"
                     "主代理决定是否加进该条 aliases，不要自创新译法。\n\n"
                     + "\n".join(rows))
    if relevant:
        rows = ["| 原文 | 译名 | 类别 | 备注 |", "|---|---|---|---|"]
        for e in relevant:
            en = e.get("en", "")
            if e.get("aliases"):
                en += "（" + " / ".join(e["aliases"]) + "）"
            rows.append(f"| {en} | {e.get('zh', '')} | {e.get('category', '')} | {e.get('note', '')} |")
        parts.append(f"## 四、本章相关术语（{len(relevant)}/{len(glossary)} 条，必须照用）\n\n" + "\n".join(rows))
    else:
        parts.append("## 四、本章相关术语\n\n（术语表中暂无本章出现的条目）")

    if ci > 0:
        prev = store.load_chapter(ci - 1)
        tail = [s for s in prev["segments"] if s.get("target")][-TAIL_SEGMENTS:]
        if tail:
            parts.append("## 五、上一章末尾译文（衔接参考，不要重译）\n\n"
                         + "\n\n".join(strip_tags(s["target"]) for s in tail))

    parts.append("## 六、本章原文\n\n" + os.path.join(project_dir, "chapters", md_name(ci, width, "en")))

    out_dir = os.path.join(state, "context")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"ch{ci:0{width}d}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")
    words = len(source_text.split())
    print(f"{out}\n  本章 {len(chapter['segments'])} 段 / 约 {words} 词；相关术语 {len(relevant)} 条"
          f"；锁定短语 {len(phrases)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())

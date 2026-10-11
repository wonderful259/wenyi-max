#!/usr/bin/env python3
"""机械质检扫描：在人工/模型审校前，先用规则把可疑段落筛出来。

用法：
  python3 qa_scan.py <project_dir>              # 扫描所有已回填章节
  python3 qa_scan.py <project_dir> ch08 ch09    # 只扫指定章

读取 state/chapters/chN.json 里已回填的 target，逐段检查：

  【严重】疑似漏译   原文 ≥15 词但译文汉字/原文词数 < 0.9
  【严重】疑似未译   译文汉字占比过低（大段英文残留）
  【提示】疑似添译   原文 ≥8 词但比值 > 3.6
  【提示】的的不休   单个分句「的」≥ 3 个
  【提示】引号失衡   原文引号成对而译文开/合引号数量不等（「」与“”都查）
  【提示】引号混用   同一段里既有「」又有“”
  【严重】数量词     half (a) dozen ≠ 六、dozen ≠ 十二/一打、score ≠ 二十、fortnight ≠ 两周…
  【提示】半角残留   中文旁仍有半角 , . ! ? : ;
  【统计】翻译腔/AI 腔高频词、被动「被」、人称代词密度（每千字）

结果写入 reviews/qa-scan.md（审校阶段优先处理【严重】项），终端打印摘要。
阈值是经验值：比值异常 ≠ 一定有错，审校时对照原文判断。
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import (  # noqa: E402
    STATUS_DONE,
    Store,
    count_cjk,
    count_en_words,
    pad_width,
    parse_chapter_arg,
    strip_tags,
)

# 翻译腔 / AI 腔高频表达（出现不一定错，密度高就要回头看）
SUSPECT_PHRASES = [
    "值得注意的是", "值得一提的是", "不可否认", "毋庸置疑", "总而言之", "综上所述",
    "此外，", "与此同时，", "然而，", "因此，", "事实上，", "换句话说",
    "深入探讨", "至关重要", "不可或缺", "错综复杂", "格局", "织锦", "画卷", "交响",
    "在……的过程中", "进行了", "作出了", "对于", "关于", "通过", "随着",
    "一种", "一个", "他自己", "她自己", "它自己",
    "仿佛", "似乎", "不禁", "缓缓地", "微微地", "轻轻地",
]
_PRONOUNS = re.compile(r"[他她它][们]?")

# 数量习语：(原文模式, 译文里应出现的写法, 常见错译, 说明)。
# 命中原文而译文既没有「应出现」也出现了「常见错译」→ 严重；只是没有「应出现」→ 提示。
QUANTITY_IDIOMS = [
    (r"\bhalf[- ]?a[- ]dozen\b|\bhalf[- ]dozen\b", r"(?<![五四])六(?!七)|半打", r"五六|五、六|六七|几[个只条位块种次口道]", "half a dozen = 六（不是「五六个」）"),
    (r"\b(?:a|one|the)\s+dozen\b(?!s)", r"十二|一打(?![算开量扮听招发断扰])", r"", "a dozen = 十二 / 一打（原文明显是约数时「十来」可接受）"),
    (r"\b(?:two|three|four|five|six)\s+dozen\b", r"[两三四五六]打(?![算开量扮听招发断扰])|二十四|三十六|四十八|六十|七十二", r"几十", "N dozen = N×12 / N 打"),
    (r"\b(?:a|one)\s+score\s+(?:of|years)\b|\b(?:three|four|two)\s*score\b", r"二十|四十|六十|八十", r"", "score = 二十"),
    (r"\bfortnight\b", r"两周|两个?星期|两个?礼拜|半个月|十四", r"", "fortnight = 两周"),
    (r"\bbrace of\b", r"一对|两", r"", "a brace of = 一对 / 两"),
]
_QUANTITY_RE = [(re.compile(a, re.I), re.compile(b), re.compile(c) if c else None, d)
                for a, b, c, d in QUANTITY_IDIOMS]
_CLAUSE_SPLIT = re.compile(r"[。！？!?…，,；;：:]+")


def scan_segment(src: str, tgt: str) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    src_plain = strip_tags(src)
    tgt_plain = strip_tags(tgt)
    words = count_en_words(src_plain)
    cjk = count_cjk(tgt_plain)
    if words >= 5:
        latin = len(re.findall(r"[A-Za-z]", tgt_plain))
        if cjk == 0 or latin > cjk * 1.5:
            issues.append(("严重", f"疑似未译（汉字 {cjk}，拉丁字母 {latin}）"))
            return issues
    if words >= 15 and cjk / words < 0.9:
        issues.append(("严重", f"疑似漏译（原文 {words} 词 → 译文 {cjk} 字，比值 {cjk / words:.2f}）"))
    elif words >= 8 and cjk / max(words, 1) > 3.6:
        issues.append(("提示", f"疑似添译/啰嗦（原文 {words} 词 → 译文 {cjk} 字，比值 {cjk / words:.2f}）"))
    for sent in _CLAUSE_SPLIT.split(tgt_plain):
        if sent.count("的") >= 3:
            issues.append(("提示", f"的的不休：「{sent.strip()[:40]}」"))
            break
    src_q = len(re.findall(r"[\"“”]", src_plain))
    if src_q and src_q % 2 == 0 and (tgt_plain.count("“") != tgt_plain.count("”")
                                     or tgt_plain.count("「") != tgt_plain.count("」")):
        issues.append(("提示", "引号失衡（原文成对，译文开/合引号数量不等）"))
    if re.search(r"[「」]", tgt_plain) and re.search(r"[“”]", tgt_plain):
        issues.append(("提示", "引号混用（同段既有「」又有“”）"))
    for src_re, ok_re, bad_re, note in _QUANTITY_RE:
        if src_re.search(src_plain) and not ok_re.search(tgt_plain):
            bad = bad_re.search(tgt_plain) if bad_re else None
            if bad:
                issues.append(("严重", f"数量词错译：译文「{bad.group(0)}」——{note}"))
            else:
                issues.append(("提示", f"数量词未见对应译法——{note}"))
    if re.search(r"[一-鿿][,.!?:;](?![0-9A-Za-z])|(?<![0-9A-Za-z])[,.!?:;][一-鿿]", tgt_plain):
        issues.append(("提示", "中文旁有半角标点残留"))
    return issues


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    project_dir = sys.argv[1]
    store = Store(project_dir)
    manifest = store.load_manifest()
    width = pad_width(len(manifest["chapters"]))
    wanted = {parse_chapter_arg(a) for a in sys.argv[2:]} - {None}
    targets = [c["index"] for c in manifest["chapters"]
               if c["status"] == STATUS_DONE and (not wanted or c["index"] in wanted)]
    if not targets:
        print("没有已回填的章节可扫描")
        return 1

    lines = ["# 机械质检扫描报告", "",
             "> 【严重】项必须在审校阶段逐条对照原文核实；【提示】项抽查。比值异常 ≠ 一定有错。", ""]
    total = Counter()
    phrase_counter: Counter = Counter()
    zh_chars = 0
    bei = 0
    pron = 0
    per_chapter = []
    for ci in targets:
        ch = store.load_chapter(ci)
        ch_issues = []
        for s in ch["segments"]:
            tgt = s.get("target")
            if not tgt:
                ch_issues.append((s["index"], "严重", "无译文（回退原文）", s["source"]))
                continue
            plain = strip_tags(tgt)
            zh_chars += count_cjk(plain)
            bei += plain.count("被")
            pron += len(_PRONOUNS.findall(plain))
            for ph in SUSPECT_PHRASES:
                if "……" in ph:
                    a, b = ph.split("……")
                    phrase_counter[ph] += len(re.findall(re.escape(a) + ".{1,20}?" + re.escape(b), plain))
                else:
                    phrase_counter[ph] += plain.count(ph)
            for level, msg in scan_segment(s["source"], tgt):
                ch_issues.append((s["index"], level, msg, s["source"]))
        sev = sum(1 for x in ch_issues if x[1] == "严重")
        hint = len(ch_issues) - sev
        total["严重"] += sev
        total["提示"] += hint
        per_chapter.append((ci, sev, hint))
        if ch_issues:
            lines.append(f"## ch{ci:0{width}d}（严重 {sev} / 提示 {hint}）")
            lines.append("")
            for idx, level, msg, src in sorted(ch_issues, key=lambda x: (x[1] != "严重", x[0])):
                excerpt = re.sub(r"\s+", " ", strip_tags(src))[:70]
                lines.append(f"- 【{level}】⟦{idx}⟧ {msg} —— 原文：{excerpt}")
            lines.append("")

    per_k = (lambda n: n * 1000 / zh_chars) if zh_chars else (lambda n: 0)
    lines.append("## 全书统计（每千汉字）")
    lines.append("")
    lines.append(f"- 汉字总数：{zh_chars}")
    lines.append(f"- 「被」字句密度：{per_k(bei):.2f}（> 3 偏高，检查被动滥用）")
    lines.append(f"- 人称代词密度：{per_k(pron):.1f}（> 25 偏高，汉语多省略主语）")
    top = [(p, n) for p, n in phrase_counter.most_common() if n and per_k(n) >= 0.3]
    if top:
        lines.append("- 高频可疑表达：" + "；".join(f"{p} {n} 次（{per_k(n):.2f}‰）" for p, n in top[:15]))

    out_dir = os.path.join(project_dir, "reviews")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "qa-scan.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"扫描 {len(targets)} 章：严重 {total['严重']} 处 / 提示 {total['提示']} 处 → {out}")
    worst = sorted(per_chapter, key=lambda x: -x[1])[:5]
    if worst and worst[0][1]:
        print("严重项最多的章：" + "，".join(f"ch{ci:0{width}d}({n})" for ci, n, _ in worst if n))
    print(f"被字密度 {per_k(bei):.2f}‰ / 代词密度 {per_k(pron):.1f}‰")
    return 0


if __name__ == "__main__":
    sys.exit(main())

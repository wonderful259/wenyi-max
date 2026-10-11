#!/usr/bin/env python3
"""原文重复句 / 情节关键短语扫描：找出全书反复出现、译法必须锁定的说法。

用法：
  python3 recurring_phrases.py <project_dir> [--min-count 3] [--top 80]

为什么需要：小说里反复出现的句子（咒语、誓词、口头禅、章末回环）和情节关键
短语（如 foreign magic、the dust-wife's price）在原文里是同一个说法，读者靠
「同一个说法」认出呼应。分章并行翻译时各章各译一套（外邦 / 外敌 / 外国），
呼应就断了。术语表只收专名，这类短语要单独扫出来锁定。

扫描三类候选（只读原文，不需要译文）：
  A. 重复句：去掉引号标点后完全相同、≥ 4 词的句子，出现 ≥ 2 次
  B. 重复长短语：3–8 词 n-gram，出现 ≥ min-count 次且跨 ≥ 2 章（保留最长的）
  C. 关键搭配：两个实词组成的二元组，出现 ≥ min-count 次、跨 ≥ 2 章，且在
     两个词各自的出现里占比高（固定搭配，不是偶然相邻）

产出 state/recurring-candidates.md。主代理在建术语表时过一遍：挑出情节相关、
需要全书一致的条目，用 category "phrase"（短语）或 "sentence"（整句）写进
glossary.jsonl 并定下译法。之后：
  - context_pack.py 会把本章出现的 phrase / sentence 条目注入上下文包
  - check_terms.py 会逐段检查：原文含该短语的段，译文必须含锁定译法
不需要锁定的（普通搭配、无呼应意义）忽略即可。
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import Store, strip_tags  # noqa: E402

STOPWORDS = set("""
a an the and or but if then than so as at by for from in into of off on onto out over to up with
without about above after again against all am any are aren't be because been before being below
between both can can't could couldn't did didn't do does doesn't doing don't down during each few
further had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself
him himself his how how's i i'd i'll i'm i've is isn't it it's its itself let's me more most mustn't
my myself no nor not now once only other ought our ours ourselves own same shan't she she'd she'll
she's should shouldn't some such that that's their theirs them themselves there there's these they
they'd they'll they're they've this those through too under until very was wasn't we we'd we'll
we're we've were weren't what what's when when's where where's which while who who's whom why why's
will won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
said says say just like one two back even still also well yes oh know think thought go went going
get got come came see saw look looked made make way thing things something nothing anything
much many little lot really quite rather perhaps maybe though although yet ever never always
""".split())

# 动作/神态套话里的词：全由这些词（加停用词、人名、术语）组成的句子/短语不值得锁定
BEATS = set("""
took take deep breath shook shake head heads step steps forward bowed bow bent dropped turned turn
opened open mouth closed close eyes eye around room across felt feel pang began begin cry tried try
explain find trying able might rest great deal half hour minutes twenty thirty years tightly widened
popped ears hurried crooked frowned blinked swallowed watched wondered nodded shrugged shrugged again
snorted sniffed stood stepped walked finally sounding muttered rumbled croaked gave sharp dryly bit
haughtily lip lips licked lower waved hands hand shoulders shoulder arm patted inhaled sharply sorry
cleared throat rolled stared asked replied answered whispered sighed laughed smiled grinned nod
good right okay fine thank thanks sure hush know need tell asked another
""".split())

_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
_SENT_SPLIT = re.compile(r"(?<=[.!?…])[\"”’)\]]*\s+|\n+")


def _norm(text: str) -> str:
    text = strip_tags(text).replace("’", "'").replace("‘", "'").lower()
    return text


def _words(text: str) -> list[str]:
    return _WORD.findall(_norm(text))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    min_count = 3
    top = 80
    if "--min-count" in sys.argv:
        min_count = int(sys.argv[sys.argv.index("--min-count") + 1])
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])

    project_dir = args[0]
    store = Store(project_dir)
    manifest = store.load_manifest()

    glossary_words: set[str] = set()
    gpath = os.path.join(store.state_dir, "glossary.jsonl")
    glossary_phrases: set[str] = set()
    person_words: set[str] = set()
    if os.path.exists(gpath):
        with open(gpath, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                en = e.get("en") or ""
                w = _words(en)
                if e.get("category") == "person":
                    person_words.update(w)
                glossary_words.update(w)
                glossary_phrases.add(" ".join(w))

    cap_count: Counter = Counter()
    sent_hits: dict[str, list[tuple[int, int]]] = defaultdict(list)
    sent_orig: dict[str, str] = {}
    ngram_hits: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    word_count: Counter = Counter()
    seg_text: dict[tuple[int, int], str] = {}

    for c in manifest["chapters"]:
        ch = store.load_chapter(c["index"])
        for s in ch["segments"]:
            src = strip_tags(s.get("source") or "")
            if not src.strip():
                continue
            key = (ch["index"], s["index"])
            seg_text[key] = src
            for sent in _SENT_SPLIT.split(src):
                w = _words(sent)
                if len(w) >= 4:
                    k = " ".join(w)
                    sent_hits[k].append(key)
                    sent_orig.setdefault(k, sent.strip().strip("\"“”‘’'"))
            w = _words(src)
            word_count.update(w)
            for m in re.finditer(r"(?<![.!?\"“]\s)(?<!^)\b([A-Z][a-z]+)", src):
                cap_count[m.group(1).lower()] += 1
            seen = set()
            for n in range(2, 9):
                for i in range(len(w) - n + 1):
                    g = tuple(w[i:i + n])
                    if g in seen:
                        continue
                    seen.add(g)
                    ngram_hits[g].append(key)

    def name_like(x: str) -> bool:
        x = x[:-2] if x.endswith("'s") else x
        return x in person_words or (word_count[x] >= 2 and cap_count[x] >= 0.7 * word_count[x])

    def boring(words) -> bool:
        """全是停用词 / 动作套话 / 人名 / 已入术语表的词 → 不值得锁定。"""
        return all(x in STOPWORDS or x in BEATS or name_like(x) or x in glossary_words
                   or x.endswith("'s") and x[:-2] in glossary_words for x in words)

    def chapters_of(hits):
        return sorted({ci for ci, _ in hits})

    # A. 重复句
    sentences = [(k, v) for k, v in sent_hits.items()
                 if len(v) >= 2 and not boring(k.split())]
    # 长句优先（咒语、誓词、回环句往往较长），同长按次数
    sentences.sort(key=lambda kv: (-len(kv[0].split()), -len(kv[1])))

    # B. 重复长短语（3–8 词），去掉被更长 n-gram 完全覆盖的
    long_ng = {g: v for g, v in ngram_hits.items()
               if len(g) >= 3 and len(v) >= min_count and len(chapters_of(v)) >= 2
               and g[0] not in STOPWORDS and g[-1] not in STOPWORDS
               and sum(1 for x in g if x not in STOPWORDS) >= 2
               and not boring(g) and " ".join(g) not in glossary_phrases}
    maximal = []
    for g, v in long_ng.items():
        covered = False
        for n2 in range(len(g) + 1, 9):
            for g2, v2 in long_ng.items():
                if len(g2) == n2 and len(v2) >= len(v) - 0 and \
                        any(g2[i:i + len(g)] == g for i in range(len(g2) - len(g) + 1)):
                    covered = True
                    break
            if covered:
                break
        if not covered:
            maximal.append((g, v))
    sent_keys = {k for k, _ in sentences}
    maximal = [(g, v) for g, v in maximal if " ".join(g) not in sent_keys]
    maximal.sort(key=lambda gv: (-len(gv[1]) * len(gv[0]), gv[0]))

    # C. 关键搭配（二元组，两个都是实词，固定程度高）
    colloc = []
    for g, v in ngram_hits.items():
        if len(g) != 2 or len(v) < min_count or len(chapters_of(v)) < 2:
            continue
        a, b = g
        if a in STOPWORDS or b in STOPWORDS or len(a) < 3 or len(b) < 3:
            continue
        if boring(g) or name_like(a) or name_like(b) or " ".join(g) in glossary_phrases:
            continue
        assoc = len(v) / min(word_count[a], word_count[b])
        if assoc >= 0.25:
            colloc.append((g, v, assoc))
    colloc.sort(key=lambda x: (-(len(x[1]) * x[2]), x[0]))

    def ctx(hits, phrase_words):
        ci, si = hits[0]
        text = seg_text[(ci, si)]
        m = re.search(r"\W+".join(re.escape(x) for x in phrase_words),
                      text.replace("’", "'"), re.I)
        if not m:
            return text[:90]
        a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
        return ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")

    def loc(hits):
        chs = chapters_of(hits)
        s = ", ".join(f"ch{c}" for c in chs[:8])
        return s + ("…" if len(chs) > 8 else "")

    lines = ["# 原文重复句 / 关键短语候选", "",
             "> 主代理在建术语表时过一遍：情节相关、读者需要认出「同一个说法」的条目，",
             "> 以 category \"phrase\" / \"sentence\" 写进 glossary.jsonl 并定下译法（en 写原文里的原样短语）。",
             "> 例：{\"en\":\"foreign magic\",\"zh\":\"外邦魔法\",\"category\":\"phrase\",\"note\":\"情节关键：……\"}",
             "> 普通搭配、无呼应意义的忽略。check_terms.py 会逐段检查已锁定条目。", ""]
    lines += [f"## A. 重复句（{len(sentences)}）", ""]
    for k, v in sentences[:top]:
        lines.append(f"- ×{len(v)}（{loc(v)}）{sent_orig[k][:120]}")
    lines += ["", f"## B. 重复长短语（{len(maximal)}）", ""]
    for g, v in maximal[:top]:
        lines.append(f"- ×{len(v)}（{loc(v)}）**{' '.join(g)}** —— {ctx(v, g)}")
    lines += ["", f"## C. 关键搭配（{len(colloc)}）", ""]
    for g, v, assoc in colloc[:top]:
        lines.append(f"- ×{len(v)}（{loc(v)}，固定度 {assoc:.2f}）**{' '.join(g)}** —— {ctx(v, g)}")

    out = os.path.join(store.state_dir, "recurring-candidates.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"重复句 {len(sentences)} / 长短语 {len(maximal)} / 关键搭配 {len(colloc)} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""术语一致性校验（通用版，冲突检测思路移植自 wenyi glossary/store.py）。

用法：
  python3 check_terms.py <chapters_dir> <glossary.jsonl> [--residue-min 4]

术语表格式（jsonl）：
  {"en":"原文词","zh":"译名","category":"person|place|org|tech|concept|item|title|fixed","note":"…","first":"chXX","aliases":["别名"]}

检查项：
  1. 术语冲突：同一 en 对应多个 zh（重复条目不同译法）→ 报错
  2. 译名撞车：同一 zh 被多个 en 共用 → 警告（人工确认是否有意合并）
  3. 逐章渲染检查：en 在 chNN-en.md 出现但 zh 未在对应 chNN-zh.md 出现 → 警告
     （该章可能用了别的译法）
  4. 英文残留：-zh.md 中长度 ≥ residue-min 的英文单词，排除术语表 en、
     可选白名单 state/allowed-words.txt（每行一词）→ 报错
  5. 锁定短语逐段复用：category 为 phrase / sentence / fixed 的多词条目，原文含
     该说法的每一段，译文都必须含锁定译法（或 aliases 之一）→ 报错
     （recurring_phrases.py 扫出候选，主代理挑选后写进术语表）
  6. 有字面义的名字被音译：人名/物名/地名等条目，英文去掉头衔后全是普通英语
     单词（如 Finder），译名却是纯音译用字（芬德）→ 警告。确认就是要音译的，
     在条目里加 "name_mode":"sound" 并在 note 写理由；意译的写 "meaning"。

退出码：有报错项时 1，否则 0。
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import parse_marked_md, strip_tags  # noqa: E402

PHRASE_CATEGORIES = {"phrase", "sentence", "fixed"}
NAME_CATEGORIES = {"person", "animal", "creature", "item", "place", "org", "title", "ship"}
# 头衔/虚词：判断「名字本体」时去掉
TITLE_WORDS = set("""the a an of lord lady miss mister mr mrs ms sir dame saint st general archbishop
bishop king queen prince princess captain doctor dr father mother sister brother aunt uncle
great old young little our""".split())
# 本身是普通英语单词、但通常照音译的常见西方名字
COMMON_GIVEN_NAMES = set("""rose hope grace will mark bill jack pat faith joy iris ivy lily daisy violet
hazel dawn summer april may june ruby pearl amber holly heather robin rob sue ray dean don guy earl
frank gene art bob carol sandy penny crystal autumn olive rusty chuck sterling hunter carter
mason baker taylor cooper fletcher""".split())
_ZH_TITLES = re.compile(r"勋爵|小姐|夫人|女士|先生|将军|大主教|主教|圣女|圣徒|爵士|大人|修女|修士|"
                        r"王后|国王|王子|公主|姑婆|姑妈|阿姨|叔叔|船长|博士|医生|号$")
# 《外国人名汉字译写》常用音译字（不求全，够判断「这是不是纯音译」）
TRANSLIT_CHARS = set(
    "阿埃艾爱安昂奥巴拜班邦保贝本比彼宾波伯博布查达代戴丹当道德登迪蒂丁东杜多厄恩尔法范菲费芬丰"
    "弗福夫盖甘冈戈格根贡古瓜圭哈海汉豪赫亨胡华霍基吉加贾杰金卡凯坎康柯科克肯库夸奎拉莱兰朗劳勒"
    "雷蕾里利莉丽连林琳隆卢鲁伦罗洛马玛迈曼芒梅蒙米明莫姆穆纳娜奈南瑙内尼妮宁诺帕潘庞佩彭皮珀普齐"
    "奇恰乔切钦琼丘萨赛桑瑟森沙莎尚舍施什斯泰坦汤唐特蒂廷图托瓦旺威韦维温沃乌伍西希锡夏谢辛休"
    "雅亚扬耶伊因英尤于扎泽詹兹佐祖娅娃丝黛姬琪薇芙苔茜妲蓓璐露缇莱斯德尔")


def _dict_words() -> set[str]:
    path = "/usr/share/dict/words"
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8", errors="ignore") as f:
        return {w.strip() for w in f if w.strip().islower()}


def _phrase_re(en: str) -> re.Pattern:
    words = re.findall(r"[A-Za-z0-9]+", en.replace("’", "'"))
    return re.compile(r"\b" + r"[\W_]+".join(re.escape(w) for w in words) + r"\b", re.I)


def _plain(text: str) -> str:
    """只留汉字和字母数字，比较锁定句时不受标点/引号风格影响。"""
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", strip_tags(text))


def load_glossary(path: str) -> list[dict]:
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"⚠️ 术语表第 {ln} 行不是合法 JSON，已跳过：{line[:60]}")
    return entries


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    chapters_dir, glossary_file = sys.argv[1], sys.argv[2]
    residue_min = 4
    if "--residue-min" in sys.argv:
        residue_min = int(sys.argv[sys.argv.index("--residue-min") + 1])

    entries = load_glossary(glossary_file)
    errors: list[str] = []
    warnings: list[str] = []

    # 1. 同 en 多 zh（冲突）
    by_en: dict[str, set[str]] = {}
    for e in entries:
        en, zh = (e.get("en") or "").strip(), (e.get("zh") or "").strip()
        if en and zh:
            by_en.setdefault(en, set()).add(zh)
    for en, zhs in sorted(by_en.items()):
        if len(zhs) > 1:
            errors.append(f"术语冲突：'{en}' 有 {len(zhs)} 个译法：{' / '.join(sorted(zhs))}")

    # 2. 同 zh 多 en（撞车）
    by_zh: dict[str, set[str]] = {}
    for en, zhs in by_en.items():
        for zh in zhs:
            by_zh.setdefault(zh, set()).add(en)
    for zh, ens in sorted(by_zh.items()):
        if len(ens) > 1:
            warnings.append(f"译名撞车：'{zh}' 同时对应 {' / '.join(sorted(ens))}")

    # 章节文件配对
    pairs: list[tuple[str, str, str]] = []  # (base, en_path, zh_path)
    for fn in sorted(os.listdir(chapters_dir)):
        if fn.endswith("-en.md"):
            base = fn[:-6]
            zh_fn = base + "-zh.md"
            if os.path.exists(os.path.join(chapters_dir, zh_fn)):
                pairs.append((base, os.path.join(chapters_dir, fn),
                              os.path.join(chapters_dir, zh_fn)))

    # 3. 逐章渲染检查 + 4. 英文残留
    glossary_en = {en for en in by_en}
    glossary_en_words = set()
    for en in glossary_en:
        glossary_en_words.update(re.findall(r"[A-Za-z]+", en))

    allowed = set()
    allowed_path = os.path.join(os.path.dirname(glossary_file), "allowed-words.txt")
    if os.path.exists(allowed_path):
        with open(allowed_path, "r", encoding="utf-8") as f:
            allowed = {w.strip() for w in f if w.strip() and not w.startswith("#")}

    residue: dict[str, list[str]] = {}
    word_re = re.compile(rf"[A-Za-z][A-Za-z''-]{{{residue_min - 1},}}")
    render_issues = 0
    for base, en_path, zh_path in pairs:
        with open(en_path, "r", encoding="utf-8") as f:
            en_text = f.read()
        with open(zh_path, "r", encoding="utf-8") as f:
            zh_text = re.sub(r"⟦[^⟧]*⟧", "", f.read())  # 段落标记不算残留

        for en, zhs in by_en.items():
            if len(zhs) != 1:
                continue  # 冲突项已单独报错
            zh = next(iter(zhs))
            if re.search(rf"\b{re.escape(en)}\b", en_text) and zh not in zh_text \
                    and en not in zh_text:
                warnings.append(f"渲染不一致：{base} 原文有 '{en}' 但译文未见 '{zh}'")
                render_issues += 1

        for w in set(word_re.findall(zh_text)):
            if w in allowed or w in glossary_en_words:
                continue
            residue.setdefault(w, []).append(base)

    # 5. 锁定短语 / 重复句逐段复用
    locked = []
    for e in entries:
        en = (e.get("en") or "").strip()
        if e.get("category") in PHRASE_CATEGORIES and len(re.findall(r"[A-Za-z]+", en)) >= 2 \
                and len(by_en.get(en, ())) == 1:
            variants = [e["zh"]] + [a for a in (e.get("aliases") or []) if a]
            locked.append((en, _phrase_re(en), [_plain(v) for v in variants], e["zh"]))
    phrase_misses = 0
    if locked:
        for base, en_path, zh_path in pairs:
            with open(en_path, encoding="utf-8") as f:
                en_segs, _ = parse_marked_md(f.read())
            with open(zh_path, encoding="utf-8") as f:
                zh_segs, _ = parse_marked_md(f.read())
            for i, src in en_segs.items():
                src_plain = strip_tags(src).replace("’", "'")
                tgt = _plain(zh_segs.get(i, ""))
                for en, rx, variants, zh in locked:
                    if rx.search(src_plain) and not any(v and v in tgt for v in variants):
                        errors.append(f"锁定短语未复用：{base} ⟦{i}⟧ '{en}' 应译「{zh}」"
                                      f"（确属合理变体就加进该条 aliases）")
                        phrase_misses += 1

    # 6. 有字面义的名字被音译
    dict_words = _dict_words()
    if dict_words:
        for e in entries:
            if e.get("category") not in NAME_CATEGORIES or e.get("name_mode") == "sound":
                continue
            en, zh = (e.get("en") or "").strip(), (e.get("zh") or "").strip()
            core = [w for w in re.findall(r"[a-z]+", en.lower()) if w not in TITLE_WORDS]
            if not core or not en[:1].isupper():
                continue  # 普通名词条目（nurse、monastery）不查
            if not all(len(w) >= 3 and w in dict_words and w not in COMMON_GIVEN_NAMES for w in core):
                continue
            zh_core = re.sub(r"[·・\s]", "", _ZH_TITLES.sub("", zh))
            if zh_core and all(ch in TRANSLIT_CHARS for ch in zh_core):
                warnings.append(f"名字可能有字面义却被音译：'{en}' → 「{zh}」"
                                f"（{' / '.join(core)} 是普通英语单词；意译见 genre 指南「有意义的名字」；"
                                f"确认音译就在条目加 \"name_mode\":\"sound\"）")

    for w, chs in sorted(residue.items()):
        errors.append(f"英文残留：'{w}'（{', '.join(chs[:5])}{'…' if len(chs) > 5 else ''}）")

    # 输出
    print(f"术语 {len(by_en)} 条（锁定短语 {len(locked)} 条）/ 已翻章节 {len(pairs)} 章")
    if warnings:
        print(f"\n⚠️ {len(warnings)} 个警告：")
        for w in warnings:
            print(f"  {w}")
    if errors:
        print(f"\n❌ {len(errors)} 个问题：")
        for e in errors:
            print(f"  {e}")
        print("\n专有名词/品牌等允许保留英文的词，加入 state/allowed-words.txt（每行一词）")
        return 1
    print("\n✅ 术语一致性检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

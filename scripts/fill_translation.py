#!/usr/bin/env python3
"""译文回填 + 段落对齐硬校验 + 格式标签校验（段数对齐移植自 wenyi）。

用法：
  python3 fill_translation.py <project_dir> ch03           # 回填单章
  python3 fill_translation.py <project_dir> --all          # 回填所有已有 -zh.md 的章
  python3 fill_translation.py <project_dir> ch03 --lenient # 允许缺段（导出时回退原文）
  python3 fill_translation.py <project_dir> ch03 --no-punct # 跳过标点规范化
  python3 fill_translation.py <project_dir> --all --quotes=curly  # 改用“”‘’并写入 manifest

引号风格：manifest 的 quote_style（corner =「」『』，curly =“”‘’），缺省 corner。
风格确认时定下；--quotes=… 会改写 manifest，之后的回填都沿用。

流程：解析 chapters/chNN-zh.md 的 ⟦N⟧ 标记 → 校验段落 id 与原文完全一致
（缺段/多段/空段则报错退出，防漏译）→ 校验段内 <N> 格式标签（脚注号/图片
丢失、标签错乱则报错；斜体等样式标签丢失只警告）→ 逐段标点规范化 → 写入
state/，章节标记为 done。⟦title⟧ 行写入 manifest 的 title_translated（目录回填用）。

校对/润色后修改了 -zh.md，重跑本脚本即可（幂等覆盖）。
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import (  # noqa: E402
    DEFAULT_QUOTE_STYLE,
    QUOTE_STYLES,
    STATUS_DONE,
    Store,
    check_tags,
    md_name,
    normalize_zh,
    pad_width,
    parse_chapter_arg,
    parse_marked_md,
    strip_tags,
)

_EXCERPT = 40


def _excerpt(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text[:_EXCERPT] + ("…" if len(text) > _EXCERPT else "")


def fill_chapter(store: Store, manifest: dict, ci: int, width: int,
                 md_dir: str, *, lenient: bool, punct: bool,
                 quote_style: str = DEFAULT_QUOTE_STYLE) -> list[str]:
    """回填一章，返回错误列表（空表示成功）。警告直接打印。"""
    zh_path = os.path.join(md_dir, md_name(ci, width, "zh"))
    if not os.path.isfile(zh_path):
        return [f"找不到译文文件 {zh_path}"]
    with open(zh_path, "r", encoding="utf-8") as f:
        translations, title_zh = parse_marked_md(f.read())

    chapter = store.load_chapter(ci)
    expected = {s["index"] for s in chapter["segments"]}
    got = set(translations)

    errors: list[str] = []
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    empty = sorted(i for i in (expected & got) if not translations[i].strip())

    if missing and not lenient:
        for i in missing:
            src = next(s["source"] for s in chapter["segments"] if s["index"] == i)
            errors.append(f"缺段 ⟦{i}⟧：原文「{_excerpt(src)}」")
    if extra:
        errors.extend(f"多出未知标记 ⟦{i}⟧（原文没有这个段号）" for i in extra)
    if empty and not lenient:
        errors.extend(f"空译文 ⟦{i}⟧" for i in empty)
    if errors:
        return errors

    by_index = {s["index"]: s for s in chapter["segments"]}
    tag_warnings = []
    for i, text in sorted(translations.items()):
        seg = by_index[i]
        t_err, t_warn = check_tags(seg["source"], text, seg.get("inline") or {})
        if t_err and not lenient:
            errors.extend(f"⟦{i}⟧ {e}" for e in t_err)
        tag_warnings.extend(f"⟦{i}⟧ {w}" for w in t_warn + (t_err if lenient else []))
    if errors:
        return errors
    if tag_warnings:
        print(f"  ⚠️ ch{ci}: {len(tag_warnings)} 处格式标签警告（样式会丢失，不影响文字）：")
        for w in tag_warnings[:8]:
            print(f"     {w}")
        if len(tag_warnings) > 8:
            print(f"     …另 {len(tag_warnings) - 8} 处")

    warned_same = 0
    for i, text in translations.items():
        seg = by_index[i]
        text = text.strip()
        if not text:
            continue
        if punct:
            text = normalize_zh(text, quote_style)
        if strip_tags(text) == strip_tags(seg["source"]).strip() and len(text) > 8:
            warned_same += 1
        seg["target"] = text
    if warned_same:
        print(f"  ⚠️ ch{ci}: {warned_same} 段译文与原文完全相同（可能未翻译）")
    if missing:
        print(f"  ⚠️ ch{ci}: {len(missing)} 段缺译（lenient 模式，导出时将回退原文）")

    store.save_chapter(chapter)
    for c in manifest["chapters"]:
        if c["index"] == ci:
            c["status"] = STATUS_DONE
            if title_zh:
                c["title_translated"] = title_zh
            break
    store.save_manifest(manifest)
    return []


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 2

    project_dir = args[0]
    store = Store(project_dir)
    if not store.exists():
        print(f"{project_dir} 下没有 state/manifest.json，先运行 ingest.py")
        return 1
    manifest = store.load_manifest()
    width = pad_width(len(manifest["chapters"]))
    md_dir = os.path.join(project_dir, "chapters")
    lenient = "--lenient" in flags
    punct = "--no-punct" not in flags
    quote_flag = next((f.split("=", 1)[1] for f in flags if f.startswith("--quotes=")), None)
    if quote_flag:
        if quote_flag not in QUOTE_STYLES:
            print(f"--quotes 只能是 {' / '.join(QUOTE_STYLES)}")
            return 2
        manifest["quote_style"] = quote_flag
        store.save_manifest(manifest)
    quote_style = manifest.get("quote_style") or DEFAULT_QUOTE_STYLE

    if "--all" in flags:
        targets = [c["index"] for c in manifest["chapters"]
                   if os.path.isfile(os.path.join(md_dir, md_name(c["index"], width, "zh")))]
        if not targets:
            print("chapters/ 下没有任何 chNN-zh.md")
            return 1
    else:
        targets = []
        for a in args[1:]:
            ci = parse_chapter_arg(a)
            if ci is None:
                print(f"无法识别章节参数：{a}（期望 ch03 这样的形式）")
                return 2
            targets.append(ci)

    if not targets:
        if quote_flag:
            print(f"已设置引号风格：{quote_flag}（之后的回填都按此统一；已回填的章节用 --all 重跑即可）")
            return 0
        print("未指定章节；用 ch03 形式或 --all")
        return 2

    failed = 0
    for ci in targets:
        errors = fill_chapter(store, manifest, ci, width, md_dir,
                              lenient=lenient, punct=punct, quote_style=quote_style)
        if errors:
            failed += 1
            print(f"❌ ch{ci} 对齐校验失败（{len(errors)} 处）：")
            for e in errors:
                print(f"   {e}")
        else:
            print(f"✅ ch{ci} 已回填")

    if failed:
        print(f"\n{failed} 章失败。修正 chapters/ 下对应 -zh.md 后重跑本脚本；"
              f"确认某些段无需翻译时可加 --lenient（导出时回退原文）。")
        return 1
    done = sum(1 for c in manifest["chapters"] if c["status"] == STATUS_DONE)
    print(f"\n进度：{done}/{len(manifest['chapters'])} 章完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())

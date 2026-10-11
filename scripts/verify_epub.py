#!/usr/bin/env python3
"""交付前 EPUB 校验：结构完整性 + 译文覆盖率 + epubcheck 对比原书。

用法：
  python3 verify_epub.py <project_dir> <out.epub>

检查项：
  1. zip 结构：mimetype 为首个条目且不压缩；原书所有文件都在（图片/字体/CSS 不丢）
  2. 每个 XHTML 是合法 XML（原书本身合法的前提下）
  3. 残留：data-tn-id 锚点、⟦N⟧ 段落标记、<N> 格式标签不得出现在正文里
  4. 覆盖率：各章已译段数 / 总段数，列出未译段
  5. epubcheck（如已安装）：只报告「原书没有、导出后新增」的错误

退出码：存在新增错误或残留时 1，否则 0。
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import Store, pad_width  # noqa: E402

_HTML_EXTS = (".xhtml", ".html", ".htm")
_RESIDUE = [
    ("data-tn-id 锚点", re.compile(r"data-tn-id")),
    ("⟦N⟧ 段落标记", re.compile(r"⟦(?:\d+|title)⟧")),
    ("<N> 格式标签", re.compile(r"&lt;/?\d+/?&gt;")),
]


def _epubcheck(path: str) -> collections.Counter | None:
    if not shutil.which("epubcheck"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        report = tmp.name
    try:
        subprocess.run(["epubcheck", path, "--json", report],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        with open(report, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    finally:
        if os.path.exists(report):
            os.remove(report)
    return collections.Counter(
        (m.get("severity"), m.get("ID")) for m in data.get("messages", [])
        if m.get("severity") in ("FATAL", "ERROR")
    )


def _xml_ok(data: bytes) -> bool:
    from lxml import etree
    try:
        etree.fromstring(data, etree.XMLParser(resolve_entities=False, recover=False))
        return True
    except Exception:
        return False


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    project_dir, out_path = sys.argv[1], sys.argv[2]
    store = Store(project_dir)
    manifest = store.load_manifest()
    problems: list[str] = []
    notes: list[str] = []

    # 1. zip 结构
    with zipfile.ZipFile(out_path) as zout:
        infos = zout.infolist()
        if not infos or infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED:
            problems.append("mimetype 不是首个未压缩条目（部分阅读器会拒绝打开）")
        out_names = {i.filename for i in infos}
        out_docs = {n: zout.read(n) for n in out_names if n.lower().endswith(_HTML_EXTS)}

    src_docs: dict[str, bytes] = {}
    if manifest["fmt"] == "epub":
        with zipfile.ZipFile(store.resolve(manifest["source_path"])) as zsrc:
            src_names = set(zsrc.namelist())
            src_docs = {n: zsrc.read(n) for n in src_names if n.lower().endswith(_HTML_EXTS)}
        missing = sorted(src_names - out_names)
        if missing:
            problems.append(f"导出缺少原书文件 {len(missing)} 个：{missing[:5]}")

    # 2. XML 合法性 + 3. 残留
    bad_xml = [n for n, d in out_docs.items() if not _xml_ok(d) and (n not in src_docs or _xml_ok(src_docs[n]))]
    if bad_xml:
        problems.append(f"{len(bad_xml)} 个 XHTML 不是合法 XML（原书合法）：{bad_xml[:5]}")
    for label, rx in _RESIDUE:
        hits = [n for n, d in out_docs.items() if rx.search(d.decode("utf-8", "replace"))]
        if hits:
            problems.append(f"残留{label}：{hits[:5]}")

    # 4. 覆盖率
    width = pad_width(len(manifest["chapters"]))
    total = done = 0
    untranslated: list[str] = []
    for c in manifest["chapters"]:
        ch = store.load_chapter(c["index"])
        segs = ch["segments"]
        n_done = sum(1 for s in segs if (s.get("target") or "").strip())
        total += len(segs)
        done += n_done
        if n_done < len(segs):
            untranslated.append(f"ch{c['index']:0{width}d}（{len(segs) - n_done}/{len(segs)} 段未译）")
    cov = done / total * 100 if total else 0
    notes.append(f"译文覆盖率：{done}/{total} 段（{cov:.1f}%）")
    if untranslated:
        notes.append("含未译段的章（导出时保留原文）：" + "，".join(untranslated[:15])
                     + ("…" if len(untranslated) > 15 else ""))

    # 5. epubcheck 对比
    out_ec = _epubcheck(out_path)
    if out_ec is None:
        notes.append("epubcheck 未安装或运行失败，跳过（brew install epubcheck）")
    else:
        src_ec = _epubcheck(store.resolve(manifest["source_path"])) if manifest["fmt"] == "epub" else collections.Counter()
        src_ec = src_ec or collections.Counter()
        new = {k: v - src_ec.get(k, 0) for k, v in out_ec.items() if v > src_ec.get(k, 0)}
        notes.append(f"epubcheck：原书 {sum(src_ec.values())} 个错误 / 导出 {sum(out_ec.values())} 个错误")
        if new:
            problems.append("导出后新增 epubcheck 错误：" + "，".join(f"{k[1]}×{v}" for k, v in new.items())
                            + f"（详情：epubcheck \"{out_path}\"）")

    print(f"校验：{out_path}")
    for n in notes:
        print(f"  · {n}")
    if problems:
        print(f"\n❌ {len(problems)} 个问题：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\n✅ EPUB 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

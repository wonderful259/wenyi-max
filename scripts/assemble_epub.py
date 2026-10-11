#!/usr/bin/env python3
"""导出译文（模板回填移植自 wenyi assemble/writer.py, MIT）。

用法：
  python3 assemble_epub.py <project_dir> [-o out.epub] [--bilingual] [--format epub|txt]

- 原文是 EPUB → 重开原 zip 逐条目拷贝，命中章节的 XHTML 按 data-tn-id 锚点
  替换为译文，段内 <N> 标签还原为原书的斜体/链接/脚注号；样式/图片/字体/
  内部跳转原样保留。目录（NCX/NAV）标题改为译名，OPF 语言改 zh-Hans；
  竖排源书自动改横排。
- --bilingual → 中英对照版：每段原文后紧跟译文（译文段去掉 id 防重复锚点）。
- 原文是 FB2/TXT（含 PDF 抽出的 txt）→ 从章节数据生成规范 EPUB3。
- --format txt → 按章重建纯文本。
缺译文的段回退用原文，保证不丢内容。

默认输出到项目文件夹根部：《中文书名》译文.epub（对照版《中文书名》中英对照.epub；
未定中文书名时用 <原文件名>_zh.epub）。可用环境变量 WENYI_SUFFIX 给文件名加后缀（如 WENYI_SUFFIX=draft → 译文_draft.epub）。项目文件夹带 _Vn 时文件名也带 _Vn；
同名文件已存在时版本号继续递增（不覆盖旧版）。

依赖：beautifulsoup4 + lxml（OPF/NCX 的 XML 解析需要 lxml）。
"""

from __future__ import annotations

import copy
import html as html_mod
import os
import re
import sys
import zipfile

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import KIND_HEADING, Store, build_inline, strip_tags  # noqa: E402

_HTML_EXTS = (".xhtml", ".html", ".htm")
_VERTICAL_MARKERS = (
    re.compile(rb"(?:-epub-|-webkit-)?writing-mode\s*:\s*(?:vertical-rl|vertical-lr|tb-rl)", re.I),
    re.compile(rb"page-progression-direction\s*=\s*['\"]rtl['\"]", re.I),
    re.compile(rb"\bclass\s*=\s*['\"][^'\"]*\bvrtl\b", re.I),
)
_HORIZONTAL_OVERRIDE_ID = "wenyi-max-horizontal-override"


def _seg_text(seg: dict) -> str:
    t = seg.get("target")
    return t if (t and t.strip()) else seg["source"]


def _ch_title(c: dict) -> str:
    return (c.get("title_translated") or c.get("title") or "").strip()


def _epub_lang(lang: str | None) -> str:
    normalized = (lang or "").strip().replace("_", "-").lower()
    if normalized in {"", "zh", "zh-cn", "zh-hans", "cn"}:
        return "zh-Hans"
    return lang or "zh-Hans"


# ── EPUB 模板回填 ───────────────────────────────────────────────────────────

def _render_chapter_html(chapter: dict, *, bilingual: bool = False) -> str:
    soup = BeautifulSoup(chapter.get("template") or "", "html.parser")
    by_anchor = {el["data-tn-id"]: el for el in soup.find_all(attrs={"data-tn-id": True})}
    for s in chapter["segments"]:
        el = by_anchor.get(s.get("anchor") or "")
        if el is None:
            continue
        del el["data-tn-id"]
        target = s.get("target")
        if not (target and target.strip()):
            continue  # 未译：保留原文原样（含格式）
        if bilingual:
            zh = copy.copy(el)
            if zh.has_attr("id"):
                del zh["id"]
            classes = zh.get("class") or []
            if isinstance(classes, str):
                classes = classes.split()
            zh["class"] = classes + ["tn-zh"]
            if el.parent is not None and el.parent.name == "hgroup":
                zh.name = "p"  # hgroup 内只允许一个标题元素
            zh.clear()
            build_inline(soup, zh, target, s.get("inline") or {})
            for node in zh.find_all(True):  # 脚注锚点等原子元素也带 id，去重
                if node.has_attr("id"):
                    del node["id"]
            el.insert_after(zh)
        else:
            el.clear()
            build_inline(soup, el, target, s.get("inline") or {})
    return str(soup)


def _base_no_frag(href: str) -> str:
    return os.path.basename((href or "").split("#", 1)[0])


def _attr_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _rewrite_opf_metadata(data: bytes, *, lang: str, force_horizontal: bool,
                          title_zh: str = "") -> bytes:
    """OPF：语言改为目标语言；有中文书名时 dc:title 改为中文；竖排源书改横排方向。"""
    try:
        soup = BeautifulSoup(data, "xml")
        if title_zh:
            title_el = soup.find("dc:title") or soup.find("title")
            if title_el is not None:
                title_el.clear()
                title_el.append(title_zh)
        lang_el = soup.find("dc:language") or soup.find("language")
        if lang_el is None:
            metadata = soup.find("metadata")
            if metadata is not None:
                lang_el = soup.new_tag("dc:language")
                metadata.append(lang_el)
        if lang_el is not None:
            lang_el.clear()
            lang_el.append(lang)

        if force_horizontal:
            for spine in soup.find_all("spine"):
                spine["page-progression-direction"] = "ltr"
        return soup.encode()
    except Exception:
        return data


def _epub_looks_vertical(zf: zipfile.ZipFile) -> bool:
    for info in zf.infolist():
        low = info.filename.lower()
        if not low.endswith((".opf", ".css", ".xhtml", ".html", ".htm")):
            continue
        try:
            data = zf.read(info.filename)
        except Exception:
            continue
        if any(marker.search(data) for marker in _VERTICAL_MARKERS):
            return True
    return False


def _rewrite_html_document(data: bytes | str, *, lang: str, force_horizontal: bool) -> bytes:
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        soup = BeautifulSoup(text, "html.parser")
        html = soup.find("html")
        if html is None:
            return text.encode("utf-8")
        html["lang"] = lang
        html["xml:lang"] = lang
        classes = html.get("class")
        if isinstance(classes, list) and "vrtl" in classes:
            html["class"] = [c for c in classes if c != "vrtl"]

        if force_horizontal and soup.find(id=_HORIZONTAL_OVERRIDE_ID) is None:
            head = soup.find("head")
            if head is None:
                head = soup.new_tag("head")
                html.insert(0, head)
            style = soup.new_tag("style", id=_HORIZONTAL_OVERRIDE_ID)
            style.string = (
                "html, body { "
                "writing-mode: horizontal-tb !important; "
                "-epub-writing-mode: horizontal-tb !important; "
                "-webkit-writing-mode: horizontal-tb !important; "
                "direction: ltr !important; "
                "text-orientation: mixed !important; "
                "} "
                ".vrtl, .vertical, [class*=\"vrtl\"] { "
                "writing-mode: horizontal-tb !important; "
                "-epub-writing-mode: horizontal-tb !important; "
                "-webkit-writing-mode: horizontal-tb !important; "
                "direction: ltr !important; "
                "}"
            )
            head.append(style)
        return str(soup).encode("utf-8")
    except Exception:
        return data if isinstance(data, bytes) else data.encode("utf-8")


def _rewrite_toc(data: bytes, title_by_base: dict[str, str], *, is_ncx: bool) -> bytes:
    """目录标题改译名：NCX navLabel / EPUB3 NAV 的 <a>，按 href 文件名匹配。"""
    try:
        if is_ncx:
            soup = BeautifulSoup(data, "xml")
            for np in soup.find_all("navPoint"):
                content = np.find("content")
                label = np.find("text")
                if content is None or label is None:
                    continue
                t = title_by_base.get(_base_no_frag(_attr_str(content.get("src"))))
                if t:
                    label.clear()
                    label.append(t)
            return soup.encode()
        soup = BeautifulSoup(data, "html.parser")
        toc_navs = [n for n in soup.find_all("nav")
                    if "toc" in (_attr_str(n.get("epub:type"))
                                 or _attr_str(n.get("type"))).split()]
        scopes = toc_navs or [soup]
        for scope in scopes:
            for a in scope.find_all("a", href=True):
                t = title_by_base.get(_base_no_frag(_attr_str(a.get("href"))))
                if t:
                    a.clear()
                    a.append(t)
        return str(soup).encode("utf-8")
    except Exception:
        return data


def _is_nav(data: bytes) -> bool:
    return b"epub:type" in data and b"toc" in data


def _assemble_epub(store: Store, manifest: dict, out_path: str, *, bilingual: bool = False) -> str:
    source_path = store.resolve(manifest["source_path"])
    target_lang = _epub_lang(manifest.get("target_lang", "zh"))

    rendered: dict[str, str] = {}
    for c in manifest["chapters"]:
        ch = store.load_chapter(c["index"])
        if ch.get("href") and ch.get("template"):
            rendered[ch["href"]] = _render_chapter_html(ch, bilingual=bilingual)

    title_by_base: dict[str, str] = {}
    for c in manifest["chapters"]:
        base = _base_no_frag(c.get("href") or "")
        t = _ch_title(c)
        if base and t:
            title_by_base[base] = t
    meta = manifest.get("meta") or {}
    for entry in meta.get("toc_entries", []) or []:
        if not isinstance(entry, dict):
            continue
        base = _base_no_frag(_attr_str(entry.get("href")))
        title_value = entry.get("title_translated") or ""
        if base and isinstance(title_value, str) and title_value.strip():
            title_by_base[base] = title_value.strip()

    with zipfile.ZipFile(source_path, "r") as zin:
        force_horizontal = _epub_looks_vertical(zin)
        infos = zin.infolist()
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in infos:
                name = info.filename
                low = name.lower()
                data = zin.read(name)
                if name in rendered:
                    zout.writestr(info, _rewrite_html_document(
                        rendered[name], lang=target_lang, force_horizontal=force_horizontal))
                elif name == "mimetype":
                    zout.writestr(info, data, zipfile.ZIP_STORED)
                elif low.endswith(".opf"):
                    zout.writestr(info, _rewrite_opf_metadata(
                        data, lang=target_lang, force_horizontal=force_horizontal,
                        title_zh=manifest.get("title_zh") or ""))
                elif low.endswith(".ncx"):
                    zout.writestr(info, _rewrite_toc(data, title_by_base, is_ncx=True))
                elif low.endswith(_HTML_EXTS):
                    if _is_nav(data):
                        data = _rewrite_toc(data, title_by_base, is_ncx=False)
                    zout.writestr(info, _rewrite_html_document(
                        data, lang=target_lang, force_horizontal=force_horizontal))
                else:
                    zout.writestr(info, data)
    return out_path


# ── 从章节数据生成规范 EPUB3（FB2/TXT 输入用，纯 zipfile 实现）──────────────

_BASIC_CSS = (
    "body{line-height:1.8;margin:0 1em;}"
    "p{text-indent:2em;margin:0.4em 0;text-align:justify;}"
    "h1{font-size:1.4em;text-align:center;margin:1.5em 0 1em;}"
    ".tn-en{color:#666;font-size:0.92em;text-indent:0;}"
)


def _build_epub_from_chapters(store: Store, manifest: dict, out_path: str, *,
                              bilingual: bool = False) -> str:
    esc = html_mod.escape
    lang = _epub_lang(manifest.get("target_lang", "zh"))
    title = manifest.get("title_zh") or manifest.get("title", "translated")

    chapter_files: list[tuple[str, str, str]] = []  # (fname, 标题, xhtml)
    for c in manifest["chapters"]:
        ch = store.load_chapter(c["index"])
        ch_title = _ch_title(c) or ch.get("title") or f"ch{c['index']}"
        body = []
        for s in ch["segments"]:
            tag = "h1" if s["kind"] == KIND_HEADING else "p"
            if bilingual and s.get("target"):
                body.append(f'<{tag} class="tn-en">{esc(strip_tags(s["source"]))}</{tag}>')
            body.append(f"<{tag}>{esc(strip_tags(_seg_text(s)))}</{tag}>")
        fname = f"ch{c['index']}.xhtml"
        xhtml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">'
            f'<head><title>{esc(ch_title)}</title><link rel="stylesheet" href="style.css" type="text/css"/></head>'
            f"<body>{''.join(body)}</body></html>"
        )
        chapter_files.append((fname, ch_title, xhtml))

    nav_lis = "".join(
        f'<li><a href="{f}">{esc(t)}</a></li>' for f, t, _ in chapter_files
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">'
        "<head><title>目录</title></head>"
        f'<body><nav epub:type="toc"><h1>目录</h1><ol>{nav_lis}</ol></nav></body></html>'
    )

    items = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
             '<item id="css" href="style.css" media-type="text/css"/>']
    itemrefs = []
    for i, (f, _t, _x) in enumerate(chapter_files):
        items.append(f'<item id="c{i}" href="{f}" media-type="application/xhtml+xml"/>')
        itemrefs.append(f'<itemref idref="c{i}"/>')
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="bookid">wenyi-max-{esc(title)}</dc:identifier>'
        f"<dc:title>{esc(title)}</dc:title>"
        f"<dc:language>{lang}</dc:language>"
        '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
        "</metadata>"
        f"<manifest>{''.join(items)}</manifest>"
        f"<spine>{''.join(itemrefs)}</spine>"
        "</package>"
    )
    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/style.css", _BASIC_CSS)
        for f, _t, xhtml in chapter_files:
            z.writestr(f"OEBPS/{f}", xhtml)
    return out_path


# ── 纯文本 ──────────────────────────────────────────────────────────────────

def _assemble_text(store: Store, manifest: dict, out_path: str) -> str:
    blocks = []
    for c in manifest["chapters"]:
        ch = store.load_chapter(c["index"])
        blocks.append("\n\n".join(strip_tags(_seg_text(s)) for s in ch["segments"]))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")
    return out_path


def _next_version_path(path: str) -> str:
    """同名文件已存在时递增版本号：x.epub → x_V2.epub；x_draft.epub → x_V2_draft.epub。"""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    m_tag = re.search(r"(_[a-zA-Z0-9]+)$", root)
    tag = m_tag.group(1) if m_tag else ""
    if re.fullmatch(r"_V\d+", tag):  # 版本号不是后缀
        tag = ""
    stem = root[:-len(tag)] if tag else root
    vm = re.search(r"_V(\d+)$", stem)
    n = int(vm.group(1)) + 1 if vm else 2
    stem = re.sub(r"_V\d+$", "", stem)
    while True:
        cand = f"{stem}_V{n}{tag}{ext}"
        if not os.path.exists(cand):
            return cand
        n += 1


def default_output(store: Store, manifest: dict, *, bilingual: bool, ext: str) -> str:
    """有中文书名：《中文书名》译文.epub / 《中文书名》中英对照.epub；
    否则用原文件名：<原文件名>_zh.epub / <原文件名>_中英对照.epub。（环境变量 WENYI_SUFFIX 可追加后缀）放在项目目录根部。
    项目文件夹带 _Vn 时文件名也带 _Vn；同名已存在则版本号继续递增，不覆盖。"""
    out_dir = store.resolve(manifest.get("output_dir") or ".")
    title_zh = (manifest.get("title_zh") or "").strip()
    suffix = os.environ.get("WENYI_SUFFIX", "")
    if suffix and not suffix.startswith("_"):
        suffix = f"_{suffix}"
    # 项目文件夹带 _V3 时，导出文件也带 _V3（与文件夹版本对应）
    fv = re.search(r"_V(\d+)(?=_|$)", os.path.basename(os.path.abspath(store.project_dir)))
    ver = f"_V{fv.group(1)}" if fv else ""
    if title_zh:
        name = f"《{title_zh}》{'中英对照' if bilingual else '译文'}{ver}{suffix}{ext}"
    else:
        origin = manifest.get("origin_path") or manifest["source_path"]
        if re.match(r"https?://", origin):
            base = re.sub(r"[\\/:*?\"<>|\s]+", "_", manifest.get("title") or "article")[:60]
        else:
            base = os.path.splitext(os.path.basename(origin))[0]
        name = f"{base}{'_中英对照' if bilingual else '_zh'}{ver}{suffix}{ext}"
    return _next_version_path(os.path.join(out_dir, name))


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    project_dir = args[0]
    out_path = None
    out_format = "epub"
    bilingual = False
    i = 1
    while i < len(args):
        if args[i] == "-o" and i + 1 < len(args):
            out_path = args[i + 1]
            i += 2
        elif args[i] == "--format" and i + 1 < len(args):
            out_format = args[i + 1]
            i += 2
        elif args[i] == "--bilingual":
            bilingual = True
            i += 1
        else:
            print(f"未知参数：{args[i]}")
            return 2

    store = Store(project_dir)
    if not store.exists():
        print(f"{project_dir} 下没有 state/manifest.json，先运行 ingest.py")
        return 1
    manifest = store.load_manifest()

    pending = [c["index"] for c in manifest["chapters"] if c["status"] != "done"]
    if pending:
        print(f"⚠️ {len(pending)} 章尚未回填完成（{pending[:10]}{'…' if len(pending) > 10 else ''}），"
              f"未完成的段将保留原文")

    if out_format == "txt":
        out = _assemble_text(store, manifest, out_path or default_output(store, manifest, bilingual=False, ext=".txt"))
    elif manifest["fmt"] == "epub":
        out = _assemble_epub(store, manifest, out_path or default_output(store, manifest, bilingual=bilingual, ext=".epub"),
                             bilingual=bilingual)
    else:
        out = _build_epub_from_chapters(store, manifest, out_path or default_output(store, manifest, bilingual=bilingual, ext=".epub"),
                                        bilingual=bilingual)
    exports = manifest.setdefault("exports", [])
    rel = os.path.relpath(out, project_dir)
    if rel not in exports:
        exports.append(rel)
        store.save_manifest(manifest)
    print(f"已导出：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

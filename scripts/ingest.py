#!/usr/bin/env python3
"""拆书：EPUB / FB2 / TXT / MD / HTML / DOCX / PDF / URL → 状态目录 + 带段落标记的翻译用 markdown。

用法：
  python3 ingest.py <输入> [<project_dir>]

  <输入> 可以是：
    book.epub / book.fb2 / book.txt     直接解析（EPUB 走模板回填，最大程度还原原书格式）
    article.md / .html / .htm / .docx  先用 pandoc 转成规范 EPUB 再解析（保留标题/斜体/链接/脚注/图片）
    https://…                           抓取网页正文 → 规范 HTML → pandoc 转 EPUB
    book.pdf                            pdftotext 抽文字 → TXT 路径（PDF 无法还原版式，只保证文字）
  <project_dir> 缺省为 <产出根目录>/YYYYMMDD_EnglishTitle[_Vn]/
               （同一本书已有项目时——不论日期，含 archive/——自动带下一个版本号）
               （产出根目录默认 ./wenyi-output，可用环境变量 WENYI_OUTPUT_ROOT 覆盖；
                 风格确认定下中文书名后用 set_title.py 改名为 YYYYMMDD_EnglishTitle_中文书名）

产出：
  <project>/source/                     原始输入的副本 / 中间 EPUB
  <project>/state/manifest.json         书籍元信息 + 各章状态
  <project>/state/chapters/chN.json     各章 segments（含回填锚点、段内格式表与 XHTML 模板）
  <project>/chapters/chNN-en.md         带 ⟦N⟧ 段落标记和 <N> 格式标签的原文（供翻译）

EPUB 解析要点（移植自 wenyi ingest/epub_reader.py, MIT）：
  按 spine 顺序逐个 XHTML 当作一章，提取叶子块级元素（p/h1-h6/li/div/blockquote…）
  为 segment，并在元素上打 data-tn-id 锚点；整份带锚点的 XHTML 存为章节
  template，导出时按锚点替换译文，原书样式/图片/目录/跳转全部保留。
  段内斜体/链接/脚注号等以 <N>…</N> / <N/> 标签保留（wenyi-max 新增）。

依赖：beautifulsoup4 + lxml；pandoc（md/html/docx/URL）；pdftotext（PDF）
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import (  # noqa: E402
    DEFAULT_QUOTE_STYLE,
    OUTPUT_ROOT,
    KIND_HEADING,
    KIND_TEXT,
    STATUS_PENDING,
    Store,
    camel_title,
    md_name,
    pad_width,
    render_marked_md,
    serialize_inline,
    strip_tags,
)

_CONTAINER = "META-INF/container.xml"
_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div",
               "dt", "dd", "figcaption", "td", "th", "caption", "pre"}
# 容器类块：只有当它内部没有其他块级元素时才作为段落（叶子块）
_SKIP_PARENTS = {"nav", "head", "script", "style", "title"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
STRAY_TEXT: list[str] = []


def _seg(index: int, source: str, kind: str, anchor: str | None = None) -> dict:
    return {"index": index, "source": source, "kind": kind,
            "target": None, "anchor": anchor}


# ── EPUB ────────────────────────────────────────────────────────────────────

def _find_opf_path(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(zf.read(_CONTAINER))
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "rootfile":
            return el.attrib["full-path"]
    raise ValueError("EPUB 损坏：container.xml 未找到 rootfile")


def _zip_href(base_path: str, href: str) -> str:
    clean = (href or "").split("#", 1)[0]
    if not clean:
        return ""
    base_dir = posixpath.dirname(base_path)
    return posixpath.normpath(posixpath.join(base_dir, clean)) if base_dir else clean


def _attr_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _parse_opf(zf: zipfile.ZipFile, opf_path: str) -> tuple[str, list[str], list[str]]:
    """返回 (书名, spine 顺序的 XHTML zip 路径列表, TOC/NAV 文件路径列表)。"""
    root = ET.fromstring(zf.read(opf_path))

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    title = ""
    manifest: dict[str, tuple[str, str, str]] = {}
    spine_ids: list[str] = []
    toc_ids: list[str] = []

    for el in root.iter():
        name = local(el.tag)
        if name == "title" and not title and el.text:
            title = el.text.strip()
        elif name == "item":
            manifest[el.attrib["id"]] = (
                el.attrib.get("href", ""),
                el.attrib.get("media-type", ""),
                el.attrib.get("properties", ""),
            )
        elif name == "itemref":
            spine_ids.append(el.attrib["idref"])
        elif name == "spine":
            toc = el.attrib.get("toc")
            if toc:
                toc_ids.append(toc)

    hrefs: list[str] = []
    for sid in spine_ids:
        if sid not in manifest:
            continue
        href, media, _props = manifest[sid]
        if "html" not in media and not href.endswith((".xhtml", ".html", ".htm")):
            continue
        hrefs.append(_zip_href(opf_path, href))

    toc_paths: list[str] = []
    for item_id, (href, media, props) in manifest.items():
        if item_id in toc_ids or "nav" in props.split() or media == "application/x-dtbncx+xml":
            toc_paths.append(_zip_href(opf_path, href))
    return title, hrefs, toc_paths


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _toc_label_map(zf: zipfile.ZipFile, toc_paths: list[str]) -> dict[str, str]:
    """zip href → 官方目录里的章节标题（NCX / EPUB3 NAV）。"""
    from bs4 import BeautifulSoup

    labels: dict[str, str] = {}
    names = set(zf.namelist())
    for toc_path in toc_paths:
        if toc_path not in names:
            continue
        data = zf.read(toc_path)
        if toc_path.lower().endswith(".ncx"):
            root = ET.fromstring(data)
            for nav_point in root.iter():
                if _local(nav_point.tag) != "navPoint":
                    continue
                label = ""
                src = ""
                for child in nav_point.iter():
                    name = _local(child.tag)
                    if name == "text" and child.text and not label:
                        label = child.text.strip()
                    elif name == "content" and not src:
                        src = child.attrib.get("src", "")
                href = _zip_href(toc_path, src)
                if href and label and href not in labels:
                    labels[href] = label
            continue

        soup = BeautifulSoup(data, "html.parser")
        toc_navs = [
            n for n in soup.find_all("nav")
            if "toc" in (_attr_str(n.get("epub:type")) or _attr_str(n.get("type"))).split()
        ]
        for nav in toc_navs or [soup]:
            for a in nav.find_all("a", href=True):
                label = a.get_text(" ", strip=True)
                href = _zip_href(toc_path, _attr_str(a.get("href")))
                if href and label and href not in labels:
                    labels[href] = label
    return labels


def _looks_like_internal_title(title: str, href: str, book_title: str = "") -> bool:
    base = posixpath.basename(href).rsplit(".", 1)[0]
    stripped = title.strip()
    return (bool(base) and stripped == base) or (bool(book_title) and stripped == book_title.strip())


def _extract_chapter(html: str, chapter_index: int, href: str, *,
                     book_title: str = "", toc_title: str = "") -> tuple[str, list[dict], str]:
    """解析单个 XHTML，返回 (标题, segments, 带锚点的模板 HTML)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    segments: list[dict] = []
    idx = 0
    for el in soup.find_all(_BLOCK_TAGS):
        # 只取叶子块：内部还有块级元素的容器（如包着 <p> 的 <div>/<blockquote>）跳过，
        # 由其内部的块各自成段；导航/脚本等区域整体跳过
        if el.find(_BLOCK_TAGS) is not None:
            continue
        if any(getattr(p, "name", None) in _SKIP_PARENTS for p in el.parents):
            continue
        if el.name == "pre":
            continue  # 代码/预排版文本原样保留
        text, inline = serialize_inline(el)
        if not strip_tags(text).strip():
            continue
        anchor = f"tn{chapter_index}_{idx}"
        el["data-tn-id"] = anchor
        kind = KIND_HEADING if el.name in _HEADING_TAGS else KIND_TEXT
        seg = _seg(idx, text, kind, anchor)
        if inline:
            seg["inline"] = inline
        segments.append(seg)
        idx += 1

    # 容器块里散落的直接文本（不在任何叶子块内）不会被翻译，计数提醒
    from bs4 import NavigableString
    for el in soup.find_all(_BLOCK_TAGS):
        if el.find(_BLOCK_TAGS) is None:
            continue
        for child in el.children:
            if isinstance(child, NavigableString) and child.strip() and len(child.strip()) > 3:
                STRAY_TEXT.append(f"{href}: {child.strip()[:40]}")

    # 标题：官方 TOC → 首个 heading → 非内部文件名/书名的 <title> → 无标题
    title = toc_title.strip()
    if not title:
        for s in segments:
            if s["kind"] == KIND_HEADING:
                title = strip_tags(s["source"])
                break
    if not title and soup.title and soup.title.string:
        candidate = soup.title.string.strip()
        if not _looks_like_internal_title(candidate, href, book_title):
            title = candidate

    return title, segments, str(soup)


def read_epub(path: str) -> dict:
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        opf_path = _find_opf_path(zf)
        book_title, hrefs, toc_paths = _parse_opf(zf, opf_path)
        toc_titles = _toc_label_map(zf, toc_paths)
        toc_entries = [
            {"href": href, "title": title, "title_translated": ""}
            for href, title in toc_titles.items()
            if href and title
        ]

        chapters: list[dict] = []
        ci = 0
        for href in hrefs:
            if href not in names:
                continue
            html = zf.read(href).decode("utf-8", errors="replace")
            title, segments, template = _extract_chapter(
                html, ci, href, book_title=book_title, toc_title=toc_titles.get(href, "")
            )
            if not segments:
                continue  # 无正文（封面等）→ 导出时原样拷贝，不作为章节
            chapters.append({"index": ci, "title": title, "segments": segments,
                             "href": href, "template": template})
            ci += 1

    return {
        "title": book_title or os.path.splitext(os.path.basename(path))[0],
        "fmt": "epub",
        "source_path": os.path.abspath(path),
        "chapters": chapters,
        "meta": {"opf_path": opf_path, "toc_paths": toc_paths, "toc_entries": toc_entries},
    }


# ── FB2（移植自 wenyi ingest/fb2_reader.py）─────────────────────────────────

_FB_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"
_CONTAINER_BLOCKS = {"epigraph", "cite", "poem", "stanza", "title", "annotation"}


def _strip_markup(el: ET.Element) -> str:
    return "".join(t for t in el.itertext() if t)


def _fb2_direct_segments(section: ET.Element, chapter_index: int) -> tuple[str, list[dict]]:
    segments: list[dict] = []
    idx = 0
    title_text = ""

    def add(text: str, kind: str) -> None:
        nonlocal idx
        text = text.strip()
        if text:
            segments.append(_seg(idx, text, kind, f"tn{chapter_index}_{idx}"))
            idx += 1

    def emit_block(el: ET.Element) -> None:
        tag = _local(el.tag)
        if tag == "subtitle":
            add(_strip_markup(el), KIND_HEADING)
        elif tag in ("p", "v", "text-author"):
            add(_strip_markup(el), KIND_TEXT)
        elif tag in _CONTAINER_BLOCKS:
            for sub in el:
                emit_block(sub)

    for child in section:
        tag = _local(child.tag)
        if tag == "section":
            continue
        if tag == "title":
            title_text = _strip_markup(child).strip()
            add(title_text, KIND_HEADING)
        else:
            emit_block(child)
    return title_text, segments


def _fb2_make_chapter(ci: int, title_text: str, segments: list[dict]) -> dict:
    if not title_text and segments:
        title_text = segments[0]["source"][:80]
    elif not title_text:
        title_text = f"第{ci + 1}章"
    return {"index": ci, "title": title_text, "segments": segments,
            "href": None, "template": None}


def _fb2_walk_sections(section: ET.Element, chapters: list[dict]) -> None:
    ci = len(chapters)
    title_text, segs = _fb2_direct_segments(section, ci)
    child_sections = [c for c in section if _local(c.tag) == "section"]

    if child_sections:
        has_body = any(s["kind"] == KIND_TEXT for s in segs)
        if has_body or title_text:
            chapters.append(_fb2_make_chapter(ci, title_text, segs))
        for cs in child_sections:
            _fb2_walk_sections(cs, chapters)
    elif segs:
        chapters.append(_fb2_make_chapter(ci, title_text, segs))


def read_fb2(path: str) -> dict:
    with open(path, "rb") as f:
        raw = f.read()

    enc = "utf-8"
    m = re.search(rb'<\?xml.*?encoding\s*=\s*"([^"]+)"', raw)
    if m:
        enc = m.group(1).decode("ascii", errors="replace")
    try:
        text = raw.decode(enc)
    except (UnicodeDecodeError, LookupError):
        text = raw.decode("utf-8", errors="replace")

    root = ET.fromstring(text)

    title = os.path.splitext(os.path.basename(path))[0]
    for desc in root.iter(f"{{{_FB_NS}}}title-info"):
        for child in desc:
            if _local(child.tag) == "book-title":
                if child.text:
                    title = child.text.strip()
                break

    chapters: list[dict] = []
    for body in root.findall(f"{{{_FB_NS}}}body"):
        if body.attrib.get("name", ""):
            continue  # notes / comments 等附属 body
        for section in body.findall(f"{{{_FB_NS}}}section"):
            _fb2_walk_sections(section, chapters)
        break

    return {"title": title, "fmt": "fb2", "source_path": os.path.abspath(path),
            "chapters": chapters, "meta": {}}


# ── TXT ─────────────────────────────────────────────────────────────────────

_TXT_CHAPTER_RE = re.compile(
    r"^\s*(第[0-9〇零一二三四五六七八九十百千]+[章节回部卷]|Chapter\s+\d+|CHAPTER\s+[IVXLC\d]+|[Pp]rologue|[Ee]pilogue)\b.*$"
)


def read_txt(path: str) -> dict:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    # 按章节标题行切分；没有可识别标题时整本一章
    chapters: list[dict] = []
    cur_title, cur_paras, buf = "", [], []

    def flush_para():
        if buf:
            cur_paras.append(" ".join(x.strip() for x in buf).strip())
            buf.clear()

    def flush_chapter():
        nonlocal cur_title, cur_paras
        flush_para()
        if cur_paras or cur_title:
            ci = len(chapters)
            segs = []
            idx = 0
            if cur_title:
                segs.append(_seg(idx, cur_title, KIND_HEADING, f"tn{ci}_{idx}"))
                idx += 1
            for p in cur_paras:
                if p:
                    segs.append(_seg(idx, p, KIND_TEXT, f"tn{ci}_{idx}"))
                    idx += 1
            if segs:
                chapters.append({"index": ci, "title": cur_title or f"第{ci + 1}章",
                                 "segments": segs, "href": None, "template": None})
        cur_title, cur_paras = "", []

    for line in lines:
        if _TXT_CHAPTER_RE.match(line):
            flush_chapter()
            cur_title = line.strip()
        elif not line.strip():
            flush_para()
        else:
            buf.append(line)
    flush_chapter()

    return {"title": os.path.splitext(os.path.basename(path))[0], "fmt": "text",
            "source_path": os.path.abspath(path), "chapters": chapters, "meta": {}}


# ── 非 EPUB 输入的预处理 ─────────────────────────────────────────────────────

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _slug(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|\s]+", "_", text).strip("_")
    return text[:60] or "article"


def _pandoc_to_epub(src: str, out_epub: str, title: str | None = None) -> None:
    if not shutil.which("pandoc"):
        raise SystemExit("需要 pandoc：brew install pandoc")
    cmd = ["pandoc", src, "-o", out_epub, "--split-level=1", "--toc", "--epub-title-page=false"]
    if title:
        cmd += ["--metadata", f"title={title}"]
    cmd += ["--resource-path", os.path.dirname(os.path.abspath(src))]
    subprocess.run(cmd, check=True)


def _extract_article_html(raw: str, url: str) -> tuple[str, str]:
    """网页 → (标题, 只含正文的 HTML)。启发式：<article> > <main> > 文字最多的容器。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    for bad in soup.find_all(["script", "style", "noscript", "nav", "footer", "aside",
                              "form", "iframe", "button", "svg"]):
        bad.decompose()
    body = soup.find("article") or soup.find("main")
    if body is None:
        best, best_len = None, 0
        for cand in soup.find_all(["div", "section"]):
            n = sum(len(p.get_text(strip=True)) for p in cand.find_all("p", recursive=False))
            if n > best_len:
                best, best_len = cand, n
        body = best or soup.body or soup
    for img in body.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            img["src"] = urllib.request.urljoin(url, src)
    html = (f"<html><head><meta charset='utf-8'><title>{title}</title></head><body>"
            f"<h1>{title}</h1>{body.decode_contents()}"
            f"<p><a href='{url}'>{url}</a></p></body></html>")
    return title, html


def prepare_input(src: str, project_dir: str) -> tuple[str, str]:
    """把各种输入统一成 epub/fb2/txt，返回 (待解析文件路径, 原始输入路径或 URL)。"""
    source_dir = os.path.join(project_dir, "source")
    os.makedirs(source_dir, exist_ok=True)

    if re.match(r"https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", "replace")
        title, html = _extract_article_html(raw, src)
        html_path = os.path.join(source_dir, _slug(title) + ".html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        epub = os.path.join(source_dir, _slug(title) + ".epub")
        _pandoc_to_epub(html_path, epub, title)
        print(f"已抓取网页正文 → {html_path}\n⚠️ 网页抓取是启发式的，开工前抽查 chapters/ch00-en.md 是否完整（付费墙/动态加载页面可能缺段）")
        return epub, src

    ext = os.path.splitext(src)[1].lower()
    base = os.path.splitext(os.path.basename(src))[0]
    if ext in (".epub", ".fb2", ".txt", ".text"):
        return src, src
    if ext in (".md", ".markdown", ".html", ".htm", ".docx", ".odt", ".rtf"):
        epub = os.path.join(source_dir, base + ".epub")
        _pandoc_to_epub(src, epub, base)
        return epub, src
    if ext == ".pdf":
        if not shutil.which("pdftotext"):
            raise SystemExit("需要 pdftotext：brew install poppler")
        txt = os.path.join(source_dir, base + ".txt")
        subprocess.run(["pdftotext", "-enc", "UTF-8", src, txt], check=True)
        print("PDF 已抽取为纯文本（项目 source/ 下的 .txt）\n⚠️ PDF 无法还原版式；页眉页脚、页码、"
              "断行连字符较多时，先清理那份 txt，再删掉本项目、用清理后的 txt 重新拆书")
        return txt, src
    raise SystemExit(f"不支持的格式：{ext}")


# ── 主流程 ──────────────────────────────────────────────────────────────────

def _book_version(title: str) -> int:
    """同一本书的下一个版本号：扫描产出根目录和 archive/ 下同名书的文件夹（不分日期），
    无版本后缀的算 V1，取最大值 + 1。全新的书返回 1。"""
    key = re.sub(r"[^a-z0-9]", "", camel_title(title).lower())
    if len(key) < 4:
        return 1
    best = 0
    for root in (OUTPUT_ROOT, os.path.join(OUTPUT_ROOT, "archive")):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if key not in re.sub(r"[^a-z0-9]", "", name.lower()):
                continue
            m = re.search(r"[_-]V(\d+)(?=$|[_-])", name)
            best = max(best, int(m.group(1)) if m else 1)
    return best + 1


def _default_project_dir(title: str) -> str:
    """YYYYMMDD_EnglishTitle[_Vn]；中文书名在风格确认后由 set_title.py 插在版本号前面。"""
    ver = _book_version(title)
    name = f"{time.strftime('%Y%m%d')}_{camel_title(title)}" + (f"_V{ver}" if ver > 1 else "")
    path = os.path.join(OUTPUT_ROOT, name)
    while os.path.exists(path):  # 极端情况兜底
        ver += 1
        path = os.path.join(OUTPUT_ROOT, f"{time.strftime('%Y%m%d')}_{camel_title(title)}_V{ver}")
    return path


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    project_dir = sys.argv[2] if len(sys.argv) >= 3 else None
    if project_dir and Store(project_dir).exists():
        print(f"{project_dir} 已有 state/manifest.json —— 项目已拆过书。"
              f"如需重拆，先手动删除 state/ 和 chapters/（会丢失已有译文状态）。")
        return 1

    stage = tempfile.mkdtemp(prefix="wenyi-ingest-")
    try:
        book_path, origin = prepare_input(src, stage)
        ext = os.path.splitext(book_path)[1].lower()
        if ext == ".epub":
            doc = read_epub(book_path)
        elif ext == ".fb2":
            doc = read_fb2(book_path)
        else:
            doc = read_txt(book_path)
        if not doc["chapters"]:
            print("未解析出任何章节，请检查源文件")
            return 1

        if not project_dir:
            project_dir = _default_project_dir(doc["title"])
        # 原始输入和中间产物备份进 source/（EPUB 模板回填从这份副本读取）
        source_dir = os.path.join(project_dir, "source")
        os.makedirs(source_dir, exist_ok=True)
        for name in os.listdir(os.path.join(stage, "source")) if os.path.isdir(os.path.join(stage, "source")) else []:
            shutil.copy2(os.path.join(stage, "source", name), os.path.join(source_dir, name))
        local_copy = os.path.join(source_dir, os.path.basename(book_path))
        if not os.path.exists(local_copy):
            shutil.copy2(book_path, local_copy)
        # 原始输入文件本身也复制一份（md/docx/html/pdf 时上面只有转换产物）；源文件只读，绝不改动
        if not re.match(r"https?://", src):
            orig_copy = os.path.join(source_dir, os.path.basename(src))
            if not os.path.exists(orig_copy):
                shutil.copy2(src, orig_copy)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    store = Store(project_dir, create=True)
    is_url = bool(re.match(r"https?://", origin))
    manifest = {
        "title": doc["title"],
        "fmt": doc["fmt"],
        "title_zh": "",
        "source_path": os.path.relpath(local_copy, project_dir),
        "origin_path": origin if is_url else os.path.abspath(origin),
        "output_dir": ".",
        "target_lang": "zh",
        "quote_style": DEFAULT_QUOTE_STYLE,
        "meta": doc["meta"],
        "chapters": [
            {"index": c["index"], "title": strip_tags(c["title"]), "title_translated": "",
             "href": c["href"], "status": STATUS_PENDING,
             "segments_total": len(c["segments"])}
            for c in doc["chapters"]
        ],
    }
    store.save_manifest(manifest)

    md_dir = os.path.join(project_dir, "chapters")
    os.makedirs(md_dir, exist_ok=True)
    width = pad_width(len(doc["chapters"]))
    n_words = 0
    n_inline = 0
    lines = []
    for c in doc["chapters"]:
        store.save_chapter(c)
        with open(os.path.join(md_dir, md_name(c["index"], width, "en")),
                  "w", encoding="utf-8") as f:
            f.write(render_marked_md(c))
        w = sum(len(strip_tags(s["source"]).split()) for s in c["segments"])
        n_words += w
        n_inline += sum(len(s.get("inline") or {}) for s in c["segments"])
        lines.append(f"  ch{c['index']:0{width}d}  {w:>6} 词  {len(c['segments']):>4} 段  {strip_tags(c['title'])[:50]}")

    n_seg = sum(len(c["segments"]) for c in doc["chapters"])
    print(f"《{doc['title']}》[{doc['fmt']}] 共 {len(doc['chapters'])} 章 / {n_seg} 段 / 约 {n_words} 词"
          f" / 段内格式标签 {n_inline} 个")
    print("\n".join(lines))
    if STRAY_TEXT:
        print(f"\n⚠️ {len(STRAY_TEXT)} 处容器内散落文本未纳入翻译（导出时保持原文），例：")
        for t in STRAY_TEXT[:5]:
            print(f"   {t}")
    print(f"\n项目目录：{os.path.abspath(project_dir)}")
    print(f"翻译用 markdown：{md_dir}/chNN-en.md（译文写入同目录 chNN-zh.md，保留 ⟦N⟧ 与 <N> 标签）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

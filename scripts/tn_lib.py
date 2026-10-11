"""wenyi-max 共享库：状态存取 + 段内格式占位符 + 段落标记解析 + 标点规范化。

状态目录结构（project/state/）：
  manifest.json        书籍元信息 + 各章状态（含 title_translated）
  chapters/chN.json    各章数据：segments（index/source/target/kind/anchor/inline）+ template

段内格式占位符（wenyi-max 新增）：
  原书段落里的斜体、粗体、链接、span 等行内元素在 source 中表示为成对数字标签
  `<1>…</1>`；脚注号、图片、换行等不可翻译的原子元素表示为 `<2/>`。
  元素原貌（标签名 + 属性，或原子元素的完整 HTML）存在 segment["inline"]，
  导出时按译文中的标签位置重建，斜体/链接/脚注跳转全部还原。

移植自 wenyi (github.com/BigDawnGhost/wenyi, MIT)：
  - normalize_zh：译文标点统一为简体中文大陆全角标点（引号风格可选 corner「」/ curly“”）
  - Store：manifest / 章节 JSON 的原子读写
"""

from __future__ import annotations

import json
import os
import re

KIND_TEXT = "text"
KIND_HEADING = "heading"

STATUS_PENDING = "pending"
STATUS_DONE = "done"

# 默认产出根目录：每本书在此建一个 YYYYMMDD_EnglishTitle_中文书名[_Vn] 文件夹。
# 优先级：环境变量 WENYI_OUTPUT_ROOT > 当前目录下的 wenyi-output/


def _default_output_root() -> str:
    env = os.environ.get("WENYI_OUTPUT_ROOT")
    if env:
        return os.path.expanduser(env)
    return os.path.join(os.getcwd(), "wenyi-output")


OUTPUT_ROOT = _default_output_root()

# ── 状态存取 ──────────────────────────────────────────────────────────────


class Store:
    """project/state/ 下的 manifest 与章节 JSON 读写（原子替换防写坏）。"""

    def __init__(self, project_dir: str, *, create: bool = False):
        self.project_dir = project_dir
        self.state_dir = os.path.join(project_dir, "state")
        self.chapters_dir = os.path.join(self.state_dir, "chapters")
        if create:
            os.makedirs(self.chapters_dir, exist_ok=True)

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.state_dir, "manifest.json")

    def chapter_path(self, ci: int) -> str:
        return os.path.join(self.chapters_dir, f"ch{ci}.json")

    @staticmethod
    def _write_json(path: str, data) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    @staticmethod
    def _read_json(path: str):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def resolve(self, path: str) -> str:
        """manifest 里的路径相对项目目录保存（项目文件夹改名后仍有效）。"""
        if not path or re.match(r"https?://", path) or os.path.isabs(path):
            return path
        return os.path.normpath(os.path.join(self.project_dir, path))

    def exists(self) -> bool:
        return os.path.isfile(self.manifest_path)

    def save_manifest(self, manifest: dict) -> None:
        self._write_json(self.manifest_path, manifest)

    def load_manifest(self) -> dict:
        return self._read_json(self.manifest_path)

    def save_chapter(self, chapter: dict) -> None:
        self._write_json(self.chapter_path(chapter["index"]), chapter)

    def load_chapter(self, ci: int) -> dict:
        return self._read_json(self.chapter_path(ci))


def pad_width(n_chapters: int) -> int:
    return max(2, len(str(max(n_chapters - 1, 0))))


def md_name(ci: int, width: int, side: str) -> str:
    return f"ch{ci:0{width}d}-{side}.md"


def camel_title(title: str, limit: int = 40) -> str:
    """'The Star Fraction' → 'TheStarFraction'（文件夹命名用）。"""
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    out = "".join(w[:1].upper() + w[1:] for w in words)
    return out[:limit] or "Untitled"


def parse_chapter_arg(arg: str) -> int | None:
    m = re.fullmatch(r"ch?(\d+)", arg)
    return int(m.group(1)) if m else None


# ── 段内格式占位符 ─────────────────────────────────────────────────────────

TAG_RE = re.compile(r"<(\d+)/>|<(\d+)>|</(\d+)>")

# 原子元素：内容不可翻译，整体保留
_ATOMIC_TAGS = {"br", "img", "wbr", "hr", "svg", "math", "image", "object", "video", "audio"}
_NOTE_TEXT_RE = re.compile(r"^[\s\d*†‡§¶#\[\]()a-zA-Z]{0,4}$")


def _is_note_ref(el) -> bool:
    """脚注/尾注引用：epub:type=noteref，或内容是极短编号的 <a>/<sup>。"""
    etype = el.get("epub:type") or el.get("role") or ""
    if isinstance(etype, list):
        etype = " ".join(etype)
    if "noteref" in etype:
        return True
    if el.name in ("a", "sup", "sub"):
        text = el.get_text().strip()
        if text and _NOTE_TEXT_RE.match(text) and not re.search(r"[a-zA-Z]{2,}", text):
            href = el.get("href") or ""
            if el.name in ("sup", "sub") or "#" in href:
                return True
    return False


def _flat_attrs(attrs: dict) -> dict:
    return {k: (" ".join(v) if isinstance(v, list) else v) for k, v in attrs.items()}


def serialize_inline(el) -> tuple[str, dict]:
    """把块级元素的内容序列化为带数字标签的文本，返回 (text, inline_registry)。"""
    from bs4 import Comment, NavigableString, Tag

    registry: dict[str, dict] = {}
    counter = [0]

    def walk(node) -> str:
        out = []
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                out.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            inner_text = child.get_text()
            if child.name in _ATOMIC_TAGS or _is_note_ref(child) or not inner_text.strip():
                if not inner_text.strip() and child.name not in _ATOMIC_TAGS \
                        and not child.attrs and not child.find(True):
                    out.append(inner_text)  # 无属性空壳，如 <span> </span>
                    continue
                counter[0] += 1
                tid = str(counter[0])
                registry[tid] = {"html": str(child)}
                out.append(f"<{tid}/>")
                continue
            if child.name == "span" and not child.attrs:
                out.append(walk(child))  # 无属性 span：解包
                continue
            counter[0] += 1
            tid = str(counter[0])
            registry[tid] = {"tag": child.name, "attrs": _flat_attrs(dict(child.attrs))}
            out.append(f"<{tid}>{walk(child)}</{tid}>")
        return "".join(out)

    text = walk(el)
    text = re.sub(r"[ \t\r\n\f\v]+", " ", text).strip()
    return text, registry


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text or "")


def tag_tokens(text: str) -> list[tuple[str, str]]:
    """返回 [(kind, id)]，kind ∈ atom/open/close。"""
    out = []
    for m in TAG_RE.finditer(text or ""):
        if m.group(1):
            out.append(("atom", m.group(1)))
        elif m.group(2):
            out.append(("open", m.group(2)))
        else:
            out.append(("close", m.group(3)))
    return out


def check_tags(source: str, target: str, registry: dict) -> tuple[list[str], list[str]]:
    """校验译文中的格式标签。返回 (errors, warnings)。

    error：标签未闭合/交叉嵌套、出现原文没有的标签号、原子元素（脚注号/图片）丢失。
    warning：成对格式标签丢失或重复（只影响斜体/链接等样式，不丢内容）。
    """
    registry = registry or {}
    errors: list[str] = []
    warnings: list[str] = []
    stack: list[str] = []
    seen_open: dict[str, int] = {}
    seen_atom: dict[str, int] = {}
    for kind, tid in tag_tokens(target):
        if tid not in registry:
            errors.append(f"出现原文没有的标签 <{tid}>")
            continue
        is_atom = "html" in registry[tid]
        if kind == "atom":
            if not is_atom:
                errors.append(f"<{tid}/> 在原文是成对标签，应写成 <{tid}>…</{tid}>")
            seen_atom[tid] = seen_atom.get(tid, 0) + 1
        elif kind == "open":
            if is_atom:
                errors.append(f"<{tid}> 在原文是单独标签，应写成 <{tid}/>")
            stack.append(tid)
            seen_open[tid] = seen_open.get(tid, 0) + 1
        else:
            if not stack or stack[-1] != tid:
                errors.append(f"</{tid}> 闭合顺序错误（未打开或与其他标签交叉）")
            else:
                stack.pop()
    if stack:
        errors.append("标签未闭合：" + "、".join(f"<{t}>" for t in stack))

    src_tokens = tag_tokens(source)
    src_atoms = {t for k, t in src_tokens if k == "atom"}
    src_pairs = {t for k, t in src_tokens if k == "open"}
    for t in sorted(src_atoms - set(seen_atom), key=int):
        errors.append(f"丢失原子标签 <{t}/>（脚注号/图片/换行，必须保留）")
    for t in sorted(src_pairs - set(seen_open), key=int):
        warnings.append(f"丢失格式标签 <{t}>…</{t}>（{registry.get(t, {}).get('tag', '?')}）")
    for t, n in seen_open.items():
        if n > 1:
            warnings.append(f"格式标签 <{t}> 出现 {n} 次")
    return errors, warnings


def build_inline(soup, parent, text: str, registry: dict) -> None:
    """把带标签的译文还原为 DOM 节点追加到 parent。标签结构异常时退化为纯文本。"""
    from bs4 import BeautifulSoup

    registry = registry or {}
    try:
        stack = [(None, parent)]
        pos = 0
        used_ids: set[str] = set()
        for m in TAG_RE.finditer(text):
            if m.start() > pos:
                stack[-1][1].append(text[pos:m.start()])
            pos = m.end()
            if m.group(1):
                reg = registry.get(m.group(1))
                if reg and "html" in reg:
                    frag = BeautifulSoup(reg["html"], "html.parser")
                    for node in list(frag.contents):
                        stack[-1][1].append(node.extract())
            elif m.group(2):
                tid = m.group(2)
                reg = registry.get(tid)
                if not reg or "tag" not in reg:
                    raise ValueError(tid)
                attrs = dict(reg.get("attrs") or {})
                if tid in used_ids:
                    attrs.pop("id", None)  # 重复使用同一标签时避免 id 冲突
                used_ids.add(tid)
                new = soup.new_tag(reg["tag"], attrs=attrs)
                stack[-1][1].append(new)
                stack.append((tid, new))
            else:
                if stack[-1][0] != m.group(3):
                    raise ValueError(m.group(3))
                stack.pop()
        if pos < len(text):
            stack[-1][1].append(text[pos:])
        if len(stack) != 1:
            raise ValueError("unclosed")
    except ValueError:
        parent.clear()
        parent.append(strip_tags(text))


# ── 段落标记（⟦N⟧）───────────────────────────────────────────────────────
# chapters/chNN-en.md 中每个可翻译段以 ⟦N⟧ 开头；译文 chNN-zh.md 保持同样
# 标记。⟦title⟧ 行（可选，放最前）给出本章标题译名，用于目录回填。

_MARKER_RE = re.compile(r"^⟦(\d+|title)⟧\s?", re.M)


def render_marked_md(chapter: dict) -> str:
    """把章节 segments 渲染为带标记的 markdown（供翻译）。"""
    parts = []
    for s in chapter["segments"]:
        text = s["source"]
        prefix = "## " if s["kind"] == KIND_HEADING else ""
        parts.append(f"⟦{s['index']}⟧ {prefix}{text}")
    return "\n\n".join(parts) + "\n"


def parse_marked_md(text: str) -> tuple[dict[int, str], str | None]:
    """解析带标记的 markdown，返回 ({segment_index: 文本}, 标题译名或 None)。

    一个标记的内容从标记处延伸到下一个标记（或文末），因此段内换行安全。
    heading 前缀 '## ' 会被剥掉（渲染时加的，不属于译文）。
    """
    result: dict[int, str] = {}
    title: str | None = None
    matches = list(_MARKER_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.end():end].strip()
        chunk = re.sub(r"^#{1,6}\s+", "", chunk)
        if m.group(1) == "title":
            title = chunk
        else:
            result[int(m.group(1))] = chunk
    return result, title


# ── 标点规范化（移植自 wenyi postprocess/punct.py）───────────────────────
# 统一为简体中文大陆通用全角标点。策略保守：半角标点仅在紧邻 CJK 时转全角，
# 不误伤英文/数字串内部（如 9.11、Mr. Smith）。格式标签 <N> 视为透明。

_CJK = (
    "一-鿿"      # CJK 统一汉字
    "぀-ヿ"      # 假名（保险）
    "＀-￯"      # 全角符号
    "“”‘’「」『』（）《》【】、，。！？：；…—"
)
_CJK_RE = f"[{_CJK}]"
_TAGS_OPT = r"(?:</?\d+/?>)*"

_HALF_TO_FULL = {",": "，", ".": "。", "!": "！", "?": "？", ":": "：", ";": "；"}


QUOTE_STYLES = ("corner", "curly")
DEFAULT_QUOTE_STYLE = "corner"   # 用户约定：中文引号用「」『』（见 user-prefs.md）
_APOS = "\uE000"                 # 撇号占位符，避免被当成单引号配对


def _convert_quotes(text: str, style: str = DEFAULT_QUOTE_STYLE) -> str:
    """统一引号。corner：外层「」、内层『』；curly：外层“”、内层‘’。

    已有的全角引号按开/合一一映射（跨段引语只有开引号也不会错配）；
    半角 " ' 交替配对；被字母包夹的 ' ’ 视为撇号，保留为 ’。
    """
    if style not in QUOTE_STYLES:
        raise ValueError(f"未知引号风格 {style!r}，可选 {QUOTE_STYLES}")
    dq_open, dq_close, sq_open, sq_close = ("「", "」", "『", "』") if style == "corner" else ("“", "”", "‘", "’")

    text = re.sub(r"(?<=[A-Za-z])['’](?=[A-Za-z])", _APOS, text)
    text = text.translate(str.maketrans({
        "「": dq_open, "“": dq_open, "」": dq_close, "”": dq_close,
        "『": sq_open, "‘": sq_open, "』": sq_close, "’": sq_close,
    }))

    out = []
    open_dq = True
    for ch in text:
        if ch == '"':
            out.append(dq_open if open_dq else dq_close)
            open_dq = not open_dq
        else:
            out.append(ch)
    text = "".join(out)

    out, open_sq = [], True
    for ch in text:
        if ch == "'":
            out.append(sq_open if open_sq else sq_close)
            open_sq = not open_sq
        else:
            out.append(ch)
    return "".join(out).replace(_APOS, "’")


def _convert_ellipsis_dash(text: str) -> str:
    text = re.sub(r"。{3,}", "……", text)
    text = re.sub(r"・{2,}", "……", text)
    text = re.sub(r"\.{3,}", "……", text)
    text = re.sub(r"…+", "……", text)
    text = re.sub(r"(?<!-)-{2,3}(?!-)", "——", text)
    text = re.sub(r"—{1,}", "——", text)
    text = re.sub(r"——(——)+", "——", text)
    return text


def _convert_halfwidth(text: str) -> str:
    def repl(m: re.Match) -> str:
        return m.group(1) + _HALF_TO_FULL[m.group(2)]

    after_cjk = re.compile(rf"({_CJK_RE}{_TAGS_OPT})([,.!?:;])")
    text = after_cjk.sub(repl, text)
    before_cjk = re.compile(rf"()([,.!?:;])(?={_TAGS_OPT}{_CJK_RE})")
    return before_cjk.sub(repl, text)


def _convert_parens(text: str) -> str:
    """包裹中文或紧跟中文的半角括号转全角；纯英文括注保持半角。"""
    def repl(m: re.Match) -> str:
        inner = m.group(1)
        before = text[max(0, m.start() - 1):m.start()]
        if re.search(_CJK_RE, inner) or (before and re.match(_CJK_RE, before)):
            return f"（{inner}）"
        return m.group(0)

    return re.sub(r"\(([^()]*)\)", repl, text)


def normalize_zh(text: str, quote_style: str = DEFAULT_QUOTE_STYLE) -> str:
    """把一段中文译文的标点规范化为简体中文通用全角标点。quote_style 见 _convert_quotes。"""
    if not text:
        return text
    text = _convert_quotes(text, quote_style)
    text = _convert_ellipsis_dash(text)
    text = _convert_halfwidth(text)
    text = _convert_parens(text)
    text = re.sub(rf"([，。！？：；、”’」』》】]{_TAGS_OPT})[ \t]+", r"\1", text)
    return text


def count_cjk(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text or ""))


def count_en_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", strip_tags(text or "")))

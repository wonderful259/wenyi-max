#!/usr/bin/env python3
"""项目进度一览 + 断点续跑指引。

用法：
  python3 status.py <project_dir>

根据盘上文件推断流水线进度：
  拆书        state/manifest.json
  全书理解    state/digests/chNN.md、state/book-overview.md
  风格确认    state/style-guide.md + state/STYLE_CONFIRMED
  翻译        manifest 中各章 status=done
  审校        reviews/chNN.md（每章审校记录）
  导出        manifest["exports"]
并打印下一步该做什么。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import STATUS_DONE, Store, pad_width, strip_tags  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    project_dir = sys.argv[1]
    store = Store(project_dir)
    if not store.exists():
        print("尚未拆书 → 下一步：python3 scripts/ingest.py <输入> [<project_dir>]")
        return 0
    m = store.load_manifest()
    chapters = m["chapters"]
    n = len(chapters)
    width = pad_width(n)
    st = store.state_dir
    digests = [c for c in chapters
               if os.path.isfile(os.path.join(st, "digests", f"ch{c['index']:0{width}d}.md"))]
    overview = os.path.isfile(os.path.join(st, "book-overview.md"))
    recurring = os.path.isfile(os.path.join(st, "recurring-candidates.md"))
    style = os.path.isfile(os.path.join(st, "style-guide.md"))
    confirmed = os.path.isfile(os.path.join(st, "STYLE_CONFIRMED"))
    done = [c for c in chapters if c["status"] == STATUS_DONE]
    reviewed = [c for c in chapters if os.path.isfile(
        os.path.join(project_dir, "reviews", f"ch{c['index']:0{width}d}.md"))]

    print(f"《{m['title']}》 {n} 章")
    print(f"  全书理解：梗概 {len(digests)}/{n}，概览 {'✓' if overview else '✗'}，"
          f"重复句扫描 {'✓' if recurring else '✗'}")
    print(f"  引号风格：{m.get('quote_style') or 'corner'}")
    print(f"  风格确认：{'✓ 已确认' if confirmed else ('待确认（风格提案已生成）' if style else '✗')}")
    print(f"  翻译：{len(done)}/{n} 章")
    print(f"  审校：{len(reviewed)}/{n} 章")
    for e in m.get("exports", []):
        print(f"  已导出：{e}")

    if len(digests) < n or not overview:
        todo = [f"ch{c['index']:0{width}d}" for c in chapters if c not in digests]
        print(f"\n下一步：阶段 1 全书理解（缺梗概：{', '.join(todo[:10])}{'…' if len(todo) > 10 else ''}）")
    elif not recurring:
        print("\n下一步：阶段 1 收尾——运行 recurring_phrases.py 扫重复句/关键短语，阶段 2 建术语表时锁定")
    elif not confirmed:
        print("\n下一步：阶段 2/3 风格提案 → 等用户确认")
    elif len(done) < n:
        nxt = next(c for c in chapters if c["status"] != STATUS_DONE)
        print(f"\n下一步：阶段 4 翻译 ch{nxt['index']:0{width}d}《{strip_tags(nxt['title'])}》"
              f"（翻译必须串行，从这一章继续）")
    elif len(reviewed) < n:
        todo = [f"ch{c['index']:0{width}d}" for c in chapters if c not in reviewed]
        print(f"\n下一步：阶段 5 审校（未审：{', '.join(todo[:10])}{'…' if len(todo) > 10 else ''}）")
    elif not m.get("exports"):
        print("\n下一步：阶段 6 导出 + 校验")
    else:
        print("\n全部完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

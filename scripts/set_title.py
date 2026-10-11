#!/usr/bin/env python3
"""定中文书名：写入 manifest，并把项目文件夹改名为 YYYYMMDD_EnglishTitle_中文书名[_Vn]。

用法：
  python3 set_title.py <project_dir> <中文书名>

- 中文书名会用于：导出文件名（《中文书名》译文.epub）、EPUB 元数据书名。
- 文件夹名已经带中文书名后缀（重复执行或改名）时，替换旧后缀。
- 改名后打印新的项目路径，之后的命令都用新路径。
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tn_lib import Store  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    project_dir = os.path.abspath(sys.argv[1].rstrip("/"))
    title_zh = sys.argv[2].strip().strip("《》")
    if not title_zh or re.search(r'[\\/:*?"<>|]', title_zh):
        print("中文书名为空或含有不能用于文件名的字符")
        return 2

    store = Store(project_dir)
    manifest = store.load_manifest()
    manifest["title_zh"] = title_zh
    store.save_manifest(manifest)

    parent, name = os.path.split(project_dir)
    m = re.match(r"^(\d{8}_[^_]+)(?:_.+)?$", name)
    if not m:
        print(f"已记录中文书名《{title_zh}》；文件夹名 {name} 不是 YYYYMMDD_EnglishTitle 格式，不改名")
        print(project_dir)
        return 0
    stem = m.group(1)
    # 版本号放在最后：保留 _V3 之类的后缀，替换旧的中文书名
    vm = re.search(r"_V\d+$", name)
    ver = vm.group(0) if vm else ""
    new_name = f"{stem}_{title_zh}{ver}"
    new_dir = os.path.join(parent, new_name)
    if new_dir != project_dir:
        if os.path.exists(new_dir):
            print(f"目标文件夹已存在：{new_dir}，未改名")
            print(project_dir)
            return 1
        os.rename(project_dir, new_dir)
    print(f"已记录中文书名《{title_zh}》")
    print(new_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

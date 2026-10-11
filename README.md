# wenyi-max · 英译中全流程翻译 Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

给一本英文书或一篇文章，产出一个**格式还原、经过校验的中文 EPUB**。全程自动，只在「风格确认」时停下来问你一次。

适用于任何能读写文件、能运行 shell 命令的 agent（Claude Code、Codex 等）。翻译和审校由 agent 背后的大模型直接完成，不调用外部机器翻译 API。

- 输入：EPUB、FB2、TXT、Markdown、HTML、DOCX、PDF，或文章网址
- 输出：按原书模板回填的中文 EPUB（样式、图片、目录、斜体、链接、脚注保留），可选中英对照版或纯文本
- 擅长：科幻小说、奇幻小说、非虚构科技报道

## 这是两个项目的合体

wenyi-max 把两个 MIT 开源项目的长处装进同一个 skill：

| 来源 | 提供什么 |
|---|---|
| [wenyi](https://github.com/BigDawnGhost/wenyi)（BigDawnGhost） | 工程骨架：EPUB 模板锚点回填、段落对齐硬校验、全书理解、上下文注入、术语库、审校—修订循环 |
| [en-zh-max](https://github.com/3060226349kk-cmd/en-zh-max)（3060226349kk-cmd） | 翻译方法论：叶子南《高级英汉翻译理论与实践》的文本画像、意合优先、翻译腔诊断、准确性六项校验，以及相关参考资料 |
| wenyi-max 自己新增 | 段内格式保留（斜体/链接/脚注号）、中英对照导出、机械质检扫描、重复句锁定、EPUB 交付校验、三份文类指南、中文终审清单、断点续跑、长期偏好记忆 |

wenyi 是一个自己调用 LLM API 的桌面/Web/CLI 应用；en-zh-max 是纯方法论 skill。wenyi-max 取 wenyi 的流水线思路和 EPUB 处理，取 en-zh-max 的方法论，改写成由 agent 驱动的 skill，所以它们**不是**同一个东西的不同版本：

| | wenyi | en-zh-max | wenyi-max |
|---|---|---|---|
| 谁来翻译 | 应用调用配置的 LLM API | agent | agent |
| 整书 EPUB 格式还原 | 有 | 无（输出中英对照 md，可派生 EPUB） | 有 |
| 全书理解与术语库 | 有 | 无 | 有（脚本 + 文件落盘） |
| 翻译方法论 | 提示词内置 | 叶子南体系 | 叶子南体系 |
| 润色、Best-of-three、证据式整本审校与自动修复 | 有 | 无 | 无（改为逐段对照审校 + 二轮盲审） |
| 多模型路由、SRT 字幕、桌面与 Web 界面 | 有 | 无 | 无 |
| 分会话交接（`handoff.py`）、PDF/MOBI 输出 | 无 | 有 | 无（用 `status.py` 断点续跑） |

## 流水线

| 阶段 | 做什么 | 人工？ |
|---|---|---|
| 0 拆书 | 解析输入，生成带 `⟦N⟧` 段落标记和 `<N>` 格式标签的原文 | 自动 |
| 1 全书理解 | 逐章梗概、全书概览、专名清单、重复句扫描 | 自动 |
| 2 风格提案 | 文本画像、风格指南、术语表草案、样章试译（A/B 两版） | 自动 |
| **3 风格确认** | **把提案给你看，等你拍板** | **唯一的人工节点** |
| 4 逐章翻译 | 串行翻译，每章回填并硬校验，术语表滚动更新 | 自动 |
| 5 全书校验 | 术语一致性、机械质检、逐段对照审校、二轮盲审 | 自动 |
| 6 导出与验收 | 按原书模板导出 EPUB，结构校验，epubcheck 对比 | 自动 |
| 7 交付 | 翻译报告 + 文件路径 | 自动 |

短篇（≤ 1.5 万词）在主对话里完成；长篇用子代理并行做梗概和审校，串行做翻译。环境不支持子代理时，主会话按同样步骤自己做。

完整说明见 [SKILL.md](SKILL.md)。

## 安装

把仓库克隆到 agent 的 skills 目录，目录名保持 `wenyi-max`：

```bash
cd ~/.claude/skills/        # 或你的 agent 的 skills 目录
git clone https://github.com/wonderful259/wenyi-max.git wenyi-max
```

**依赖**

- Python 3
- `beautifulsoup4` 和 `lxml`：`pip3 install beautifulsoup4 lxml`
- 可选：`pandoc`（处理 md/html/docx/网址）、`pdftotext`（处理 PDF）、`epubcheck`（交付校验，缺它只会跳过这一项）

## 使用

对 agent 说「帮我把这本 epub 翻成中文」，附上文件路径或网址即可。agent 会读取 `SKILL.md` 并按流水线执行，到风格确认时停下来问你。

中断后重新开始，运行下面的命令就能看到进度和下一步：

```bash
python3 scripts/status.py <项目目录>
```

## 产出目录与命名

- 产出根目录由环境变量 `WENYI_OUTPUT_ROOT` 指定，缺省是**运行命令时当前目录**下的 `wenyi-output/`。长期使用请设成固定位置。
- 每本书一个项目文件夹：`YYYYMMDD_EnglishTitle_中文书名[_Vn]/`。版本号按书计、放在最后，同一本书再翻会自动取下一个版本号（`$ROOT/archive/` 里的旧项目也算）。
- 成品放在项目文件夹根部：`《中文书名》译文.epub`、`《中文书名》中英对照.epub`、`风格试译.md`、`翻译报告.md`。同名已存在时递增版本号，不覆盖旧版。
- 想给导出的 EPUB 文件名加统一后缀：设环境变量 `WENYI_SUFFIX`（如 `WENYI_SUFFIX=draft` → `《书名》译文_draft.epub`）。
- 源文件只读：拆书时复制到项目的 `source/`，原文件不会被移动或修改。

## 仓库结构

```
wenyi-max/
├── SKILL.md              # 运行时指令：流水线、格式协议、命名约定
├── user-prefs.md         # 跨书的长期偏好（发布时为空模板，你自己的积累只存本地）
├── references/           # 方法论、文类指南、终审清单、子代理提示词模板、语域词表等
├── scripts/              # 拆书、回填、术语检查、质检、导出、校验、进度
├── licenses/             # 两个上游项目的 MIT 许可证全文
├── NOTICE                # 来源与署名明细
└── LICENSE
```

## 已知限制

- PDF 输入无法还原版式，只保证文字。
- 网页抓取是启发式的，复杂页面可能缺段或混入杂项。
- 原书块级元素里散落的文字（不在任何段落标签内）不进入翻译，导出时保留原文。
- `<pre>` 预排版文本、图片里的文字不翻译。
- 斜体、链接等格式标签若被译文漏掉，只丢样式不丢文字；脚注号、图片丢失则直接报错。
- 翻译质量取决于所用的模型；机械质检和审校只能降低风险，不能保证零错误。
- 目前没有自动化测试。发布前只用一个最小 EPUB 样例手动跑通了拆书、回填、导出、校验；`epubcheck` 那一步没有跑过。

## 致谢与许可

- [wenyi](https://github.com/BigDawnGhost/wenyi) © BigDawnGhost（MIT）：EPUB 解析、模板回填、标点规范化等思路和实现改写自该项目。
- [en-zh-max](https://github.com/3060226349kk-cmd/en-zh-max)（MIT）：`references/` 中的方法论文件来自该项目，其中 5 份与上游逐字相同。
- 方法论本身来自叶子南《高级英汉翻译理论与实践》（第 4 版，清华大学出版社，2020）。本仓库只含提炼后的工作笔记，不含原书内容。

来源明细见 [NOTICE](NOTICE)，两个上游的许可证全文在 [licenses/](licenses/)。本项目以 [MIT](LICENSE) 发布。

请勿用本工具翻译并公开发布你没有权利再分发的受版权保护文本。

---
name: wenyi-max
license: MIT
description: '英译中全流程翻译流水线，专为科幻小说、奇幻小说和非虚构科技报道设计。输入 EPUB/FB2/TXT/Markdown/HTML/DOCX/PDF 或文章网址，自动完成拆书、全书理解、文本分析与风格提案、（仅此一处请用户确认风格）、逐章串行翻译、术语一致性与机械质检、逐段对照审校、中文润色，最后按原书模板导出还原格式（样式/图片/目录/斜体/链接/脚注）的中文 EPUB，并用 epubcheck 校验。合并了 wenyi（工程流水线）与 en-zh-max（叶子南翻译方法论）。Use when the user wants to translate an English book, novel, sci-fi, fantasy, or tech article/report into Chinese and get an EPUB. Triggers: "翻译这本书", "翻译这篇", "翻译这本小说", "英译中", "translate this book/article into Chinese", "帮我把这个 epub 翻成中文", or whenever the user provides an English EPUB/article for translation.'
---

# wenyi-max · 英译中全流程翻译

一句话：**给一本英文书或一篇文章，产出一个格式还原、经过校验的中文 EPUB。** 全程自动，只在「风格确认」时停下来问用户一次。

- 工程骨架来自 [wenyi](https://github.com/BigDawnGhost/wenyi)：锚点回填原书模板、段落对齐硬校验、全书理解、上下文注入、术语库、审校—修订循环。
- 翻译方法论来自 [en-zh-max](https://github.com/3060226349kk-cmd/en-zh-max)：叶子南《高级英汉翻译理论与实践》的文本画像、意合优先、翻译腔诊断、准确性六维校验。
- 本 skill 新增：段内格式保留（斜体/链接/脚注号）、中英对照导出、机械质检扫描、EPUB 交付校验、文类指南、长期偏好记忆。

```
SKILL=<本 SKILL.md 所在目录>           # 如 ~/.claude/skills/wenyi-max
ROOT=${WENYI_OUTPUT_ROOT:-./wenyi-output}   # 产出根目录；建议设环境变量 WENYI_OUTPUT_ROOT 指向固定位置
P=<项目目录>                           # 每本书一个：$ROOT/YYYYMMDD_EnglishTitle_中文书名[_Vn]/
```

**产出目录与命名约定**：
- 产出根目录由环境变量 `WENYI_OUTPUT_ROOT` 指定，缺省为当前目录下的 `wenyi-output/`。脚本取的是运行时的当前目录，换目录运行会换根目录，所以长期使用请设环境变量。
- **源文件只读，绝不移动、改名、修改**：拆书时复制一份到项目的 `source/`，之后所有步骤都只用这份副本（PDF 抽出的 txt 需要清理时，也只改 `source/` 里的副本）。
- 拆书时在 `$ROOT` 下建 `YYYYMMDD_EnglishTitle[_Vn]/`；阶段 3 定下中文书名后用 `set_title.py` 改名为 `YYYYMMDD_EnglishTitle_中文书名[_Vn]/`（如 `20261007_TheLongTide_长潮`）。
- **版本号按书计、放在最后**：同一本书之前做过（不论日期，`$ROOT` 和 `$ROOT/archive/` 里都算，无版本号的算 V1），新项目自动取下一个版本。例：已有 `20261006_TheLongTide_长潮`、`20261007_TheLongTide_长潮_V2`，新项目就是 `20261007_TheLongTide_长潮_V3/`。第一次翻的书不带版本号。
- 所有产出都在项目文件夹里：成品 EPUB、试译、报告在根部，工作文件在 `source/ chapters/ state/ reviews/`。
- **交付物文件名**：`《中文书名》译文.epub`、`《中文书名》中英对照.epub`、`风格试译.md`、`翻译报告.md`。文件夹带 `_V3` 时文件名也带 `_V3`（如 `《长潮》译文_V3.epub`）；同名已存在时版本号继续递增，不覆盖旧版。需要给导出的 EPUB 文件名加统一后缀时设环境变量 `WENYI_SUFFIX`（如 `WENYI_SUFFIX=draft` → `《长潮》译文_draft.epub`）。
- **模型原生红线**：所有翻译及审校任务必须由当前大模型本身（或继承 `Model: "inherit"` 的 Subagent）直接生成高质量译文，严禁调用任何外部机器翻译 API。

**适用于任何能读写文件、能运行 shell 命令的 agent**（Claude Code、Codex 等）。下文提到的「向用户提问」「派子代理」「发送文件」，用你所在环境的对应能力；没有就按括号里的降级办法做。

**环境检查**（开工前跑一次）：
```bash
python3 -c "import bs4, lxml" || pip3 install beautifulsoup4 lxml
which pandoc pdftotext epubcheck   # 缺 pandoc 则不能处理 md/html/docx/网址；缺 pdftotext 不能处理 PDF；缺 epubcheck 只是跳过该项校验
```

## 总流程

| 阶段 | 做什么 | 人工？ |
|---|---|---|
| 0 拆书 | `ingest.py` 解析输入，生成带 ⟦N⟧ 段落标记和 `<N>` 格式标签的原文 | 自动 |
| 1 全书理解 | 逐章梗概 + 全书概览 + 专名清单 | 自动 |
| 2 风格提案 | 文本画像、风格指南草案、术语表草案、样章试译 | 自动 |
| **3 风格确认** | **把提案给用户看，等用户拍板** | **唯一的人工节点** |
| 4 逐章翻译 | 串行翻译，每章回填并硬校验，术语表滚动更新 | 自动 |
| 5 全书校验 | 术语一致性 → 机械质检 → 逐段对照审校并修订 → 二轮盲审 | 自动 |
| 6 导出与验收 | 按原书模板导出 EPUB → 结构与 epubcheck 校验 | 自动 |
| 7 交付 | 翻译报告 + 文件路径 | 自动 |

**规模分档**（阶段 0 后按总词数决定）：
- **短篇**（≤ 1.5 万词，如单篇报道、短篇小说）：全部在主对话内完成，不派子代理。
- **长篇**（> 1.5 万词）：梗概和审校用并行子代理，翻译用**串行**子代理（一章一个，上一章核验完才派下一章），主代理只做编排和核验。提示词模板见 `references/agent-prompts.md`。
  - 子代理怎么派：Claude Code 用 Agent 工具（general-purpose）；其他 agent 用各自的子任务/子代理机制。
  - **环境不支持子代理时**：主会话按模板里的同样步骤逐章自己做。每章做完立刻落盘、跑 fill；上下文快满时，靠 `status.py` 在新会话里接着做。

**断点续跑**：任何时候中断，运行 `python3 $SKILL/scripts/status.py $P` 看进度和下一步，从那里继续。所有中间产物都在盘上。

---

## 阶段 0 · 拆书

```bash
python3 $SKILL/scripts/ingest.py <输入>          # 自动在 $ROOT 下建项目文件夹，输出里会打印路径
```

- EPUB 直接解析，导出时按原书模板回填，格式还原度最高。
- 原文件复制进 `$P/source/`，原文件本身不动。
- `.md / .html / .docx` 和网址先经 pandoc 转成规范 EPUB 再解析（标题、斜体、链接、脚注、图片都保留）。网址抓取是启发式的：拆完后抽查 `ch00-en.md` 是否完整；付费墙页面抓不全时，请用户另存网页或给 PDF。
- PDF 只能保证文字：`pdftotext` 抽出 txt 后，先清掉页眉页脚、页码、断行连字符，再用 txt 重新拆书。告诉用户 PDF 无法还原版式。

输出会列出每章词数、段数。**核对一下**：非正文页（版权页、目录、"Also by"、索引）分别是哪几章，记下来，阶段 4 要区别处理。

## 阶段 1 · 全书理解

目标：让翻译每一章时都知道全书走向，避免前后矛盾、伏笔译错（wenyi 的 book understanding）。

1. **逐章梗概** → `$P/state/digests/chNN.md`（每章 ≤ 200 字中文）。长篇用 T1 模板并行派子代理（每个负责 3–6 章）；短篇自己写。
2. **全书概览** → `$P/state/book-overview.md`（≤ 500 字）：故事主线/核心论点、主要人物及关系、世界观设定要点、叙事结构。读完全部梗概后写。
3. **专名清单**：汇总 T1 返回的专名，去重，作为阶段 2 术语表的原料。
4. **重复句与关键短语**：`python3 $SKILL/scripts/recurring_phrases.py $P` → `$P/state/recurring-candidates.md`。脚本列出全书反复出现的句子（誓词、咒语、回环句）、长短语和固定搭配。阶段 2 建术语表时逐条过：**读者需要认出「同一个说法」的**（如祝福词 *No foreign magic shall harm them* 在 ch6、ch14、ch18 三处呼应）收进术语表锁定译法；动作套话、普通搭配忽略。分章翻译最容易在这里各译各的（外邦 / 外敌 / 外国），呼应一断，伏笔就没了。

## 阶段 2 · 风格提案

先读：`references/methodology.md`、对应文类指南（`genre-scifi.md` / `genre-fantasy.md` / `genre-nonfiction-tech.md`）、`user-prefs.md`（用户的长期偏好，提案默认遵从）。再读原文样本：开头、中段、结尾各取约 1000 词，外加一段典型对话。

产出三样东西：

**① 风格指南草案** `$P/state/style-guide.md`，按这个结构写：

```markdown
# 风格指南 · 《书名》
## 文本画像
档位: 偏软 | 自由度: 7/10 | 文本类型: 表达型 | 语域: 混合（叙述中性，对话口语） | 语域脏度: 3 | 策略: 流畅优先
## 文类与基调          （2–3 句）
## 叙事               （人称、视角、时态、闪回标记方式）
## 节奏               （句长、分段密度、动作场面与说明段的不同处理）
## 人物声音           （主要人物逐个写：身份、说话习惯、自称、对他人称呼、粗口程度）
## 修辞               （比喻、内心独白、作者标志性的句式——要保留的前景化特征）
## 专项决策           （代词、专名方案、引号、单位、新造词规则、脚注/索引处理……阶段 3 定稿）
## 用户特别要求        （阶段 3 填）
```

**② 术语表草案** `$P/state/glossary.jsonl`，每行一个：
```json
{"en":"Alumata","zh":"阿卢玛塔号","category":"item","note":"星舰，原书斜体","first":"ch08","aliases":[]}
```
category 取 `person|animal|place|org|tech|concept|item|title|fixed|phrase|sentence`。同一 en 只能有一个 zh；改译名时改原条目，不要追加新行。

- **phrase / sentence**：从 `recurring-candidates.md` 挑出的锁定短语和重复句（en 写原文原样，zh 写定稿译法）。`check_terms.py` 会逐段检查：原文有这个说法的每一段，译文必须含锁定译法（合理变体写进 `aliases`）。
- **有字面义的名字**（人名、动物名、物名、地名）：按文类指南「有意义的名字」一节判断，意译优先。条目里加 `"meaning"`（字面义，如 `"finder：找东西的人"`）；确定要音译的加 `"name_mode":"sound"` 并在 note 写理由。`check_terms.py` 会对「英文是普通单词、译名却是纯音译」的条目报警告（Finder→芬德 就是这种）。

**③ 样章试译** `$P/风格试译.md`：从正文里挑 2–3 段最能体现风格的（一段叙述 + 一段对话，科技报道挑导语 + 一段技术说明），给出：
- **A 推荐版**：按风格指南翻译。
- **B 对照版**：在一个关键维度上换一种处理（通常是自由度 ±2：更贴原文 / 更归化；或者对话口语化程度不同）。
- 两版下面各用一句话说明差别在哪。

## 阶段 3 · 风格确认 ★ 唯一的人工节点

在对话里给用户看一份**简洁的**提案（详细内容在文件里，给路径即可）：

1. 一行文本画像（如「太空歌剧，偏软，自由度 7，语域脏度 3，流畅优先」）
2. 风格指南要点 4–6 条
3. 核心术语 10–20 条（人名、地名、关键新造词），用表格
4. 样章 A/B 两版的对比（只贴 1–2 段）
5. 需要用户拍板的专项决策（只列真正有分歧的，如单数 they 的译法、非虚构人名方案、是否保留通行旧译名、索引是否翻译）。以下两项**不用问**，按默认写进专项决策，用户另有要求才改：
   - **引号**：默认「」『』（`user-prefs.md` 通用偏好）。用户要“”‘’时，运行 `python3 $SKILL/scripts/fill_translation.py $P --quotes=curly` 写进 manifest，之后回填都按这个统一。
   - **代词**：跟原文走，不按物种或常识改（原文给木偶、骨狗、AI 用 he/she 就译「他/她」，用 it 就译「它」；同一对象在原文里 it 和 he 之间切换，译文跟着切换）。原文的代词是误导、悬念或笑点的一部分时尤其不能改。
6. 规模与预计耗时（总词数、章数；长篇提示会派多少次子代理），并提供「先译前几章试读」的选项

然后一次问完：方向选 A 还是 B、各项专项决策（最多 4 个问题）。有选择题式的提问工具就用（Claude Code 的 AskUserQuestion），没有就在回复里把问题和选项编号列出来，**停下来等用户回复，不要自己替用户选**。用户也可以写自由意见。

用户确认后：
- 定中文书名（有通行中译名的用通行译名，没有就按风格拟一个，在提案里一并请用户确认），然后：
  `python3 $SKILL/scripts/set_title.py $P <中文书名>` —— 项目文件夹改名，最后一行打印新路径，**之后一律用新路径作 $P**。
- 把决定写进 `style-guide.md` 的「专项决策」「用户特别要求」，同步修改术语表。
- `touch $P/state/STYLE_CONFIRMED`
- 用户表达的**跨书通用**偏好（如「人物对话要更口语」「非虚构人名保留英文」）追加到 `$SKILL/user-prefs.md`，注明日期和书名。
- 告诉用户：「接下来全自动运行，完成后交付。」**此后不再打断用户**，除非遇到无法自行解决的阻塞（输入文件损坏、需要用户提供缺失内容）。

## 阶段 4 · 逐章翻译（串行）

**为什么串行**：每章会产生新术语，下一章必须用更新后的术语表。

对每一章（按顺序）：

```bash
python3 $SKILL/scripts/context_pack.py $P chNN     # 生成上下文包 state/context/chNN.md
```

- **短篇**：自己读上下文包 + `chNN-en.md`，按 T2 模板里的「格式协议」「翻译要求」翻译，写入 `chNN-zh.md`。
- **长篇**：用 T2 模板派一个子代理翻这一章，等它返回。

每章完成后主代理核验：
```bash
python3 $SKILL/scripts/fill_translation.py $P chNN   # 必须 ✅；失败就补齐缺段/修标签后重跑
python3 $SKILL/scripts/check_terms.py $P/chapters $P/state/glossary.jsonl   # 有术语冲突、锁定短语未复用当场解决
```
子代理说「拿不准」的地方，主代理判断并处理；子代理建议修改已有译名的，主代理决定是否改（改了就同步修改已译章节）。子代理报告的锁定短语变体，主代理判断：合理就加进该条 `aliases`，不合理就改回锁定译法。本章新出现、后文还会重复的说法（`recurring-candidates.md` 里有、但阶段 2 没收的），这时补进术语表，下一章起锁定。

**格式协议要点**（细节见 T2）：⟦N⟧ 一一对应不增不减；首行 `⟦title⟧ 章标题译名`；`<N>…</N>` 包住对应译词，`<N/>` 原位保留；不写任何说明文字。`fill_translation.py` 会硬校验段数和标签、自动规范标点。

**非正文章节**：
- 版权页、"Also by"、广告页：`chNN-zh.md` 只写一行 `⟦title⟧ 版权信息`（或相应译名），用 `fill_translation.py $P chNN --lenient` 回填，导出时保留原文。
- 书内目录页：**放到最后**，所有章节标题译完后再按 `⟦title⟧` 的译名翻译，保证一致。
- 索引（Index）：默认保留原文（同上 `--lenient`），除非用户在阶段 3 要求翻译。
- 尾注（Notes）：照常翻译；参考文献条目里的作者、书名、期刊名保留原文。

**试读模式**：用户要求先译前几章的，译完这几章直接跳到阶段 5–6（只审校、导出已译部分，未译章节保留原文），交付后等用户反馈再继续。

## 阶段 5 · 全书校验

按顺序做，**发现问题就改，改完重跑 fill**：

1. **术语一致性**：`check_terms.py`。冲突、撞车、渲染不一致、锁定短语未复用、英文残留全部处理掉；确实该保留英文的词（品牌、型号）加进 `$P/state/allowed-words.txt`。「名字可能有字面义却被音译」的警告逐条判断：该意译的改译名并同步全书，确认音译的在条目加 `"name_mode":"sound"`。
2. **机械质检**：`python3 $SKILL/scripts/qa_scan.py $P` → `$P/reviews/qa-scan.md`。【严重】项（疑似漏译/未译、数量词错译）是审校的必查清单。数量词按原文的精确数译（half a dozen = 六，不是「五六」；fortnight = 两周），原文明显是约数的除外。
3. **逐段对照审校**（wenyi 的 Reviewer + Fixer，配合叶子南六维校验）：按 T3 模板。长篇并行派子代理（每个 1–4 章）；短篇自己做。每章产出 `$P/reviews/chNN.md`。
4. **二轮盲审**：一轮里出现一级问题（理解错误）、或重跑 `qa_scan.py` 后仍有【严重】项的章节，按 T4 再审一遍。二轮后仍未解决的，记入翻译报告，不无限循环。
5. 收尾再跑一次 `check_terms.py` 和 `qa_scan.py`，确认没有新增问题。

## 阶段 6 · 导出与验收

```bash
python3 $SKILL/scripts/assemble_epub.py $P                  # → $P/《中文书名》译文.epub
python3 $SKILL/scripts/verify_epub.py $P <导出的 epub 路径>   # 必须 ✅
```

- 同名文件已存在时版本号自动递增（`_V2`、`_V3`……），不覆盖旧版。
- `verify_epub.py` 检查：原书文件一个不少、XHTML 合法、无标记残留、译文覆盖率、epubcheck 新增错误。不通过就修，修完重新导出。
- 用户想要中英对照版（学英语、校对用）时加 `--bilingual`，输出 `《中文书名》中英对照.epub`。用户没提就在交付时提一句可以生成。
- 也可 `--format txt` 导出纯文本。

## 阶段 7 · 交付

写 `$P/翻译报告.md`：
- 书名、文本画像、风格要点、用户的专项决策
- 规模：章数、段数、原文词数、译文字数
- 核心术语表（前 30 条）
- 校验结果：术语检查、qa_scan 严重/提示项变化、审校问题数（一级/二级）、二轮盲审章节、未解决项（逐条列出段号和原因）
- verify_epub 结果、epubcheck 对比
- 已知限制（见下）和需要用户留意的地方

对话里只给：输出文件路径、报告路径、三五行要点（尤其是未解决项）。环境里有发送文件的工具就把 EPUB 发给用户，没有就给出完整路径。

最后问一句是否归档：把项目文件夹移到 `$ROOT/archive/`。**用户同意才移动；原书留在原处不动。**

---

## 文件布局

```
$ROOT/20261007_TheLongTide_长潮_V3/      ← $P
├── 《长潮》译文_V3.epub
├── 《长潮》中英对照_V3.epub   （按需）
├── 风格试译.md
├── 翻译报告.md
├── source/                 原始输入副本（EPUB 模板回填从这里读）
├── chapters/               chNN-en.md（原文，带标记）/ chNN-zh.md（译文）
├── state/
│   ├── manifest.json       书籍元信息 + 各章状态 + 导出记录 + 引号风格
│   ├── chapters/chN.json   段落、锚点、格式表、模板（脚本维护，勿手改）
│   ├── digests/chNN.md     章节梗概
│   ├── book-overview.md    全书概览
│   ├── style-guide.md      风格指南（用户确认版）
│   ├── STYLE_CONFIRMED     风格确认标记
│   ├── recurring-candidates.md  重复句/关键短语候选（阶段 1 生成）
│   ├── glossary.jsonl      术语表（含锁定短语）
│   ├── allowed-words.txt   允许保留的英文词（可选）
│   └── context/chNN.md     每章上下文包
└── reviews/                qa-scan.md + chNN.md 审校记录
```

manifest 里的路径都是相对项目文件夹保存的，文件夹改名、移到 `archive/` 后照样能继续导出。

## 脚本一览

| 脚本 | 用途 |
|---|---|
| `ingest.py` | 各种输入 → 状态 + 带标记原文 |
| `recurring_phrases.py` | 扫原文重复句、长短语、关键搭配 → 锁定短语候选 |
| `context_pack.py` | 生成单章上下文包（风格/概览/梗概/锁定短语/相关术语/上一章结尾） |
| `fill_translation.py` | 译文回填：段数对齐硬校验 + 格式标签校验 + 标点规范化（引号风格按 manifest，默认「」） |
| `check_terms.py` | 术语冲突/撞车/渲染一致性/锁定短语逐段复用/有义名字被音译/英文残留 |
| `qa_scan.py` | 机械质检：疑似漏译/未译/添译、数量词、的的不休、引号失衡与混用、半角残留、翻译腔统计 |
| `assemble_epub.py` | 按原书模板导出（`--bilingual` 对照版，`--format txt`） |
| `verify_epub.py` | 交付校验：结构、残留、覆盖率、epubcheck 对比 |
| `set_title.py` | 定中文书名：写入元数据并给项目文件夹改名 |
| `status.py` | 进度一览 + 下一步 |

## 参考文件

| 文件 | 何时读 |
|---|---|
| `references/methodology.md` | 阶段 2、4、5 必读：叶子南方法论浓缩 |
| `references/genre-scifi.md` / `genre-fantasy.md` / `genre-nonfiction-tech.md` | 阶段 2 起，按文类读一份 |
| `references/zh-polish-checklist.md` | 阶段 5：翻译腔 + AI 腔终审 |
| `references/agent-prompts.md` | 长篇：T1 梗概 / T2 翻译 / T3 审校 / T4 盲审 模板 |
| `references/translationese-symptoms.md` / `techniques.md` / `text-analysis-and-qa.md` | 遇到具体问题时查 |
| `references/libertine-vocabulary.md` / `literary-fiction-sexual-register.md` | 性/身体直白内容的语域对等 |
| `user-prefs.md` | 阶段 2 读、阶段 3 写 |

## 已知限制

- PDF 输入无法还原版式，只保证文字。
- 网页抓取是启发式的，复杂页面可能缺段或混入杂项。
- 原书块级元素里散落的文字（不在任何段落标签内）不进入翻译，导出时保留原文；`ingest.py` 会提示数量。
- `<pre>` 预排版文本、图片里的文字不翻译。
- 斜体、链接等格式标签若被译文漏掉，只丢样式不丢文字（fill 会警告）；脚注号、图片丢失则直接报错。
- 定了中文书名后，EPUB 元数据书名换成中文；作者等其他元数据保持原文。

## 致谢与许可

- wenyi © BigDawnGhost, MIT —— 解析、回填、导出、标点规范化移植自该项目
- en-zh-max © 3060226349kk-cmd, MIT —— `references/` 下方法论文件来自该项目

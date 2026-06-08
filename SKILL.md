---
name: product-knowledge-base 
description: 支持通过 Git 多人协作维护，用于业务咨询\功能匹配度分析\产品使用介绍\二开指导\应用问题提单
---
# 产品业务知识库

## 概述

这是一个基于本地 Markdown 文件的产品业务知识库，支持通过 Git 多人协作维护，在 QoderWork 中提供对话式智能问答。

**架构说明**：本 Skill 采用"指令层与数据层分离"架构：
- **Skill 仓库**（当前目录）：仅包含 SKILL.md 指令文件
- **知识库仓库**（独立数据仓库）：存储知识条目、索引和图片

## 数据初始化（每次调用前执行）

每次调用本 Skill 时，首先执行智能数据同步。通过时间戳缓存避免不必要的网络请求，提升响应速度。脚本同时会自动检测并拉取 Git LFS 管理的图片资源：

```bash
bash scripts/sync_kb.sh
```

如果 clone 或 pull 失败（网络问题），检查本地是否已有数据：
- 有数据 → 使用本地缓存继续工作，提示"当前使用离线缓存，联网后将自动同步"
- 无数据 → 提示用户检查网络连接，无法执行问答

## 迁移兼容逻辑（仅旧版升级时执行一次）

如果检测到当前 Skill 目录下存在 `knowledge/` 子目录，说明是从旧版升级而来，执行迁移：

```bash
bash scripts/migrate.sh
```

## 知识库目录结构

知识库文件存储在 `~/.product-knowledge-base/knowledge/`：

```
product-knowledge-base/
├── knowledge/             # 知识条目数据根目录
│   ├── _index.md          # 主索引（分类+标题+标签+日期）
│   ├── _tags_index.md     # 标签倒排索引（检索性能关键）
│   ├── _cloud_index.md    # 云索引（按产品族 人才发展云/目标绩效云/人才供应云 分组）
│   ├── product/           # 产品功能
│   │   ├── feature-xxx.md
│   │   └── ...
│   ├── business/          # 业务流程
│   │   ├── process-xxx.md
│   │   └── ...
│   ├── faq/               # 常见问题
│   │   ├── faq-xxx.md
│   │   └── ...
│   ├── guide/             # 操作指南
│   │   ├── guide-xxx.md
│   │   └── ...
│   ├── spec/              # 测试用例规格
│   │   ├── spec-xxx.md
│   │   └── ...
│   └── images/            # 图片资源（Git LFS 管理）
│       ├── xxx.png        # 通用图片
│       ├── product/       # 产品功能配图
│       ├── guide/         # 操作指南配图
│       ├── faq/           # 常见问题配图
│       └── ...
└── CONTRIBUTING.md        # 协作贡献指南
```

## 问答工作流

当用户提出产品/业务相关问题时，按以下优化流程执行：

### Step 1：提取关键词

从用户问题中提取 2-5 个核心关键词/标签词。优先匹配产品术语、功能名称、业务概念。将关键词用 `|` 连接，形成组合检索表达式，例如：`关键词1|关键词2|关键词3`。

**关键词抽取硬约束**：

- **最小长度 ≥ 2**：单个关键词必须 ≥ 2 个字符（中文 ≥ 2 字、英文 ≥ 2 字母）。单字关键词（如「人」「表」「招」「绩」）必须丢弃，否则会因前缀误匹配把整段倒排索引拉进结果。
- **停用词过滤**：剔除以下虚词/疑问词/泛义词后再组装表达式（仅当它们作为独立词出现时过滤）：
  `的、是、在、和、与、或、了、吗、呢、怎么、如何、什么、为何、为什么、哪些、是否、能否、可以、请、问、说一下、介绍、说明、流程、配置、概述、整体、功能`。
- **超级标签拒绝**：如果抽出的关键词正好是「模块级/通用级」标签（如 `spec`、`整体介绍`、`产品概述`、`基础配置`、`FAQ`、`产品功能`、`业务流程`、`常见问题`、`操作指南`、`测试用例规格`），必须替换为更具体的功能/业务名词后再检索；这些超级标签已在 `_tags_index.md` 中被 `rebuild_index.sh` 自动过滤，对它们做倒排检索得不到任何结果。
- **大小写归一**：英文关键词统一转小写后再拼接表达式（倒排索引也已做 `tolower` 归一）。

**云预筛（可选优化）**：

如果用户问句明显指向某一产品族（出现「干部 / 职级 / 人才盘点 / 任职资格 / 能力素质 / 继任 / 认证组」→ 人才发展云；「绩效 / 考核 / 评估 / KPI / PBC / BSC / 校准 / 指标」→ 目标绩效云；「招聘 / 录用 / 候选人 / Offer / 直通车 / 面试」→ 人才供应云），可先 Grep `_cloud_index.md` 拿到该云下的全部文件清单，再把 Step 2 的候选范围与该清单做交集，能进一步压缩 noise。跨云或云无法判定时跳过此步。

### Step 2：阶段一 — 索引检索（双路并行）

同时发起两路索引检索，快速锁定候选范围：

**路径 A — 标签倒排索引检索（权重最高）：**

```
Grep pattern="关键词1|关键词2|关键词3" path="{KB_PATH}/knowledge/_tags_index.md" output_mode="content"
```

**路径 B — 主索引检索：**

```
Grep pattern="关键词1|关键词2|关键词3" path="{KB_PATH}/knowledge/_index.md" output_mode="content"
```

从两路结果中提取所有候选 `.md` 文件路径（排除 `_tags_index.md` 和 `_index.md` 本身）。

**候选数控制（硬约束）**：

- 如果**任意一路**索引检索（路径 A 或 路径 B）返回的去重候选文件数 **> 20**，说明关键词过宽，必须立即收窄：
  1. 优先丢弃命中数最多、最泛化的那个关键词，或将其替换为更具体的同义词；
  2. 若仍 > 20，则在阶段二（Step 3）把检索范围限定到推断出的单一分类目录（不再退回全目录全文检索）；
  3. 若用户问句本身就很泛（如"绩效有哪些功能？"），按"分类导航"的方式回答——只读取该分类的 `_index.md` 表格部分给出列表式概览，**不要**强行读取 20+ 个文件。
- 如果**单一关键词**在 `_tags_index.md` 里挂了 > 20 个文件，几乎可以肯定它是模块级标签（如「内部招聘」「职级评定」），不应作为检索关键词，应改为该模块下更具体的功能名（如「应聘许可」「资料复核」）。

### Step 3：阶段二 — 定向全文检索（仅在阶段一候选不足时）

如果阶段一获得的**去重候选文件 < 3 个**，执行定向全文检索补充候选：

1. **推断目标分类**：根据用户问题中的关键词判断最可能的分类目录（product/business/faq/guide/spec），缩小检索范围。例如问题提到"绩效""考核"则优先搜索 `product/` 和 `business/`；提到"操作""配置"则优先搜索 `guide/`。

2. **在目标分类目录下执行全文检索**，使用 `files_with_matches` 模式仅获取文件名（避免大量内容输出）：

```
Grep pattern="关键词1|关键词2|关键词3" path="{KB_PATH}/knowledge/{target_category}" glob="*.md" output_mode="files_with_matches"
```

3. 如果仍无法确定目标分类，则退回到全目录检索（同样仅取文件名）：

```
Grep pattern="关键词1|关键词2|关键词3" path="{KB_PATH}/knowledge" glob="*.md" output_mode="files_with_matches"
```

> **关键约束**：全文检索必须使用 `output_mode="files_with_matches"`，仅获取匹配文件路径列表，不输出匹配行内容，防止输出过大。

### Step 4：结果融合与排序

收集阶段一和阶段二的结果后，按以下规则融合去重：

1. **打分排序**：
   - 标签索引命中（路径 A）→ 权重 +3
   - 主索引命中（路径 B）→ 权重 +2
   - 全文检索命中（阶段二）→ 权重 +1
   - 多路径命中同一文件 → 累加计分
   - 多关键词命中同一文件 → 额外 +1
2. **取 Top 3 文件**：按得分从高到低排序，取前 3 个最相关的文件（去重后不足 3 个则全部读取）

### Step 5：读取相关文件

读取排序后的 **最相关的 1-3 个文件**（不要贪多）：

```
Read file_path="{KB_PATH}/knowledge/{matched_file_path}"
```

### Step 6：综合回答

基于读取到的知识条目内容：
- 组织清晰、准确的回答
- 标注引用来源（文件名）
- 如果知识库中无相关内容，执行「知识缺口自动追踪」流程（见下文章节），自动提交 GitHub issue 并告知用户

### Step 7：展示来源

回答末尾使用 `present_files` 工具展示引用的知识条目文件，让用户可以直接点击查看原始文档：

```
present_files files=[{"file_path": "{KB_PATH}/knowledge/product/feature-xxx.md"}]
```

### 回答规范

- 优先基于知识库内容回答，引用具体条目
- 如果知识库中无相关内容，执行「知识缺口自动追踪」流程，自动提交 GitHub issue 记录该问题，同时告知用户未找到答案且已创建追踪工单
- 保持回答简洁、准确、可操作

## 知识条目管理

### 添加新条目

当用户说"添加知识"、"记录一下"、"新增条目"时：

1. 确认分类（product/business/faq/guide/spec）与产品族（人才发展云 / 目标绩效云 / 人才供应云）
2. 使用模板创建新 Markdown 文件到 `{KB_PATH}/knowledge/{category}/`，记得填入 `cloud` 与 `aliases` 字段
3. **图片处理**：如条目包含配图，将图片统一存放至 `{KB_PATH}/knowledge/images/`，并在 Markdown 中使用相对路径引用，如 `![描述](../images/xxx.png)`
4. 运行 `bash scripts/rebuild_index.sh` 一次性刷新 `_index.md` / `_tags_index.md` / `_cloud_index.md` 三个索引
5. 执行 git commit

### 条目文件模板

每个知识条目文件遵循以下格式：

```markdown
---
title: 条目标题
category: product | business | faq | guide | spec
cloud: 人才发展云 | 目标绩效云 | 人才供应云   # 产品族归属，参与 _cloud_index.md 分组
tags: [标签1, 标签2]
aliases: [同义词1, 同义词2]                    # 可选；rebuild_index.sh 会把它当作额外标签写入 _tags_index.md
author: 作者
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# 条目标题

## 内容

[具体内容]

## 相关条目

- [相关条目1](../xxx/yyy.md)
```

**字段约定**：

- `cloud`：单值字符串。三大云之一；跨云或暂未归类时可留空（会落入 `_cloud_index.md` 的「未分类」段，建议尽快补齐）。
- `aliases`：列表。写入用户口语 / 异名 / 历史名称（例如 "PBC" 的别名 "个人绩效承诺"）。`rebuild_index.sh` 会把每个 alias 当作一个额外标签插入到倒排索引，提高同义词召回率，但**不会**写入 `_index.md` 的标签列。空 `aliases: []` 是合法的占位写法。

### 更新条目

当用户说"更新知识"、"修改条目"时：

1. 通过标签索引定位目标条目
2. 修改内容并更新 `updated` 日期
3. 运行 `bash scripts/rebuild_index.sh` 同步刷新 `_index.md` / `_tags_index.md` / `_cloud_index.md`
4. 执行 git commit

### 删除条目

当用户说"删除条目"、"移除知识"时：

1. 确认要删除的条目
2. 将文件移至回收站（不永久删除）
3. 运行 `bash scripts/rebuild_index.sh` 同步刷新三个索引
4. 执行 git commit

## 图片资源管理

### 存放规则

- **分类子目录**：图片按知识分类存放在 `{KB_PATH}/knowledge/images/` 下的对应子目录中：
  - `images/product/` — 产品功能配图
  - `images/guide/` — 操作指南配图
  - `images/faq/` — 常见问题配图
  - `images/` 根目录 — 通用图片或跨分类引用图片
- **路径引用**：Markdown 文件中使用相对路径引用图片：
  - `product/` 下的条目 → `../images/product/xxx.png`
  - `guide/` 下的条目 → `../images/guide/xxx.png`
  - `faq/` 下的条目 → `../images/faq/xxx.png`
  - `business/`、`spec/` 下的条目 → `../images/xxx.png`（使用根目录）
- **Git LFS 管理**：所有图片文件由 Git LFS 跟踪管理，`sync_kb.sh` 会在同步后自动检测并拉取 LFS 二进制数据，确保图片为真实内容而非指针文件。
- **命名规范**：尽量使用原始文件的有序命名（如截图工具生成的哈希名），避免中文文件名。
- **重复处理**：若不同条目引用同一张图片（内容完全相同），只保留一份副本，多个条目共用同一图片路径。

### 图片处理流程

当用户补充的知识包含图片时：

1. 确定图片所属分类，创建对应子目录（如不存在）：`{KB_PATH}/knowledge/images/{category}/`
2. 将图片复制到该子目录
3. 在 Markdown 中使用相对路径引用：`../images/{category}/xxx.png`

## Git 协作规范

### 远程仓库配置

- **知识库仓库**：`https://github.com/lz1996lizhu-commits/product-knowledge-base`
- **Skill 仓库**：`https://github.com/lz1996lizhu-commits/product-kb-skill`
- **主分支**：`master`（受保护，所有变更需通过 PR 合并）
- **权限问题**：如遇推送或拉取权限不足（403/401错误），请联系 **李铸** 处理权限配置

### 推送流程（每次知识变更后执行）

操作对象为**知识库仓库**（`{KB_PATH}`），不是 Skill 目录：

```bash
bash scripts/push_kb.sh
```

如果推送失败报权限错误，输出提示：
> ⚠️ 推送/拉取权限不足，请联系 **李铸** 处理仓库权限配置。
> 仓库地址：https://github.com/lz1996lizhu-commits/product-knowledge-base

### 提交规范

```
知识库提交消息格式:
- add(category): 新增XXX条目
- update(category): 更新XXX条目
- remove(category): 移除XXX条目
- fix(category): 修正XXX条目错误
```

### 协作流程总结

1. Skill 调用时自动 git pull 同步知识库最新
2. 在本地知识库中添加/修改知识条目
3. 更新双索引文件，提交变更
4. 创建 `task_{用户名}_{日期}_{时间}` 分支，推送并提 PR 到 master
5. 团队 review 后合并
6. 其他人下次调用 Skill 时自动 pull 到最新

## 索引维护

### 主索引 `_index.md`

```markdown
# 知识库索引

## 产品功能 (product)
| 文件 | 标题 | 标签 | 更新日期 |
|------|------|------|----------|
| product/feature-xxx.md | XXX功能说明 | 标签1,标签2 | 2025-01-01 |

## 业务流程 (business)
...

## 常见问题 (faq)
...

## 操作指南 (guide)
...

## 测试用例规格 (spec)
...
```

### 标签倒排索引 `_tags_index.md`

```markdown
# 标签倒排索引

> 自动生成，请勿手动编辑。

## K
### KPI
- product/feature-indicator-setting.md
- guide/guide-performance-indicators.md

## 人
### 人才盘点
- product/feature-talent-inventory-overview.md
- guide/guide-talent-inventory.md
...
```

### 云索引 `_cloud_index.md`

按产品族（人才发展云 / 目标绩效云 / 人才供应云）对所有条目重新分组的索引。文件来源是各条目 frontmatter 的 `cloud` 字段，由 `rebuild_index.sh` 自动生成。

```markdown
# 云索引（按产品族分组）

## 人才发展云 （N 条）
| 文件 | 标题 | 分类 | 更新日期 |
|------|------|------|----------|
| product/feature-cadre-management.md | 干部管理整体介绍 | product | 2026-05-29 |
...

## 目标绩效云 （N 条）
...

## 人才供应云 （N 条）
...

## 未分类 （N 条）
> 这些条目的 frontmatter 缺少 cloud 字段，建议补齐以便加入云索引。
...
```

**用途**：

- 检索预筛：用户问句明显指向某一云时，先 Grep 该云段拿到候选文件，再走标签倒排，能在 287+ 文件库下进一步压缩 noise。
- 一致性巡检：「未分类」段是 cloud 字段缺失的告警面板，新条目入库时一眼可见。

### 索引重建工具

当索引与实际条目不一致时（如批量导入、手工编辑后遗漏更新索引），可使用重建脚本从所有条目的 frontmatter 重新生成三个索引：

```bash
# 重建并写入（覆盖现有索引文件）
bash scripts/rebuild_index.sh

# 仅预览不写入（用于对比检查）
bash scripts/rebuild_index.sh --dry-run
```

脚本逻辑：遍历 `knowledge/` 下所有分类目录中的 `.md` 文件 → 解析 YAML frontmatter（title / category / tags / cloud / aliases / updated / test_case_count）→ 按分类生成 `_index.md` 表格 → 把 `tags` 与 `aliases` 合并、tolower、过滤超级标签后按首字符分组生成 `_tags_index.md` → 按 `cloud` 字段分组生成 `_cloud_index.md`。运行结束会输出条目数、标签数、被过滤的超级标签命中次数、分类分布、云分布、含非空 aliases 的条目数等统计。

**推荐使用场景**：

- 批量导入条目后一次性重建
- 检测到检索结果与预期不符时作为修复工具
- 定期运行以确保索引一致性

### spec 标签回填工具

`scripts/backfill_spec_tags.py` 用于审计/补齐 spec 区文件的业务关键词标签。规则：把文件名 `spec-{云}-{模块}-{子模块}.md` 按 `-` 拆分（去掉 `spec` 前缀和 `.md` 后缀，过滤 `spec/功能规格/测试用例` 三个通用词），剩余词作为业务标签。若 frontmatter 现有 `tags` 缺少这些词，则**仅追加，不删除**。

```bash
# 预览（不写入），列出每个文件待补充的标签
PYTHONIOENCODING=utf-8 python3 scripts/backfill_spec_tags.py --dry-run

# 实际写入（幂等，重复运行无副作用）
PYTHONIOENCODING=utf-8 python3 scripts/backfill_spec_tags.py
```

**推荐使用场景**：

- 批量导入新的 spec 用例后一次性补齐
- 周期性审计 spec 区命名 → 标签的一致性
- 写入完成后建议接着运行 `rebuild_index.sh` 刷新双索引

**安全约束**：仅识别内联 `tags: [...]` 格式（spec 区当前全部使用此格式）；多行 YAML 列表会被自动跳过，不会破坏。

### 云字段回填工具

`scripts/backfill_cloud_field.py` 用于为缺少 `cloud` 与 `aliases` 字段的旧条目批量补齐。规则按四级优先级推断：

1. 文件名直接出现「人才发展云 / 目标绩效云 / 人才供应云」→ 取该值
2. 现有 `tags` 中出现以上三个云名 → 取该值
3. 关键词词典（标题 + 文件名 + tags 拼接小写串）匹配到对应云
4. 仍命中不到 → 写空 `cloud:` 并在终端输出警告，等待人工补齐

`aliases` 始终在缺失时写入空列表 `aliases: []`。已存在且非空的字段不会被覆盖（幂等）。

```bash
# 仅报告每个文件的推断结果，不修改
PYTHONIOENCODING=utf-8 python3 scripts/backfill_cloud_field.py --report

# 预览（不写入）
PYTHONIOENCODING=utf-8 python3 scripts/backfill_cloud_field.py --dry-run

# 实际写入（幂等，重复运行无副作用）
PYTHONIOENCODING=utf-8 python3 scripts/backfill_cloud_field.py
```

**推荐使用场景**：

- 引入 `cloud` / `aliases` 字段后，对存量条目做一次性回填
- 周期性巡检：发现 `_cloud_index.md` 的「未分类」段长出新条目时手工或 CI 重跑
- 写入完成后必须接着运行 `rebuild_index.sh` 才能让 `_cloud_index.md` 生效

## 知识缺口自动追踪（GitHub Issue）

当用户在知识库中搜索后未获得明确答案时，本 Skill 自动向知识库仓库提交 GitHub issue，用于追踪和后续补充缺失知识。

### 触发条件

同时满足以下条件时执行：
1. Step 2 阶段一索引检索（标签索引 + 主索引）未命中任何结果
2. Step 3 阶段二定向全文检索仍未命中（或候选文件数 < 3 触发全文检索后仍无结果）
3. Step 4 结果融合后无任何候选文件

### Issue 提交流程

```bash
export ISSUE_TITLE="[知识缺失] {用户问题摘要}"
export ISSUE_BODY="## 缺失知识描述

{用户原始问题}

## 搜索关键词

- {关键词1}
- {关键词2}
- ...

## 信息

- 提交时间: $(date '+%Y-%m-%d %H:%M:%S')
- 来源: QoderWork 产品知识库 Skill 自动检测

## 建议

请补充相关知识条目到知识库，完成后关闭此 issue。"

bash scripts/create_issue.sh
```

### Issue 标题规范

标题格式统一为：
```
[知识缺失] {用户问题的核心摘要（不超过30字）}
```

示例：
- `[知识缺失] 如何配置 KPI 指标的权重规则`
- `[知识缺失] 人才盘点流程中校准会的参与角色`

### Issue 标签

优先添加标签：`知识缺失`

> 若仓库中不存在该标签，`gh` 不支持自动创建，将自动降级为不带标签提交。

### 用户告知话术

提交 issue 后，向用户回复：
> 当前知识库中暂未找到与您问题相关的条目，我已自动在 GitHub 上创建了追踪 issue（#{issue_number}），团队会尽快补充相关知识。您可以关注该 issue 的进展，或手动添加知识条目。

### 权限与失败处理

- 需要 `gh` 已登录且对 `lz1996lizhu-commits/product-knowledge-base` 仓库有创建 issue 的权限
- 若提交失败（网络问题、权限不足等），仅向用户告知"未找到答案，建议手动添加知识"，不阻塞后续对话
- 如遇权限问题，输出提示：
  > ⚠️ 自动提交 issue 失败，请联系 **李铸** 确认 GitHub 仓库权限配置。

## 环境依赖

本 Skill 需要以下工具（首次使用时检查）：

- **git**：版本管理（通常已预装）
- **gh**（GitHub CLI）：用于创建 PR 和 Issue

如果 `gh` 未安装，提示用户：
> 需要安装 GitHub CLI 以支持 PR 和 Issue 创建。请访问 https://cli.github.com/ 安装，或运行：
> - Windows: `winget install GitHub.cli`
> - macOS: `brew install gh`
> - 安装后执行 `gh auth login` 完成认证

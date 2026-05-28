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

每次调用本 Skill 时，首先执行智能数据同步。通过时间戳缓存避免不必要的网络请求，提升响应速度：

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
│   └── images/            # 图片资源统一存放目录
│       ├── xxx.png        # 所有知识条目的配图统一放在此目录下
│       └── ...
└── CONTRIBUTING.md        # 协作贡献指南
```

## 问答工作流

当用户提出产品/业务相关问题时，按以下优化流程执行：

### Step 1：提取关键词

从用户问题中提取 2-5 个核心关键词/标签词。优先匹配产品术语、功能名称、业务概念。将关键词用 `|` 连接，形成组合检索表达式，例如：`关键词1|关键词2|关键词3`。

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

1. 确认分类（product/business/faq/guide/spec）
2. 使用模板创建新 Markdown 文件到 `{KB_PATH}/knowledge/{category}/`
3. **图片处理**：如条目包含配图，将图片统一存放至 `{KB_PATH}/knowledge/images/`，并在 Markdown 中使用相对路径引用，如 `![描述](../images/xxx.png)`
4. 更新 `{KB_PATH}/knowledge/_index.md` 索引
5. 更新 `{KB_PATH}/knowledge/_tags_index.md` 标签索引
6. 执行 git commit

### 条目文件模板

每个知识条目文件遵循以下格式：

```markdown
---
title: 条目标题
category: product | business | faq | guide | spec
tags: [标签1, 标签2]
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

### 更新条目

当用户说"更新知识"、"修改条目"时：

1. 通过标签索引定位目标条目
2. 修改内容并更新 `updated` 日期
3. 同步更新 `_index.md` 和 `_tags_index.md`
4. 执行 git commit

### 删除条目

当用户说"删除条目"、"移除知识"时：

1. 确认要删除的条目
2. 将文件移至回收站（不永久删除）
3. 从 `_index.md` 和 `_tags_index.md` 移除条目
4. 执行 git commit

## 图片资源管理

### 存放规则

- **统一目录**：所有知识条目的配图必须统一存放在 `{KB_PATH}/knowledge/images/` 目录下，禁止在 `product/`、`business/`、`faq/`、`guide/`、`spec/` 等分类目录中创建子目录存放图片。
- **路径引用**：Markdown 文件中使用相对路径引用图片：
  - `product/`、`business/`、`faq/`、`guide/`、`spec/` 下的文件统一使用 `../images/xxx.png`
- **命名规范**：尽量使用原始文件的有序命名（如截图工具生成的哈希名），避免中文文件名。
- **重复处理**：若不同条目引用同一张图片（内容完全相同），只保留一份副本，多个条目共用同一图片路径。

### 图片处理流程

当用户补充的知识包含图片时：

1. 将所有图片复制到 `{KB_PATH}/knowledge/images/`
2. 在 Markdown 中将原始路径（如 `子目录/xxx.png`）替换为 `../images/xxx.png`
3. 清理已迁移的原图片子目录

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

### 索引重建工具

当索引与实际条目不一致时（如批量导入、手工编辑后遗漏更新索引），可使用重建脚本从所有条目的 frontmatter 重新生成双索引：

```bash
# 重建并写入（覆盖现有索引文件）
bash scripts/rebuild_index.sh

# 仅预览不写入（用于对比检查）
bash scripts/rebuild_index.sh --dry-run
```

脚本逻辑：遍历 `knowledge/` 下所有分类目录中的 `.md` 文件 → 解析 YAML frontmatter（title/category/tags/updated/test_case_count）→ 按分类生成 `_index.md` 表格 → 按标签首字符分组生成 `_tags_index.md` 倒排索引。

**推荐使用场景**：

- 批量导入条目后一次性重建
- 检测到检索结果与预期不符时作为修复工具
- 定期运行以确保索引一致性

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

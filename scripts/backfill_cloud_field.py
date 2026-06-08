#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_cloud_field.py
=======================

为知识库每个 .md 条目的 frontmatter 补齐 `cloud` 与 `aliases` 字段。

字段语义
--------
- cloud: 产品族归属，取值 `人才发展云` / `目标绩效云` / `人才供应云`，用于
         _cloud_index.md 先做产品族缩圈、再走标签倒排，提升大库精度。
- aliases: 同义词列表，rebuild_index.sh 会把 aliases 当作额外标签写入
           _tags_index.md，提高召回率。

推断顺序（仅在原值缺失或为空时）
--------------------------------
1. 文件名中直接出现「人才发展云 / 目标绩效云 / 人才供应云」→ 取该值
2. 现有 tags 中出现以上三个云名 → 取该值
3. 按关键词词典（标题 + 文件名 + tags）匹配到对应云
4. 仍命中不到 → 留空，并在终端输出 WARN 行让人工补齐

幂等性
------
- 已存在的 cloud（非空）不会被覆盖
- 已存在的 aliases（即便是 []）不会被覆盖
- 仅当确实新增字段时才写文件

用法
----
  backfill_cloud_field.py             # 实际写入
  backfill_cloud_field.py --dry-run   # 仅预览，不写文件
  backfill_cloud_field.py --report    # 仅输出每个文件的推断结果（不写）
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

KB_PATH = Path(os.environ.get("KB_PATH", str(Path.home() / ".product-knowledge-base")))
KNOWLEDGE_DIR = KB_PATH / "knowledge"
CATEGORIES = ("product", "business", "faq", "guide", "spec")

CLOUDS = ("人才发展云", "目标绩效云", "人才供应云")

# 关键词 → 云 映射（用于推断 cloud）。匹配范围：title + filename + tags 拼接小写串。
# 注意：关键词不分大小写匹配；中文不受 lower 影响。
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    ("人才发展云", [
        "干部", "继任", "人才盘点", "人才档案", "人才储备", "人才星图",
        "能力素质", "认证组", "专委会", "任职资格", "职级", "学习",
        "人才发展", "dfx",
    ]),
    ("目标绩效云", [
        "绩效", "考核", "kpi", "bsc", "pbc", "mbo", "360",
        "评估表单", "评估对象", "指标制定", "结果归档", "结果汇总",
        "结果确认", "校准", "绩效面谈", "绩效档案", "绩效评估",
        "团队绩效", "我的绩效", "hr自助服务",
    ]),
    ("人才供应云", [
        "招聘", "录用", "候选人", "offer", "直通车", "简历",
        "面试", "入职协同", "应聘", "人人面试官", "招聘职位",
        "招聘服务", "生态租户", "moka",
    ]),
]


def split_frontmatter(text: str) -> tuple[list[str], list[str], list[str]] | None:
    """把文件内容拆成 (前导=[], frontmatter_lines, body_lines)。

    frontmatter_lines 不含两个 `---` 分隔行。
    若文件没有合法的 frontmatter，返回 None。
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None
    return ([lines[0]], lines[1:end_idx], lines[end_idx:])


def parse_field(fm_lines: list[str], name: str) -> tuple[bool, str, int]:
    """在 frontmatter 行中查找 `name:` 字段。

    返回 (是否存在, 该行原始值串, 行号 index)。
    - 不区分内联 / 多行格式：只关心是否有顶层 `name:` 行
    - 对于多行 list 字段，本函数只看第一行；调用方按需判断空/非空
    """
    pat = re.compile(r"^" + re.escape(name) + r"\s*:\s*(.*)$")
    for i, ln in enumerate(fm_lines):
        m = pat.match(ln)
        if m:
            return (True, m.group(1).rstrip("\r\n").strip(), i)
    return (False, "", -1)


def has_nonempty_field(fm_lines: list[str], name: str) -> tuple[bool, bool]:
    """返回 (字段是否存在, 是否非空)。

    aliases: [] / aliases:\n （后跟无续行） 视为空。
    """
    exists, value, idx = parse_field(fm_lines, name)
    if not exists:
        return (False, False)
    v = value.strip()
    if v == "":
        # 多行格式：检查下一行是否有 "- xxx" 续行
        nxt = idx + 1
        while nxt < len(fm_lines) and fm_lines[nxt].lstrip().startswith("- "):
            return (True, True)
        return (True, False)
    if v in ("[]", "[ ]"):
        return (True, False)
    return (True, True)


def get_tags_text(fm_lines: list[str]) -> str:
    """提取 tags 字段的全部文本（包含多行续行），用于关键词匹配。"""
    exists, value, idx = parse_field(fm_lines, "tags")
    if not exists:
        return ""
    parts = [value]
    nxt = idx + 1
    while nxt < len(fm_lines) and fm_lines[nxt].lstrip().startswith("- "):
        parts.append(fm_lines[nxt].lstrip()[2:].strip())
        nxt += 1
    return " ".join(parts)


def get_field_value_simple(fm_lines: list[str], name: str) -> str:
    """简单单值字段（title 等）。"""
    _, value, _ = parse_field(fm_lines, name)
    return value.strip().strip('"').strip("'")


def infer_cloud(filename: str, fm_lines: list[str]) -> str:
    """按四级规则推断 cloud。返回空串表示无法判定。"""
    title = get_field_value_simple(fm_lines, "title")
    tags_text = get_tags_text(fm_lines)
    haystack_lower = (filename + " " + title + " " + tags_text).lower()

    # 1) 文件名直接命中
    for c in CLOUDS:
        if c in filename:
            return c
    # 2) 现有 tags 命中
    for c in CLOUDS:
        if c in tags_text:
            return c
    # 3) 关键词词典
    for cloud, keywords in KEYWORD_RULES:
        for kw in keywords:
            if kw in haystack_lower:
                return cloud
    return ""


def render_new_lines(missing_cloud: bool, missing_aliases: bool, cloud_value: str) -> list[str]:
    """生成要插入到 frontmatter 末尾的新行。"""
    out = []
    if missing_cloud:
        if cloud_value:
            out.append(f"cloud: {cloud_value}\n")
        else:
            # 留空字符串，方便后续手工补齐
            out.append("cloud: \n")
    if missing_aliases:
        out.append("aliases: []\n")
    return out


def process_file(path: Path, dry_run: bool, report_only: bool) -> dict:
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        return {"path": path, "status": "no-frontmatter"}

    head, fm, tail = parts

    cloud_exists, cloud_nonempty = has_nonempty_field(fm, "cloud")
    aliases_exists, _ = has_nonempty_field(fm, "aliases")

    # 决定是否需要新增
    need_cloud = (not cloud_exists) or (not cloud_nonempty)
    need_aliases = not aliases_exists

    # 推断 cloud（只在需要时算）
    inferred = ""
    if need_cloud:
        inferred = infer_cloud(path.name, fm)

    # 如果 cloud 存在但是空（cloud:），同样视为需要补值；
    # 我们采用「重写该行」策略：找到原行替换。否则在末尾追加。
    rewrite_existing_cloud = cloud_exists and not cloud_nonempty and inferred

    if not need_cloud and not need_aliases:
        return {"path": path, "status": "skip-already-complete",
                "cloud_existing": True}

    if report_only:
        return {
            "path": path,
            "status": "report",
            "need_cloud": need_cloud,
            "need_aliases": need_aliases,
            "inferred_cloud": inferred,
        }

    new_fm = list(fm)
    if rewrite_existing_cloud:
        for i, ln in enumerate(new_fm):
            if re.match(r"^cloud\s*:\s*$", ln) or re.match(r"^cloud\s*:\s*\r?\n$", ln):
                new_fm[i] = f"cloud: {inferred}\n"
                break
        # 此情况下 need_cloud 已被处理，无需在末尾再追加
        appended = render_new_lines(False, need_aliases, "")
    else:
        appended = render_new_lines(need_cloud, need_aliases, inferred)

    # 保证 frontmatter 末尾不留空行；插入到最后
    if appended:
        # 如果最后一行没换行，先补
        if new_fm and not new_fm[-1].endswith("\n"):
            new_fm[-1] = new_fm[-1] + "\n"
        new_fm.extend(appended)

    new_text = "".join(head + new_fm + tail)
    if new_text == text:
        return {"path": path, "status": "skip-no-change"}

    if not dry_run:
        path.write_text(new_text, encoding="utf-8")

    return {
        "path": path,
        "status": "patched",
        "added_cloud": need_cloud or rewrite_existing_cloud,
        "added_aliases": need_aliases,
        "inferred_cloud": inferred,
        "warn_cloud_unknown": need_cloud and not inferred,
    }


def iter_kb_files() -> list[Path]:
    files = []
    for cat in CATEGORIES:
        d = KNOWLEDGE_DIR / cat
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            if p.name.startswith("_"):
                continue
            files.append(p)
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="只预览不写入")
    ap.add_argument("--report", action="store_true",
                    help="只输出每个文件的推断结果，不修改也不写入")
    args = ap.parse_args()

    if not KNOWLEDGE_DIR.is_dir():
        print(f"❌ 知识库目录不存在: {KNOWLEDGE_DIR}", file=sys.stderr)
        return 1

    files = iter_kb_files()
    print(f"📂 扫描 {len(files)} 个条目（{KNOWLEDGE_DIR}）")

    summary = {
        "patched": 0, "skip-already-complete": 0, "skip-no-change": 0,
        "no-frontmatter": 0, "report": 0,
    }
    warns = []
    cloud_dist: dict[str, int] = {}

    for f in files:
        r = process_file(f, dry_run=args.dry_run, report_only=args.report)
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        if r.get("warn_cloud_unknown"):
            warns.append(str(f.relative_to(KNOWLEDGE_DIR)))
        if r.get("inferred_cloud"):
            cloud_dist[r["inferred_cloud"]] = cloud_dist.get(r["inferred_cloud"], 0) + 1
        if args.report:
            mark_c = "+cloud" if r.get("need_cloud") else "     "
            mark_a = "+aliases" if r.get("need_aliases") else "        "
            print(f"  [{mark_c}|{mark_a}] {r.get('inferred_cloud') or '-':<8}  "
                  f"{f.relative_to(KNOWLEDGE_DIR)}")

    print()
    print("📊 处理结果：")
    for k, v in summary.items():
        if v == 0:
            continue
        print(f"   {k}: {v}")
    if cloud_dist:
        print("   推断/写入的 cloud 分布:")
        for c in CLOUDS:
            if c in cloud_dist:
                print(f"     {c}: {cloud_dist[c]}")
        for c, n in cloud_dist.items():
            if c not in CLOUDS:
                print(f"     {c}: {n}")
    if warns:
        print()
        print(f"⚠️  无法推断 cloud 的条目（共 {len(warns)} 条，已写入 cloud: 空值）：")
        for p in warns:
            print(f"   - {p}")
        print("   建议手动给这些条目的 frontmatter 补上 cloud 字段后重新运行。")

    if args.dry_run:
        print()
        print("📋 --dry-run 模式，未写入文件")

    return 0


if __name__ == "__main__":
    sys.exit(main())

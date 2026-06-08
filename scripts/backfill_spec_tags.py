#!/usr/bin/env python3
"""
backfill_spec_tags.py
=====================
从 spec 区文件名拆分业务关键词，补充进每个文件的 frontmatter tags 字段。

设计原则：
- 幂等：重复执行无副作用（已有的标签不会重复添加）
- 仅追加：永不删除任何现有标签
- 安全：默认 dry-run 提示；写入采用就地替换，但只动 tags 这一行
- 通用：兼容内联格式 `tags: [a, b, c]`；不处理多行 YAML 列表（spec 区目前全部使用内联）

文件名解析规则
--------------
基名 `spec-A-B-C.md` 去掉 `spec-` 前缀和 `.md` 后缀后，按 `-` 拆分得到 [A, B, C]。
过滤掉 COMMON_TAGS 中的通用词（spec/功能规格/测试用例），其余作为候选业务标签。

用法
----
    python3 backfill_spec_tags.py             # 实际写入
    python3 backfill_spec_tags.py --dry-run   # 仅预览，不写文件

环境变量
--------
    KB_PATH  知识库根路径，默认 ~/.product-knowledge-base
"""

import argparse
import os
import re
import sys

KB_PATH = os.environ.get("KB_PATH", os.path.expanduser("~/.product-knowledge-base"))
SPEC_DIR = os.path.join(KB_PATH, "knowledge", "spec")

# 已默认存在或不具备业务区分度的通用词，从候选中剔除
COMMON_TAGS = {"spec", "功能规格", "测试用例"}

# 仅匹配内联 tags（spec 全部用此格式）；不破坏多行 YAML
TAGS_RE = re.compile(r"^(tags:\s*\[)(.*?)(\])\s*$", re.MULTILINE)


def parse_filename_tags(filename: str) -> list:
    """从 spec 文件名拆出候选业务标签。"""
    base = filename[:-3] if filename.endswith(".md") else filename
    if base.startswith("spec-"):
        base = base[len("spec-"):]
    parts = [p.strip() for p in base.split("-") if p.strip()]
    return [p for p in parts if p not in COMMON_TAGS]


def split_existing_tags(inner: str) -> list:
    """把 `[a, b, c]` 内部内容解析为列表，去除引号与首尾空白。"""
    out = []
    for t in inner.split(","):
        t = t.strip().strip("\"'")
        if t:
            out.append(t)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="仅预览，不写入文件")
    args = ap.parse_args()

    if not os.path.isdir(SPEC_DIR):
        print(f"❌ spec 目录不存在: {SPEC_DIR}")
        return 1

    files_total = 0
    files_changed = 0
    tags_added_total = 0
    skipped_no_tags = 0

    for fname in sorted(os.listdir(SPEC_DIR)):
        if not fname.endswith(".md") or fname.startswith("_"):
            continue
        files_total += 1
        path = os.path.join(SPEC_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        m = TAGS_RE.search(content)
        if not m:
            skipped_no_tags += 1
            print(f"⚠️  {fname}: 未找到内联 tags 字段，跳过")
            continue

        existing = split_existing_tags(m.group(2))
        existing_set = set(existing)
        candidates = parse_filename_tags(fname)
        to_add = [c for c in candidates if c not in existing_set]

        if not to_add:
            continue

        files_changed += 1
        tags_added_total += len(to_add)
        merged = existing + to_add
        new_line = m.group(1) + ", ".join(merged) + m.group(3)

        if args.dry_run:
            print(f"[+] {fname}")
            print(f"    原: {existing}")
            print(f"    增: {to_add}")
        else:
            new_content = content[: m.start()] + new_line + content[m.end():]
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"[✓] {fname}: +{to_add}")

    print()
    print("📊 统计：")
    print(f"   spec 文件总数:       {files_total}")
    print(f"   受影响文件数:        {files_changed}")
    print(f"   新增标签条目（去重）: {tags_added_total}")
    print(f"   未找到 tags 而跳过:  {skipped_no_tags}")
    if args.dry_run:
        print("📋 --dry-run 模式，未写入文件")
    else:
        print("✅ 写入完成。建议接着运行 rebuild_index.sh 刷新双索引。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

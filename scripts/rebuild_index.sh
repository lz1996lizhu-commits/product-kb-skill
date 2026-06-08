#!/usr/bin/env bash
# 索引重建工具：扫描所有 .md 条目的 frontmatter，重建 _index.md 和 _tags_index.md
#
# 用法：
#   rebuild_index.sh          # 重建索引并写入文件
#   rebuild_index.sh --dry-run # 仅重建不写入，输出到 stdout 供对比
#
# 适用场景：
#   - 定期运行以确保索引与实际条目一致
#   - 增删改条目后索引出现偏差时的修复工具
#   - 批量导入条目后一次性重建

set -e

KB_PATH="${KB_PATH:-$HOME/.product-knowledge-base}"
KNOWLEDGE_DIR="$KB_PATH/knowledge"
INDEX_FILE="$KNOWLEDGE_DIR/_index.md"
TAGS_FILE="$KNOWLEDGE_DIR/_tags_index.md"
CLOUD_FILE="$KNOWLEDGE_DIR/_cloud_index.md"

# ============================================================
# 超级标签黑名单（仅在 _tags_index.md 中被过滤；_index.md 中仍展示）
# 这些是「模块级 / 通用级」标签，挂在过多条目上会污染倒排索引检索结果。
# 如审计脚本发现新的 >30 条目标签，可追加到此列表。
# 比对在 tolower 之后进行，因此黑名单也写小写。
# ============================================================
SUPER_TAGS="spec|整体介绍|产品概述|faq|基础配置|产品功能|业务流程|常见问题|操作指南|测试用例规格"

DRY_RUN=false
if [ "$1" = "--dry-run" ]; then
    DRY_RUN=true
fi

if [ ! -d "$KNOWLEDGE_DIR" ]; then
    echo "❌ 知识库目录不存在: $KNOWLEDGE_DIR"
    echo "请先运行 sync_kb.sh 同步知识库"
    exit 1
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# ============================================================
# Step 1: 扫描所有 .md 条目，解析 frontmatter，输出 TSV
# 字段: 文件相对路径 \t 标题 \t 分类 \t 逗号分隔标签 \t 更新日期 \t 用例数
# ============================================================
scan_entries() {
    find "$KNOWLEDGE_DIR" -name "*.md" \
        -not -name "_*" \
        -path "*/product/*" -o -name "*.md" -not -name "_*" -path "*/business/*" \
        -o -name "*.md" -not -name "_*" -path "*/faq/*" \
        -o -name "*.md" -not -name "_*" -path "*/guide/*" \
        -o -name "*.md" -not -name "_*" -path "*/spec/*" \
        | sort | while IFS= read -r filepath; do

        [ -f "$filepath" ] || continue

        local rel_path
        rel_path=$(echo "$filepath" | sed "s|${KNOWLEDGE_DIR}/||")

        # 验证文件在已知分类目录下
        case "$rel_path" in
            product/*|business/*|faq/*|guide/*|spec/*) ;;
            *) continue ;;
        esac

        awk -v rel_path="$rel_path" '
        BEGIN {
            title=""; category=""; tags_raw=""; updated=""
            test_case_count=""; in_tags_multi=0
            cloud=""; aliases_raw=""; in_aliases_multi=0
        }

        # 第二个 --- 结束 frontmatter
        NR > 1 && /^---[[:space:]]*$/ { exit }

        # 处理多行 tags 的续行: "- tag_name"
        in_tags_multi == 1 {
            if (/^[[:space:]]*-[[:space:]]/) {
                t = $0
                gsub(/^[[:space:]]*-[[:space:]]*/, "", t)
                gsub(/[[:space:]]*$/, "", t)
                gsub(/["'"'"']/, "", t)
                if (t != "") {
                    if (tags_raw != "") tags_raw = tags_raw "," t
                    else tags_raw = t
                }
            } else {
                in_tags_multi = 0
            }
        }

        # 处理多行 aliases 的续行: "- alias_name"
        in_aliases_multi == 1 {
            if (/^[[:space:]]*-[[:space:]]/) {
                t = $0
                gsub(/^[[:space:]]*-[[:space:]]*/, "", t)
                gsub(/[[:space:]]*$/, "", t)
                gsub(/["'"'"']/, "", t)
                if (t != "") {
                    if (aliases_raw != "") aliases_raw = aliases_raw "," t
                    else aliases_raw = t
                }
            } else {
                in_aliases_multi = 0
            }
        }

        # title 字段
        /^title:/ {
            t = $0; sub(/^title:[[:space:]]*/, "", t)
            gsub(/["'"'"']/, "", t); gsub(/[[:space:]]*$/, "", t)
            title = t
        }

        # category 字段
        /^category:/ {
            t = $0; sub(/^category:[[:space:]]*/, "", t)
            gsub(/[[:space:]]*$/, "", t)
            category = t
        }

        # cloud 字段（单值字符串）
        /^cloud:/ {
            t = $0; sub(/^cloud:[[:space:]]*/, "", t)
            gsub(/["'"'"']/, "", t); gsub(/[[:space:]]*$/, "", t)
            cloud = t
        }

        # tags 字段 - 内联格式 [tag1, tag2, ...]
        /^tags:[[:space:]]*\[/ {
            t = $0; sub(/^tags:[[:space:]]*\[/, "", t)
            sub(/\][[:space:]]*$/, "", t)
            gsub(/[[:space:]]*$/, "", t)
            tags_raw = t
        }

        # tags 字段 - 多行格式（下一行开始 "- tag"）
        /^tags:[[:space:]]*$/ {
            in_tags_multi = 1
        }

        # aliases 字段 - 内联格式 [a, b, ...]
        /^aliases:[[:space:]]*\[/ {
            t = $0; sub(/^aliases:[[:space:]]*\[/, "", t)
            sub(/\][[:space:]]*$/, "", t)
            gsub(/[[:space:]]*$/, "", t)
            aliases_raw = t
        }

        # aliases 字段 - 多行格式
        /^aliases:[[:space:]]*$/ {
            in_aliases_multi = 1
        }

        # updated 字段
        /^updated:/ {
            t = $0; sub(/^updated:[[:space:]]*/, "", t)
            gsub(/[[:space:]]*$/, "", t)
            updated = t
        }

        # test_case_count 字段（spec 专用）
        /^test_case_count:/ {
            t = $0; sub(/^test_case_count:[[:space:]]*/, "", t)
            gsub(/[[:space:]]*$/, "", t)
            test_case_count = t
        }

        END {
            # 清理标签中的引号和多余空格
            gsub(/["'"'"']/, "", tags_raw)
            gsub(/,[[:space:]]*/, ",", tags_raw)
            gsub(/[[:space:]]*,/, ",", tags_raw)
            # 清理 aliases 同样格式
            gsub(/["'"'"']/, "", aliases_raw)
            gsub(/,[[:space:]]*/, ",", aliases_raw)
            gsub(/[[:space:]]*,/, ",", aliases_raw)
            # TSV: rel_path \t title \t category \t tags \t updated \t tc_count \t cloud \t aliases
            printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n", \
                rel_path, title, category, tags_raw, updated, test_case_count, cloud, aliases_raw
        }
        ' "$filepath"
    done
}

echo "📂 扫描知识库: $KNOWLEDGE_DIR"
scan_entries > "$TMPDIR/entries.tsv"

# ============================================================
# NFC 归一化（best-effort）
# 修复 macOS NFD 路径或剪贴板异体写法导致的"看似相同实则不等"的标签碎片化。
# 仅在 python3 可用时执行；否则跳过，不影响后续流程。
# ============================================================
if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import sys, unicodedata
for line in sys.stdin:
    sys.stdout.write(unicodedata.normalize("NFC", line))
' < "$TMPDIR/entries.tsv" > "$TMPDIR/entries.nfc.tsv" && \
        mv "$TMPDIR/entries.nfc.tsv" "$TMPDIR/entries.tsv"
else
    echo "⚠️  未检测到 python3，跳过 NFC 归一化（仅在极少数 NFD 来源数据上有差异）"
fi

ENTRY_COUNT=$(wc -l < "$TMPDIR/entries.tsv" | tr -d ' ')
echo "📊 找到 $ENTRY_COUNT 个知识条目"

if [ "$ENTRY_COUNT" -eq 0 ]; then
    echo "❌ 未找到任何条目，终止重建"
    exit 1
fi

# ============================================================
# Step 2: 生成 _index.md
# ============================================================
generate_index() {
    awk -F'\t' '
    BEGIN {
        # 分类顺序和中文名称
        n_cats = 5
        cats[1]="product";   cat_names[1]="产品功能"
        cats[2]="business";  cat_names[2]="业务流程"
        cats[3]="faq";       cat_names[3]="常见问题"
        cats[4]="guide";     cat_names[4]="操作指南"
        cats[5]="spec";      cat_names[5]="测试用例规格"
    }
    {
        path=$1; title=$2; category=$3; tags=$4; updated=$5; tc_count=$6
        n = ++count[category]
        cat_path[category, n]   = path
        cat_title[category, n]  = title
        cat_tags[category, n]   = tags
        cat_updated[category, n]= updated
        cat_tc[category, n]     = tc_count
    }
    END {
        print "# 知识库索引"
        print ""
        print "> 本文件为金蝶AI HR产品业务知识库总索引，由 rebuild_index.sh 自动生成。"
        print "> 产品版本：金蝶AI HR V8.0.11 / 2026Q2"

        for (ci = 1; ci <= n_cats; ci++) {
            c = cats[ci]
            if (count[c] == 0) continue

            print ""
            print "## " cat_names[ci] " (" c ")"

            if (c == "spec") {
                print ""
                print "> 本节为产品功能测试用例规格说明，每个文件按模块整理了完整的功能路径、操作步骤、预期结果，可作为产品行为的权威规格参考。"
                print ""
                print "| 文件 | 标题 | 标签 | 用例数 | 更新日期 |"
                print "|------|------|------|--------|----------|"

                total_cases = 0
                for (i = 1; i <= count[c]; i++) {
                    printf "| %s | %s | %s | %s | %s |\n", \
                        cat_path[c,i], cat_title[c,i], cat_tags[c,i], \
                        cat_tc[c,i], cat_updated[c,i]
                    if (cat_tc[c,i]+0 > 0) total_cases += cat_tc[c,i]+0
                }
                print ""
                printf "> 测试用例总数：%s 条 / %d 个模块\n", \
                    format_number(total_cases), count[c]
            } else {
                print ""
                print "| 文件 | 标题 | 标签 | 更新日期 |"
                print "|------|------|------|----------|"

                for (i = 1; i <= count[c]; i++) {
                    printf "| %s | %s | %s | %s |\n", \
                        cat_path[c,i], cat_title[c,i], \
                        cat_tags[c,i], cat_updated[c,i]
                }
            }
        }
    }

    # 数字千分位格式化
    function format_number(num,    s, result, len, i) {
        s = sprintf("%d", num)
        len = length(s)
        result = ""
        for (i = 1; i <= len; i++) {
            if (i > 1 && (len - i) % 3 == 2) result = result ","
            result = result substr(s, i, 1)
        }
        return result
    }
    ' "$TMPDIR/entries.tsv"
}

echo "📝 生成 _index.md ..."
generate_index > "$TMPDIR/_index.md"

# ============================================================
# Step 3: 生成 _tags_index.md
# ============================================================

# 3a. 展开 "标签-文件" 对，每行一条
#     - 同时把 aliases 视为额外标签写入倒排表（提升同义词召回率）
#     - trim 前后空白
#     - tolower（中文字符不受影响，仅消除英文标签如 Spec/SPEC/spec 的碎片化）
#     - 跳过空标签
#     - 跳过 SUPER_TAGS 黑名单中的"模块级/通用级"标签
awk -F'\t' -v super_tags="$SUPER_TAGS" '
BEGIN {
    super_re = "^(" super_tags ")$"
}
{
    path=$1; tags_str=$4; aliases_str=$8
    # 把 tags 和 aliases 合并（aliases 视为同义标签）
    combined = tags_str
    if (aliases_str != "") {
        if (combined != "") combined = combined "," aliases_str
        else combined = aliases_str
    }
    n = split(combined, tags_arr, ",")
    for (i = 1; i <= n; i++) {
        tag = tags_arr[i]
        gsub(/^[[:space:]]+/, "", tag)
        gsub(/[[:space:]]+$/, "", tag)
        tag = tolower(tag)
        if (tag == "") continue
        if (tag ~ super_re) continue
        print tag "\t" path
    }
}' "$TMPDIR/entries.tsv" | sort -u > "$TMPDIR/tag_pairs.tsv"

# 3b. 生成 _tags_index.md
generate_tags_index() {
    # 排序：按标签名 → 按文件路径
    sort -t$'\t' -k1,1 -k2,2 "$TMPDIR/tag_pairs.tsv" | \
    awk -F'\t' '
    BEGIN {
        current_tag = ""
        current_group = ""
        first_tag_in_group = 1
    }
    {
        tag = $1; file_path = $2

        # 提取标签首字符作为分组键
        group_char = substr(tag, 1, 1)

        if (group_char != current_group) {
            # 输出上一个分组的结尾空行
            if (current_group != "") print ""

            current_group = group_char
            printf "## %s\n", group_char
            first_tag_in_group = 1
        }

        if (tag != current_tag) {
            current_tag = tag
            printf "\n### %s\n", tag
        }

        printf "- %s\n", file_path
    }
    END {
        if (current_group != "") print ""
    }'
}

echo "🏷️  生成 _tags_index.md ..."

{
    echo "# 标签倒排索引"
    echo ""
    echo "> 自动生成，请勿手动编辑。由 rebuild_index.sh 从条目 frontmatter 重建。"
    echo ""
    generate_tags_index
} > "$TMPDIR/_tags_index.md"

# ============================================================
# Step 3.5: 生成 _cloud_index.md（按产品族分组）
# 数据源：entries.tsv 第 7 列 cloud 字段
# 分组顺序固定为：人才发展云 → 目标绩效云 → 人才供应云 → 其他/未分类
# ============================================================
generate_cloud_index() {
    awk -F'\t' '
    BEGIN {
        n_clouds = 3
        clouds[1] = "人才发展云"
        clouds[2] = "目标绩效云"
        clouds[3] = "人才供应云"
        for (i = 1; i <= n_clouds; i++) known[clouds[i]] = 1
    }
    {
        path=$1; title=$2; category=$3; updated=$5; cloud=$7
        if (cloud == "") cloud = "未分类"
        if (!(cloud in known) && cloud != "未分类") {
            # 收集额外的 cloud 值（比如以后新增的产品族），保持顺序
            if (!(cloud in extra_seen)) {
                extra_seen[cloud] = 1
                n_extra++
                extras[n_extra] = cloud
            }
        }
        n = ++count[cloud]
        c_path[cloud, n] = path
        c_title[cloud, n] = title
        c_category[cloud, n] = category
        c_updated[cloud, n] = updated
    }
    END {
        print "# 云索引（按产品族分组）"
        print ""
        print "> 自动生成，请勿手动编辑。由 rebuild_index.sh 从 frontmatter.cloud 字段重建。"
        print "> 用于先按产品族缩小检索范围，再走标签倒排索引，提升大规模知识库下的精度。"

        # 1) 已知三大云
        for (i = 1; i <= n_clouds; i++) {
            c = clouds[i]
            if (count[c] == 0) continue
            printf "\n## %s （%d 条）\n\n", c, count[c]
            print "| 文件 | 标题 | 分类 | 更新日期 |"
            print "|------|------|------|----------|"
            for (k = 1; k <= count[c]; k++) {
                printf "| %s | %s | %s | %s |\n", \
                    c_path[c,k], c_title[c,k], c_category[c,k], c_updated[c,k]
            }
        }

        # 2) 额外发现的 cloud（保持稳定顺序）
        for (i = 1; i <= n_extra; i++) {
            c = extras[i]
            printf "\n## %s （%d 条）\n\n", c, count[c]
            print "| 文件 | 标题 | 分类 | 更新日期 |"
            print "|------|------|------|----------|"
            for (k = 1; k <= count[c]; k++) {
                printf "| %s | %s | %s | %s |\n", \
                    c_path[c,k], c_title[c,k], c_category[c,k], c_updated[c,k]
            }
        }

        # 3) 未分类
        if (count["未分类"] > 0) {
            printf "\n## 未分类 （%d 条）\n\n", count["未分类"]
            print "> 这些条目的 frontmatter 缺少 cloud 字段，建议补齐以便加入云索引。"
            print ""
            print "| 文件 | 标题 | 分类 | 更新日期 |"
            print "|------|------|------|----------|"
            for (k = 1; k <= count["未分类"]; k++) {
                printf "| %s | %s | %s | %s |\n", \
                    c_path["未分类",k], c_title["未分类",k], c_category["未分类",k], c_updated["未分类",k]
            }
        }
    }' "$TMPDIR/entries.tsv"
}

echo "☁️  生成 _cloud_index.md ..."
generate_cloud_index > "$TMPDIR/_cloud_index.md"

# ============================================================
# Step 4: 写入或输出结果
# ============================================================
if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "===== _index.md (前 30 行预览) ====="
    head -30 "$TMPDIR/_index.md"
    echo ""
    echo "===== _tags_index.md (前 30 行预览) ====="
    head -30 "$TMPDIR/_tags_index.md"
    echo ""
    echo "===== _cloud_index.md (前 30 行预览) ====="
    head -30 "$TMPDIR/_cloud_index.md"
    echo ""
    echo "📋 --dry-run 模式，未写入文件"
else
    cp "$TMPDIR/_index.md" "$INDEX_FILE"
    cp "$TMPDIR/_tags_index.md" "$TAGS_FILE"
    cp "$TMPDIR/_cloud_index.md" "$CLOUD_FILE"

    echo ""
    echo "✅ 索引重建完成！"
    echo "   📄 _index.md       → $INDEX_FILE"
    echo "   🏷️  _tags_index.md  → $TAGS_FILE"
    echo "   ☁️  _cloud_index.md → $CLOUD_FILE"
fi

echo ""
echo "📊 统计："
echo "   条目总数: $ENTRY_COUNT"
TAG_COUNT=$(awk -F'\t' '{print $1}' "$TMPDIR/tag_pairs.tsv" | sort -u | wc -l | tr -d ' ')
echo "   标签总数（已过滤超级标签后）: $TAG_COUNT"

# 报告被过滤掉的超级标签命中次数（按出现频次降序）
SUPER_REPORT=$(awk -F'\t' -v super_tags="$SUPER_TAGS" '
BEGIN { super_re = "^(" super_tags ")$" }
{
    path=$1; tags_str=$4
    n = split(tags_str, tags_arr, ",")
    for (i = 1; i <= n; i++) {
        tag = tags_arr[i]
        gsub(/^[[:space:]]+/, "", tag); gsub(/[[:space:]]+$/, "", tag)
        tag = tolower(tag)
        if (tag != "" && tag ~ super_re) cnt[tag]++
    }
}
END {
    for (t in cnt) printf "%s\t%d\n", t, cnt[t]
}' "$TMPDIR/entries.tsv" | sort -t$'\t' -k2,2 -rn)

if [ -n "$SUPER_REPORT" ]; then
    echo "   已过滤的超级标签（仅在 _tags_index.md 中过滤，_index.md 中保留）:"
    echo "$SUPER_REPORT" | while IFS=$'\t' read -r tag cnt; do
        echo "     - $tag: $cnt 次"
    done
fi

echo "   分类分布:"
awk -F'\t' '{print $3}' "$TMPDIR/entries.tsv" | sort | uniq -c | sort -rn | while read cnt cat; do
    echo "     $cat: $cnt"
done

echo "   云分布（cloud 字段）:"
awk -F'\t' '{c=$7; if (c=="") c="(未分类)"; print c}' "$TMPDIR/entries.tsv" \
    | sort | uniq -c | sort -rn | while read cnt c; do
    echo "     $c: $cnt"
done

# aliases 覆盖率（非空 aliases 的条目数；空 aliases: [] 不计入，因为它对召回率无贡献）
ALIAS_COUNT=$(awk -F'\t' '$8 != "" {n++} END {print n+0}' "$TMPDIR/entries.tsv")
echo "   含非空 aliases 的条目: $ALIAS_COUNT / $ENTRY_COUNT"

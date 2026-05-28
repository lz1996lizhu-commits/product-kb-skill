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
            # 规范化逗号后的空格
            gsub(/,[[:space:]]*/, ",", tags_raw)
            gsub(/[[:space:]]*,/, ",", tags_raw)
            printf "%s\t%s\t%s\t%s\t%s\t%s\n", \
                rel_path, title, category, tags_raw, updated, test_case_count
        }
        ' "$filepath"
    done
}

echo "📂 扫描知识库: $KNOWLEDGE_DIR"
scan_entries > "$TMPDIR/entries.tsv"

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
awk -F'\t' '{
    path=$1; tags_str=$4
    n = split(tags_str, tags_arr, ",")
    for (i = 1; i <= n; i++) {
        tag = tags_arr[i]
        gsub(/^[[:space:]]+/, "", tag)
        gsub(/[[:space:]]+$/, "", tag)
        if (tag != "") print tag "\t" path
    }
}' "$TMPDIR/entries.tsv" > "$TMPDIR/tag_pairs.tsv"

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
    echo "📋 --dry-run 模式，未写入文件"
else
    cp "$TMPDIR/_index.md" "$INDEX_FILE"
    cp "$TMPDIR/_tags_index.md" "$TAGS_FILE"

    echo ""
    echo "✅ 索引重建完成！"
    echo "   📄 _index.md     → $INDEX_FILE"
    echo "   🏷️  _tags_index.md → $TAGS_FILE"
fi

echo ""
echo "📊 统计："
echo "   条目总数: $ENTRY_COUNT"
TAG_COUNT=$(awk -F'\t' '{print $1}' "$TMPDIR/tag_pairs.tsv" | sort -u | wc -l | tr -d ' ')
echo "   标签总数: $TAG_COUNT"
echo "   分类分布:"
awk -F'\t' '{print $3}' "$TMPDIR/entries.tsv" | sort | uniq -c | sort -rn | while read cnt cat; do
    echo "     $cat: $cnt"
done

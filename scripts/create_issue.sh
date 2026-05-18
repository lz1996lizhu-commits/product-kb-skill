#!/usr/bin/env bash
# Issue 提交流程：知识缺口自动追踪
# 环境变量依赖：ISSUE_TITLE, ISSUE_BODY

KB_PATH="$HOME/.product-knowledge-base"

# 切换到知识库仓库目录
cd "$KB_PATH"

# 提交 issue（使用 gh CLI）
# 先尝试带标签提交，若标签不存在则降级为不带标签提交
gh issue create \
  --repo "lz1996lizhu-commits/product-knowledge-base" \
  --title "$ISSUE_TITLE" \
  --body "$ISSUE_BODY" \
  --label "知识缺失" \
|| gh issue create \
  --repo "lz1996lizhu-commits/product-knowledge-base" \
  --title "$ISSUE_TITLE" \
  --body "$ISSUE_BODY"

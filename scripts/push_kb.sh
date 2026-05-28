#!/usr/bin/env bash
# 推送流程：每次知识变更后执行
# 操作对象为知识库仓库（{KB_PATH}），不是 Skill 目录

set -e  # 遇到错误立即退出

KB_PATH="$HOME/.product-knowledge-base"
cd "$KB_PATH"

# 1. 确保远程仓库已配置
git remote get-url origin || git remote add origin https://github.com/lz1996lizhu-commits/product-knowledge-base.git

# 2. 获取当前 git 用户名和日期，创建分支
GIT_USER=$(git config user.name 2>/dev/null | tr ' ' '_')
GIT_USER=${GIT_USER:-unknown}
TODAY=$(date +%Y%m%d)
TIMESTAMP=$(date +%H%M%S)
BRANCH="task_${GIT_USER}_${TODAY}_${TIMESTAMP}"

# 3. 基于本地最新提交创建推送分支（-B 允许覆盖同名分支）
git checkout -B "$BRANCH"

# 4. 推送分支到远程
if ! git push -u origin "$BRANCH"; then
    echo "⚠️ 推送失败，请检查网络或仓库权限"
    git checkout master 2>/dev/null || git checkout main 2>/dev/null || true
    exit 1
fi

# 5. 创建 PR 到 master 分支（先检查是否已有同名 PR）
EXISTING_PR=$(gh pr list --head "$BRANCH" --state open --json number -q '.[0].number' 2>/dev/null || echo "")
if [ -n "$EXISTING_PR" ]; then
    echo "已有 PR #${EXISTING_PR}，跳过创建"
else
    # 从最近的 commit 消息生成 PR 描述
    COMMIT_MSG=$(git log -1 --format='%s')
    gh pr create --base master --head "$BRANCH" \
        --title "知识库更新: ${COMMIT_MSG}" \
        --body "## 变更内容

${COMMIT_MSG}

## 分支

${BRANCH}" || echo "⚠️ PR 创建失败，请手动在 GitHub 上创建"
fi

# 6. 推送完成后切回 master 分支
git checkout master 2>/dev/null || git checkout main 2>/dev/null || true

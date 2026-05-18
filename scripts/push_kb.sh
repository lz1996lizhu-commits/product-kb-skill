#!/usr/bin/env bash
# 推送流程：每次知识变更后执行
# 操作对象为知识库仓库（{KB_PATH}），不是 Skill 目录

KB_PATH="$HOME/.product-knowledge-base"
cd "$KB_PATH"

# 1. 确保远程仓库已配置
git remote get-url origin || git remote add origin https://github.com/lz1996lizhu-commits/product-knowledge-base.git

# 2. 获取当前 git 用户名和日期，创建分支
GIT_USER=$(git config user.name | tr ' ' '_')
TODAY=$(date +%Y%m%d)
BRANCH="task_${GIT_USER}_${TODAY}"

# 3. 基于本地最新提交创建推送分支
git checkout -b "$BRANCH"

# 4. 推送分支到远程
git push -u origin "$BRANCH"

# 5. 创建 PR 到 master 分支
gh pr create --base master --head "$BRANCH" --title "$BRANCH" --body "知识库更新"

# 6. 推送完成后切回 main/master 分支
git checkout master || git checkout main

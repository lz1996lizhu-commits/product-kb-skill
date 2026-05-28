#!/usr/bin/env bash
# 迁移兼容逻辑：仅旧版升级时执行一次
# 如果检测到当前 Skill 目录下存在 knowledge/ 子目录，说明是从旧版升级而来

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KB_PATH="$HOME/.product-knowledge-base"
MIGRATE_MARKER="$SKILL_DIR/.migrated"

if [ -d "$SKILL_DIR/knowledge" ] && [ ! -f "$MIGRATE_MARKER" ]; then
  echo "检测到旧版数据，执行自动迁移..."

  # 1. 确保知识库数据存在于新的数据路径
  if [ ! -d "$KB_PATH" ]; then
    # 数据路径由 git clone 自动创建
    # 从当前远程仓库浅克隆最新知识库数据
    git clone --depth 1 \
      https://github.com/lz1996lizhu-commits/product-knowledge-base.git \
      "$KB_PATH"
  fi

  # 2. 清理 Skill 目录中的旧知识数据
  rm -rf "$SKILL_DIR/knowledge"
  rm -f "$SKILL_DIR/CONTRIBUTING.md"

  # 3. 切换 Skill 仓库 remote 到 Skill 专用仓库，并重置本地分支
  cd "$SKILL_DIR"
  git remote set-url origin https://github.com/lz1996lizhu-commits/product-kb-skill.git
  git fetch origin master
  git checkout -B master origin/master --force

  # 4. 标记迁移完成
  touch "$SKILL_DIR/.migrated"

  echo "✓ 迁移完成！Skill 已升级为新架构。"
  echo "  - 知识库数据路径: $KB_PATH"
  echo "  - Skill 仓库已切换到: product-kb-skill"
fi

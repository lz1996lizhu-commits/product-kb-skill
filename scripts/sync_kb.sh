#!/usr/bin/env bash
# 数据初始化脚本：智能同步知识库
# 通过时间戳缓存避免不必要的网络请求

KB_PATH="$HOME/.product-knowledge-base"
SYNC_MARKER="$KB_PATH/.last_sync"
SYNC_INTERVAL=28800  # 同步间隔：8小时（单位：秒）

needs_sync=false

if [ ! -d "$KB_PATH/.git" ]; then
    # 首次使用：需要克隆
    needs_sync=true
else
    if [ ! -f "$SYNC_MARKER" ]; then
        needs_sync=true
    else
        LAST_SYNC=$(cat "$SYNC_MARKER")
        NOW=$(date +%s)
        if [ $((NOW - LAST_SYNC)) -gt $SYNC_INTERVAL ]; then
            needs_sync=true
        fi
    fi
fi

if [ "$needs_sync" = true ]; then
    if [ ! -d "$KB_PATH" ]; then
        # 首次使用：浅克隆知识库（不下载 LFS 图片二进制）
        GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \
            https://github.com/lz1996lizhu-commits/product-knowledge-base.git \
            "$KB_PATH"
    else
        # 增量同步：静默拉取最新内容
        cd "$KB_PATH" && git pull origin master --depth 1 --no-edit 2>/dev/null || true
    fi
    # 更新同步时间戳
    date +%s > "$SYNC_MARKER"
else
    echo "使用本地缓存（上次同步后8小时内不再重复拉取）"
fi

#!/usr/bin/env bash
# 数据初始化脚本：智能同步知识库
# 通过时间戳缓存避免不必要的网络请求
# 用法：sync_kb.sh [--force]  （--force 跳过时间戳检查，强制同步）

KB_PATH="$HOME/.product-knowledge-base"
SYNC_MARKER="$KB_PATH/.last_sync"
SYNC_INTERVAL=28800  # 同步间隔：8小时（单位：秒）

FORCE_SYNC=false
if [ "$1" = "--force" ]; then
    FORCE_SYNC=true
fi

needs_sync=false

if [ ! -d "$KB_PATH/.git" ]; then
    # 首次使用：需要克隆
    needs_sync=true
elif [ "$FORCE_SYNC" = true ]; then
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
    SYNC_SUCCESS=false

    if [ ! -d "$KB_PATH" ]; then
        # 首次使用：浅克隆知识库
        if git clone --depth 1 \
            https://github.com/lz1996lizhu-commits/product-knowledge-base.git \
            "$KB_PATH"; then
            SYNC_SUCCESS=true
        else
            echo "⚠️ 首次克隆失败，请检查网络连接"
        fi
    else
        # 增量同步：静默拉取最新内容
        if cd "$KB_PATH" && git pull origin master --depth 1 --no-edit 2>/dev/null; then
            SYNC_SUCCESS=true
        else
            echo "⚠️ 同步失败，下次调用时将重试（使用本地缓存继续工作）"
        fi
    fi

    # 仅在同步成功时更新时间戳
    if [ "$SYNC_SUCCESS" = true ]; then
        mkdir -p "$KB_PATH"
        date +%s > "$SYNC_MARKER"
    fi
else
    echo "使用本地缓存（上次同步后8小时内不再重复拉取）"
fi

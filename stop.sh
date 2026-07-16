#!/usr/bin/env bash
# 停止 Flask 服务（macOS / Linux），端口 5000
echo "========================================"
echo "  停止 PDF to AI Film 服务（端口 5000）"
echo "========================================"

PIDS=""
# 优先按端口精确定位
if command -v lsof >/dev/null 2>&1; then
    PIDS=$(lsof -ti tcp:5000 2>/dev/null)
fi
# 退而求其次：按进程命令行匹配 app.py
if [ -z "$PIDS" ]; then
    PIDS=$(pgrep -f "[a]pp.py" 2>/dev/null)
fi

if [ -z "$PIDS" ]; then
    echo "未发现运行中的服务（可能已停止）。"
    exit 0
fi

for pid in $PIDS; do
    echo "结束进程 PID $pid ..."
    kill "$pid" 2>/dev/null || true
done

sleep 1
# 若仍存活则强制结束
for pid in $PIDS; do
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
    fi
done

echo "服务已停止。"

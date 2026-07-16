#!/usr/bin/env bash
# 启动 Flask 服务（macOS / Linux）
set -e
cd "$(dirname "$0")"

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
    echo "[错误] 找不到项目内虚拟环境 .venv"
    echo "请先运行 ./install.sh 安装依赖。"
    exit 1
fi

echo "========================================"
echo "  PDF to AI Film Generator"
echo "  Flask 服务启动中..."
echo "========================================"
echo
echo "浏览器访问: http://127.0.0.1:5000"
echo "按 Ctrl+C 停止服务"
echo

exec "$VENV_PY" app.py

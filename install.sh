#!/usr/bin/env bash
# 安装依赖（macOS / Linux）：创建项目内 .venv 并安装 requirements.txt
set -e
cd "$(dirname "$0")"

VENV_DIR=".venv"
PY="python3"
command -v "$PY" >/dev/null 2>&1 || PY="python"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "[提示] 未找到项目内虚拟环境，正在创建 $VENV_DIR ..."
    "$PY" -m venv "$VENV_DIR"
fi

echo "========================================"
echo "  安装 Python 依赖"
echo "========================================"

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

echo
echo "安装完成！运行 ./run.sh 启动服务。"
echo "注意：还需系统安装 ffmpeg（macOS: brew install ffmpeg；Debian/Ubuntu: sudo apt install ffmpeg）。"

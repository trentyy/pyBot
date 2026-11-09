#!/usr/bin/env bash
# 在樹莓派上安裝 dc-manager 並註冊為 systemd 服務。
# 用法：在專案根目錄執行 bash deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="${SUDO_USER:-$(whoami)}"
SERVICE=/etc/systemd/system/dc-manager.service

cd "$APP_DIR"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "需要 Python 3.11 以上，目前是 $(python3 --version)" >&2
    exit 1
fi
if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "缺少 python3-venv，請先執行：sudo apt install python3-venv" >&2
    exit 1
fi

echo "==> 建立虛擬環境並安裝套件"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

mkdir -p data logs
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "==> 已建立 .env，請編輯填入 DISCORD_TOKEN：nano $APP_DIR/.env"
fi

echo "==> 安裝 systemd 服務（需要 sudo）"
sed -e "s#__DIR__#$APP_DIR#g" -e "s#__USER__#$APP_USER#g" deploy/dc-manager.service | sudo tee "$SERVICE" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable dc-manager

if grep -qE '^DISCORD_TOKEN=.+' .env; then
    sudo systemctl restart dc-manager
    echo "==> 已啟動。查看狀態：sudo systemctl status dc-manager"
else
    echo "==> 填好 .env 後執行：sudo systemctl start dc-manager"
fi
echo "    即時 log：journalctl -u dc-manager -f"

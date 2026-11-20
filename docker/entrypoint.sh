#!/bin/sh
# 容器啟動入口：修正掛載目錄的擁有者後，改用一般使用者執行 bot。
# 額外參數會傳給 bot，例如 docker compose run --rm dc-manager --sync
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    # 主機上的 data/、logs/ 可能是 root 或其他使用者建立的
    chown -R "$PUID:$PGID" /app/data /app/logs
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups python -m dc_manager "$@"
fi

exec python -m dc_manager "$@"

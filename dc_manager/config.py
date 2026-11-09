"""讀取設定：環境變數或專案根目錄的 .env 檔。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_or_none(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value)


@dataclass(frozen=True)
class Config:
    token: str
    db_path: Path
    log_dir: Path
    log_level: str
    # 需在 Developer Portal 開啟 SERVER MEMBERS INTENT，自動身分組功能才會運作
    members_intent: bool
    # 開發用：設定後斜線指令會立即同步到這個伺服器（全域同步可能要等一段時間）
    dev_guild_id: int | None
    # 選填：只能透過 HTTP proxy 對外連線的網路環境，例如 http://192.168.1.10:3128
    proxy: str | None = None
    # 儀器警報：區網接收器（預設關閉）
    alarm_http_enabled: bool = False
    alarm_http_host: str = "0.0.0.0"
    alarm_http_port: int = 8080
    # 儀器警報：資料庫輪詢設定檔（檔案不存在就不輪詢）
    alarm_sources_file: Path | None = None

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Config":
        load_dotenv(env_file or PROJECT_ROOT / ".env")
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise SystemExit("缺少 DISCORD_TOKEN，請複製 .env.example 為 .env 並填入 Bot Token。")

        def path(name: str, default: str) -> Path:
            p = Path(os.getenv(name) or default)
            return p if p.is_absolute() else PROJECT_ROOT / p

        return cls(
            token=token,
            db_path=path("DB_PATH", "data/dc-manager.db"),
            log_dir=path("LOG_DIR", "logs"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            members_intent=_bool(os.getenv("ENABLE_MEMBERS_INTENT"), True),
            dev_guild_id=_int_or_none(os.getenv("DEV_GUILD_ID")),
            proxy=(os.getenv("DISCORD_PROXY") or "").strip() or None,
            alarm_http_enabled=_bool(os.getenv("ALARM_HTTP_ENABLED"), False),
            alarm_http_host=(os.getenv("ALARM_HTTP_HOST") or "0.0.0.0").strip(),
            alarm_http_port=int(os.getenv("ALARM_HTTP_PORT") or 8080),
            alarm_sources_file=path("ALARM_SOURCES_FILE", "alarm_sources.toml"),
        )

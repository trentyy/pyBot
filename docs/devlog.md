# 開發日誌

新的紀錄放在最上面。

## 2026-11-20　Prometheus 指標輸出

**目的**：讓 Portal（另一個 repo）的 Prometheus 抓 bot 的狀態。commit 88678c3。

**做了什麼**
- 新增 `dc_manager/metrics.py`：`GET /metrics`，Prometheus text format。
- 指標（名稱固定，Portal 依此查詢）：`dcbot_up`、`dcbot_guilds`、`dcbot_latency_seconds`、`dcbot_start_time_seconds`、`dcbot_log_messages_total{level="warning|error|critical"}`、`dcbot_last_error_timestamp_seconds`。
- root logger 掛 `LogCounter` handler，涵蓋 discord.py 與 dc_manager 的 log。
- 容器內 `0.0.0.0:9107`；主機端 `${METRICS_BIND:-127.0.0.1}:9107:9107`，Pi 上於 `.env` 設 `METRICS_BIND=<Tailscale IP>`（目前 100.122.167.97）。
- 一直開著，與警報接收器（`ALARM_HTTP_*`）無關。

**決定與理由**
- 用現有 aiohttp 手寫文字格式，不引入 prometheus_client：discord.py 已依賴 aiohttp，只有 6 個指標，少一個要固定版本的套件。
- `dcbot_guilds` 在每次抓取時讀 `len(bot.guilds)`，不掛 join／remove 事件，永遠與實際一致。
- `dcbot_up` 由 `on_ready`／`on_resumed` 設 1，`on_disconnect`／關閉設 0。
- `dcbot_latency_seconds` 未連線（nan／inf）時不輸出；`dcbot_last_error_timestamp_seconds` 從未發生 ERROR 時不輸出。
- 端點啟動失敗只記一筆 error log，bot 照常運作。

**注意事項**
- `LOG_LEVEL` 設得比 WARNING 高時，被濾掉的日誌不會被計數。
- 端點在 `setup_hook` 啟動，早於登入完成，bot 剛啟動幾秒內 `dcbot_up` 為 0、`dcbot_guilds` 為 0，屬正常。
- Pi 部署要先 `git pull` 再 `docker compose up -d --build`；沒 push 之前 Pi 上沒有這些檔案。

**驗證**
- 單元測試 `tests/test_metrics.py`，在 Docker 容器內跑全部 127 個通過。本機 Python 是 conda，base 不裝東西，所以測試一律走 `python:3.11-slim` 容器。
- Pi 部署後 `curl http://100.122.167.97:9107/metrics` 回傳指標，實測正常。

**未動**：警報接收器、資料庫結構、現有指令行為。

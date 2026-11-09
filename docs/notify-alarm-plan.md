# 儀器警報（notify / alarm）規劃草案

狀態：已決定並實作第 1、2 階段（2026-10-24）。

## 決定

| 項目 | 決定 |
| --- | --- |
| 資料來源 | 儀器電腦可跑腳本（區網推送）＋ 資料已在 PostgreSQL（輪詢） |
| Grafana | 選項 B：bot 判斷條件，Grafana 只看圖表（不做 webhook / annotation） |
| 重複提醒 | 每條規則自選「通知一次」或「警報：重複直到已知悉」 |
| 範圍調整 | 條件只做 ≥ > ≤ <（「介於 / 超出範圍」可用兩條規則達成）；`/api/v1/events` 事件端點暫不做 |

以下為原草案。

## 1. 目標

- 儀器溫度（或其他數值）達到條件時，在 Discord 提醒指定的成員 / 身分組。
- 資料來源兩種：
  1. **區網推送**：其他電腦透過區網把數值或事件送給樹莓派上的 bot。
  2. **資料庫輪詢**：bot 定期查詢資料庫，發現達到條件就提醒。
- 伺服器管理者在 Discord 設定「條件」與「提醒對象」。
- 可選：與 Grafana 整合。

## 2. 架構

```
 儀器電腦 A ──HTTP POST──┐
 儀器電腦 B ──HTTP POST──┤                     ┌───────────────────────┐
 Grafana Alerting ─webhook┼──> 區網 :8080 ───> │  樹莓派 dc-manager      │ ──WebSocket(對外)──> Discord
                          │                     │  · 接收器（aiohttp）    │
 資料庫 (MySQL…) <─SELECT─┼──── 定期查詢 ───── │  · 規則引擎            │
                          │                     │  · Discord 指令 / 按鈕 │
                          │                     └───────────────────────┘
```

- 接收器跑在 bot 同一個程式裡，用 discord.py 已經內建的 aiohttp，**不用新套件**。
- 只聽區網位址的 port（預設關閉，要在 `.env` 開啟），不需要對外 IP。
- 仍維持「bot 主動連 Discord」的模式，對外只需 443 出站。

## 3. 資料來源

### 3.1 區網推送（建議第一階段做）

兩種端點：

| 端點 | 用途 | 誰判斷條件 |
| --- | --- | --- |
| `POST /api/v1/readings` | 送數值，例如 `{"metric": "furnace1.temp", "value": 852.3}` | bot（Discord 設定的規則） |
| `POST /api/v1/events` | 送已判斷好的事件，例如 `{"key": "furnace1.ready", "message": "加熱完成"}` | 儀器電腦自己 |

- 驗證：每個來源一組 token（`Authorization: Bearer <token>`），在 Discord 用 `/alarm source create` 產生，**token 綁定該 Discord 伺服器**，所以多個伺服器共用一台 bot 也不會互相看到資料。
- 儀器電腦端只要能發 HTTP 即可：PowerShell `Invoke-RestMethod`、curl、Python 都行；會附一支範例腳本（讀 CSV / log 檔最後一行再送出）。
- 安全：限制 payload 大小、每個 token 限流、可選 IP 白名單；建議用 `ufw` 只允許實驗室網段連 8080。區網內是 HTTP 明文，若需要可再加 nginx + 自簽憑證。

### 3.2 資料庫輪詢（第二階段，依資料庫種類而定）

- **查詢語句寫在樹莓派的設定檔，不能在 Discord 裡輸入 SQL**。原因：bot 是多伺服器共用，若任何伺服器管理員都能在 Discord 下 SQL，等於把實驗室資料庫開放給所有邀請 bot 的人。
- 設定檔範例（`alarm_sources.toml`，由 bot 主機管理者維護）：

  ```toml
  [[database]]
  name = "lab"
  driver = "mysql"            # mysql / postgresql / influxdb / mssql …
  dsn = "mysql://readonly:***@192.168.1.20/lab"

  [[database.metric]]
  key = "furnace1.temp"
  query = "SELECT ts, value FROM temperature WHERE instrument = 'F1' ORDER BY ts DESC LIMIT 1"
  interval = "30s"
  guilds = [123456789012345678]   # 哪些 Discord 伺服器可以用這個數值
  ```

- Discord 這端只能「挑選允許的 metric 來設規則」，看不到 DSN 和 SQL。
- 建議資料庫開唯讀帳號。
- 需要依資料庫加一個套件（例如 MySQL 用 `aiomysql`、PostgreSQL 用 `asyncpg`、InfluxDB 用 `influxdb-client`）。

## 4. 規則（在 Discord 設定）

| 欄位 | 說明 |
| --- | --- |
| metric | 要監看的數值，例如 `furnace1.temp` |
| 條件 | `>=`、`<=`、`>`、`<`、介於、超出範圍 |
| 門檻 | 例如 850 |
| 持續時間 | 連續達到 N 秒才觸發，避免雜訊（預設 0） |
| 解除門檻 | 回到這個值才算恢復（遲滯），避免在門檻附近一直跳 |
| 類型 | **通知**：達到就提醒一次（例如「加熱到 850°C 了」）<br>**警報**：異常狀態，每 N 分鐘重複提醒，直到有人按「已知悉」 |
| 對象 | 成員 / 身分組（可多個），可選另外私訊 |
| 頻道 | 發在哪個頻道 |
| 無資料警報 | 超過 N 分鐘沒收到數值就提醒（儀器電腦當機、網路斷線） |
| 管理身分組 | 沿用提醒功能的做法：被指定的身分組可以管理自己的規則 |

狀態流程：`正常 → 觸發 →（已知悉）→ 恢復`

提醒訊息上的按鈕：

- **✅ 已知悉**：停止重複提醒，記錄是誰按的。
- **🔕 靜音**：選 30 分 / 1 小時 / 4 小時，期間不再提醒。
- 恢復正常時，原訊息自動更新成「已恢復」。

### 指令草案

```
/alarm source create|list|rotate|delete     區網推送來源與 token
/alarm rule create|edit|pause|resume|delete|list|test
/alarm status                               各 metric 最新數值與規則狀態
/alarmmanager add|remove|list               誰可以設定警報規則
```

### 資料保存

- 只保存每個 metric 的「最新值」與警報事件紀錄，不保存完整歷史（樹莓派 SD 卡寫入有限；歷史資料留在原資料庫 / Grafana）。
- 最新值放記憶體，每分鐘寫回 SQLite 一次。

## 5. Grafana 整合選項

| 選項 | 做法 | 優點 | 缺點 |
| --- | --- | --- | --- |
| **A. Grafana 判斷，bot 發送**（若已有 Grafana 建議此項） | Grafana Alerting 的 contact point 設成 webhook，指向 bot 的 `POST /api/v1/grafana/<token>` | 規則、歷史圖表、多資料來源都由 Grafana 處理；bot 不用連資料庫 | 條件要到 Grafana 介面設定，不是在 Discord |
| **B. bot 判斷，Grafana 只看圖** | bot 自己推送 / 輪詢並在 Discord 設規則；Grafana 照常顯示圖表 | 條件在 Discord 設定，符合需求 | 輪詢要支援你的資料庫種類 |
| **C. 兩者都要** | A + B 同時存在；bot 觸發 / 已知悉時另外在 Grafana 圖表加註記 (annotation) | 最完整 | 工作量最大，設定分兩處 |

選項 A 與 Grafana 內建的 Discord 通知相比，多了：

- Grafana 主機不需要能連網（由樹莓派轉送）
- 對應到 Discord 成員 / 身分組的 @提及
- 已知悉 / 靜音按鈕、重複提醒

## 6. 分階段

| 階段 | 內容 | 新套件 |
| --- | --- | --- |
| 1 | 區網接收器 + Discord 規則 + 已知悉 / 靜音 + 無資料警報 + 儀器端範例腳本 | 無 |
| 2 | 資料庫輪詢（設定檔） | 依資料庫種類 |
| 3（選用） | Grafana webhook 接收、annotation | 無 |

若選 Grafana 選項 A，第 1 階段的 webhook 接收器做好後，第 3 階段只需要再加一個 Grafana 格式的端點，工作量很小。

## 7. 待決定

1. 溫度資料目前在哪裡？（儀器軟體寫檔案 / 寫進資料庫 / 已經在 Grafana）
2. 若有資料庫：種類與版本？
3. 儀器電腦能不能跑一個小腳本（PowerShell 或 Python）主動送資料？
4. Grafana 整合選 A / B / C，或先不整合？
5. 警報要不要「重複提醒直到有人按已知悉」？預設間隔幾分鐘？

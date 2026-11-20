# dc-manager

多伺服器 Discord 身分組管理 bot。一台樹莓派跑一個 bot，就能邀請到任意多個伺服器，每個伺服器的設定各自獨立。

## 功能

| 功能 | 說明 |
| --- | --- |
| 按鈕身分組面板 | bot 發一則面板訊息，成員按按鈕取得 / 移除身分組（推薦） |
| 反應身分組 | 對任何訊息加表情，成員點表情取得身分組 |
| 三種模式 | **一般**：自由取得/移除 · **唯一**：同一則訊息只能選一個（例如顏色） · **驗證**：只能取得 |
| 自動身分組 | 新成員加入（或通過規則驗證）時自動給身分組 |
| 紀錄頻道 / 私訊通知 | 每個伺服器可設定身分組變動紀錄頻道，以及是否私訊成員 |
| 週期提醒 | 每 N 天 / 週 / 月提醒指定成員或身分組執行任務，可按「完成」或自選天數「延期」 |
| 提醒管理身分組 | 指定身分組（例如「A 儀器管理人」）可以建立並管理屬於自己的提醒任務 |
| 儀器警報 | 儀器電腦透過區網推送數值，或定期查詢 PostgreSQL；達到 Discord 設定的條件就提醒，可「已知悉」/「靜音」 |
| 自動清理 | 訊息、身分組被刪除或 bot 被踢出時，自動清除相關設定 |

安全性：管理指令預設只有「管理身分組」權限的人看得到；不能設定比自己或 bot 更高的身分組，也不能把含管理權限的身分組設成自助領取。

## 為什麼不需要對外 IP

bot 是**主動向 Discord 連線**（Gateway WebSocket），所有斜線指令、按鈕、反應事件都從這條連線送進來，不需要開 port、不需要固定 IP、不需要 port forwarding，放在 NAT / 內網後面即可。

樹莓派只需要能**對外**連到以下位址（HTTPS / 443 port）：

- `discord.com`（REST API）
- `gateway.discord.gg`（WebSocket）

如果內網只能透過 HTTP proxy 上網，在 `.env` 設定 `DISCORD_PROXY` 即可。若完全不能對外連線，Discord bot 無法運作。

儀器警報的區網接收器（選用）會在樹莓派開一個**區網** port（預設 8080），只給實驗室內的電腦連，仍然不需要對外 IP。

## 1. 建立 Discord Bot

1. 到 [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**，名稱填 `dc-manager`。
2. 左側 **Bot**：
   - **Reset Token** 取得 Token（等一下填進 `.env`，不要外流）。
   - **Privileged Gateway Intents** 開啟 **SERVER MEMBERS INTENT**（自動身分組需要；不需要可在 `.env` 設 `ENABLE_MEMBERS_INTENT=false`）。
   - 想讓別人也能邀請 bot，保持 **Public Bot** 開啟。
3. 邀請連結（把 `CLIENT_ID` 換成 **General Information** 的 Application ID）：

   ```
   https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=268528704
   ```

   包含權限：管理身分組、檢視頻道、傳送訊息、嵌入連結、讀取訊息歷史、新增反應、管理訊息。

> bot 加入超過 100 個伺服器後，Discord 會要求驗證 bot 才能繼續使用 SERVER MEMBERS INTENT。

## 2. 部署到樹莓派（Docker）

需求：64 位元 Raspberry Pi OS（或任何能跑 Docker 的 Linux / Windows / macOS）。

安裝 Docker（已安裝可略過，裝完重新登入讓群組生效）：

```bash
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
```

下載專案並建立設定檔：

```bash
git clone <repo網址> dc-manager && cd dc-manager
```

```bash
cp .env.example .env && nano .env
```

填入 `DISCORD_TOKEN` 後建置並啟動（第一次建置在樹莓派上要幾分鐘）：

```bash
docker compose up -d --build
```

常用指令：

```bash
docker compose ps
```

```bash
docker compose logs -f
```

```bash
docker compose restart
```

更新版本：

```bash
git pull && docker compose up -d --build
```

強制重新同步斜線指令（平常不需要，指令有變更時會自動同步）：

```bash
docker compose stop && docker compose run --rm dc-manager --sync
```

看到「已全域同步」後按 Ctrl+C，再 `docker compose up -d`。

- 資料庫是 SQLite，存在主機的 `data/dc-manager.db`，不需要另外架 MySQL。備份只要複製這個檔案（建議先 `docker compose stop`）。
- log 寫在主機的 `logs/dc-manager.log`（每檔 2MB，保留 5 份），`docker compose logs` 也看得到。
- 主機重開機會自動啟動；bot 異常結束最多自動重啟 5 次（`restart: on-failure:5`），所以 token 錯誤這類問題不會無限重啟。容器停下後用 `docker compose logs` 看原因，修好 `.env` 再 `docker compose up -d --force-recreate`。
- 儀器警報的 PostgreSQL 設定檔放在 `config/alarm_sources.toml`。若 PostgreSQL 跟 bot 在同一台主機，`dsn` 的主機名稱請寫 `host.docker.internal`（容器裡的 `localhost` 指的是容器自己）。
- 容器內的資料庫、log、設定檔路徑是固定的，`.env` 裡的 `DB_PATH`、`LOG_DIR`、`ALARM_SOURCES_FILE`、`ALARM_HTTP_HOST` 在 Docker 下不會生效；`ALARM_HTTP_PORT` 決定主機對外的 port。
- Prometheus 指標：容器內 `0.0.0.0:9107`，路徑 `/metrics`，一直開著（不受 `ALARM_HTTP_ENABLED` 影響）。主機端綁定位址由 `.env` 的 `METRICS_BIND` 決定，預設 `127.0.0.1`；部署到樹莓派請設成 Tailscale IP，再用 `curl http://<Tailscale IP>:9107/metrics` 驗證。指標說明見下方「監控指標」。
- 從舊的 systemd 版改用 Docker：先 `sudo systemctl disable --now dc-manager`，在同一個資料夾執行上面的 `docker compose up -d --build` 即可沿用原本的 `data/`；`alarm_sources.toml` 要移到 `config/` 底下。

### 不用 Docker：systemd 服務

需求：Raspberry Pi OS Bookworm 以上（Python 3.11+）。

```bash
cd dc-manager && bash deploy/install.sh
```

填好 `.env` 後 `sudo systemctl start dc-manager`，log 用 `journalctl -u dc-manager -f` 查看；更新版本用 `git pull && bash deploy/install.sh`。這個方式的 PostgreSQL 設定檔放在專案根目錄的 `alarm_sources.toml`。

## 3. 本機開發（conda）

```bash
conda env create -f environment.yml
```

```bash
conda activate dc-manager
```

```bash
python -m dc_manager
```

```bash
python -m pytest
```

開發時在 `.env` 設定 `DEV_GUILD_ID`（測試伺服器 ID），斜線指令會立刻更新；正式環境留空則全域同步（可能要等幾分鐘）。斜線指令只在內容有變更時才重新同步，若要強制同步：`python -m dc_manager --sync`。

## 指令

| 指令 | 說明 |
| --- | --- |
| `/buttonrole create [channel] [mode]` | 建立按鈕面板（跳出表單填標題與說明） |
| `/buttonrole add message role [label] [emoji] [style]` | 加入 / 更新按鈕 |
| `/buttonrole remove message role` | 移除按鈕 |
| `/buttonrole edit message` | 修改面板標題與說明 |
| `/buttonrole mode message mode` | 設定模式 |
| `/buttonrole list` | 列出面板 |
| `/reactionrole add message emoji role` | 新增表情 → 身分組 |
| `/reactionrole remove message role` | 移除設定 |
| `/reactionrole mode message mode` | 設定模式 |
| `/reactionrole clear message` | 清除整則訊息的設定 |
| `/reactionrole list` | 列出反應身分組 |
| `/autorole add\|remove\|list` | 新成員自動身分組 |
| `/settings logchannel [channel]` | 紀錄頻道（不填則關閉） |
| `/settings dmnotify enabled` | 私訊通知開關（預設關閉） |
| `/settings timezone name` | 提醒使用的時區（預設 Asia/Taipei） |
| `/settings show` | 顯示設定與 bot 權限狀態 |
| `/reminder create ...` | 建立週期提醒（見下方說明） |
| `/reminder edit task ...` | 修改任務，只填要改的欄位 |
| `/reminder pause\|resume task` | 暫停 / 恢復 |
| `/reminder trigger task` | 立刻發一次提醒（測試用，不影響固定週期） |
| `/reminder delete\|info task` · `/reminder list` | 刪除 / 詳細資訊 / 列表 |
| `/reminder week [date] [scope] [user] [role] [public]` | 雙週日程：一次看兩週、哪天要做什麼 |
| `/reminder digest set channel [weekday] [at]` · `off` · `show` | 每週自動發送雙週日程（需「管理伺服器」權限，預設星期一 09:00，沒有提醒就不發） |
| `/remindermanager add\|remove\|list role` | 設定可建立提醒的管理身分組（需「管理伺服器」權限） |
| `/help` | 使用說明 |

### 週期提醒

範例：每兩週一的 09:00 提醒 @A儀器組 做校正，每次最多可延期 3 天

```
/reminder create title:A 儀器校正 every:2 unit:週 role:@A儀器組 start_date:2026-11-02 at:09:00 max_postpone:3
```

- **週期**：`every` + `unit`，例如 2 週、1 月、10 天。每月 31 號遇到小月會自動取月底，下個月再回到 31 號。
- **每月固定的日子**（單位選「月」，`every` 是每幾個月一次）：
  - **每月幾號**：加上 `month_day`，例如 `every:1 unit:月 month_day:15` = 每月 15 號；31 號遇到小月取月底。
  - **每月第幾個星期幾**：加上 `nth` 和 `weekday`，例如 `every:1 unit:月 nth:第 2 個 weekday:星期四` = 每月第 2 個星期四；`nth` 選「最後一個」就是每月最後一個星期幾。第 1～4 個在每個月都存在，不會遇到沒有那一天的月份。
  - 兩種擇一。沒指定 `start_date` 時，第一次提醒是現在之後第一個符合的日子；指定的話取該日（含）之後第一個符合的日子。`/reminder edit` 也能改，換規則時從現在往後找，只改時間則保留原本的起點。
  - 搭配「從完成那天重新起算」時，完成後的下一次是「完成那個月再過 `every` 個月」依規則算出的日子，例如 11/5 完成、每月第 2 個星期四 → 12 月的第 2 個星期四。
- **下次提醒怎麼算**（`schedule`）
  - **依原本週期**（預設）：固定在週期日提醒，例如每月 1 號。沒按完成就在下一期標記「未完成」並發新的提醒。
  - **從完成那天重新起算**：按完成的那天才開始算下一個週期，例如 11/5 完成、兩週一次 → 11/19 提醒。
- **雙週日程**：`/reminder week` 一次列出本週與下週的提醒，沒有提醒的日子與整週都不顯示，有逾期未完成的會列在最上面；按鈕可切換前兩週 / 後兩週。`date` 指定要看哪一天所在週起的兩週，`scope` 選「只看我的」，`user` / `role` 篩選對象，`public` 公開顯示（預設只有你看得到）。「依原本週期」的任務可以事先算出每一次；「從完成那天重新起算」的任務在有人按完成之前，依原本的時程往後推算並標示「預估」，按完成後才重新起算。
- **每週自動發送**：管理員用 `/reminder digest set channel:#頻道` 設定後，bot 每週一 09:00（可用 `weekday`、`at` 調整，使用 `/settings timezone` 的時區）在該頻道發出兩週日程。兩週內沒有任何提醒就不發。bot 關機錯過發送時間，12 小時內開機會補發，超過就略過等下週。
- **完成**：負責的成員（指定的人或身分組成員）或任務管理者按「✅ 完成」。
- **延期**：按「⏰ 延期」後由按的人選擇天數；同一次提醒累計延期不超過 `max_postpone` 天（0 = 不能延期）。延期時間到會再發一次提醒。依原本週期的任務不能延到下一期之後。
- **權限**
  - 伺服器管理員（管理伺服器權限）可以管理所有任務。
  - 用 `/remindermanager add role:@A儀器管理人` 後，該身分組成員可以建立任務；任務的 `manager_role` 是 @A儀器管理人，擁有這個身分組的人都能修改、暫停、刪除這些任務，也能代按完成。
- 時間依伺服器時區（`/settings timezone`）計算；訊息裡的時間會依每個人的裝置時區顯示。
- 樹莓派關機期間錯過的提醒，開機後只補發一次，不會一次狂發。
- 若要 @ 身分組，該身分組需開啟「允許任何人 @提及此身分組」，或給 bot「提及所有身分組」權限。

### 儀器警報

數值（例如溫度）達到條件時，在 Discord 提醒指定的成員 / 身分組。條件在 Discord 設定，由 bot 判斷；Grafana 照常用來看圖表。

```
儀器電腦 ──HTTP（區網）──┐
                         ├──> 樹莓派 dc-manager ──> Discord
PostgreSQL <──定期查詢───┘
```

**數值來源（二選一或兩個都用）**

1. **區網推送**：儀器電腦主動把數值送給樹莓派。
   1. bot 主機的 `.env` 設 `ALARM_HTTP_ENABLED=true`，重啟服務。
   2. 建議用防火牆只開放實驗室網段，例如：`sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp`
   3. Discord 執行 `/alarm source create name:furnace-pc`，取得 token（只顯示一次）。
   4. 儀器電腦執行 [tools/send_reading.py](tools/send_reading.py)（只需 Python，不用裝套件），例如每 30 秒讀 CSV 最後一列：
      ```
      python send_reading.py --url http://<樹莓派IP>:8080 --token dcm_xxx --metric furnace1.temp --csv C:/Log/today.csv --column Temperature --watch 30
      ```
      或用 PowerShell / curl 直接 `POST /api/v1/readings`，內容 `{"metric": "furnace1.temp", "value": 852.3}`，標頭 `Authorization: Bearer <token>`。可用 `GET /api/v1/health` 測試連線。
2. **PostgreSQL 輪詢**：bot 定期查詢資料庫。
   1. 複製 [alarm_sources.example.toml](alarm_sources.example.toml) 為 `alarm_sources.toml`，填入連線字串、查詢語句、可使用的 Discord 伺服器 ID。
   2. 重啟服務。查詢只寫在樹莓派的設定檔，**不能從 Discord 輸入 SQL**（bot 是多伺服器共用，避免把資料庫開放給所有伺服器）。連線強制唯讀，仍建議使用唯讀帳號。

**規則**

```
/alarm rule create name:A 爐加熱完成 metric:furnace1.temp operator:≥ threshold:850 role:@A儀器組
/alarm rule create name:冷卻水溫過高 metric:chiller.temp operator:≥ threshold:30 kind:警報 repeat_minutes:10 clear_threshold:28 nodata_minutes:5 role:@A儀器組
```

- **類型**：通知（達到就提醒一次，回落後再達到會再提醒）／警報（每 `repeat_minutes` 分鐘重複 @，直到有人按「👀 已知悉」）。
- `for_seconds`：連續達到幾秒才觸發，避免雜訊。
- `clear_threshold`：回到這個值才算恢復，避免數值在門檻附近一直跳。
- `nodata_minutes`：超過幾分鐘沒收到數值就提醒（儀器電腦當機、網路斷線、資料庫沒更新）。
- 提醒訊息上的按鈕：**👀 已知悉**（停止重複提醒）、**🔕 靜音**（30 分鐘～24 小時不重複）。恢復時原訊息會自動改成「已恢復」。
- 只有提醒對象、規則管理者、伺服器管理員能按按鈕。
- 權限與週期提醒相同：伺服器管理員可管理全部；`/alarmmanager add role:@A儀器管理人` 後，該身分組可建立並管理屬於自己的規則。推送來源（token）只有伺服器管理員能建立。
- `/alarm status` 看各數值最新值與更新時間。
- 只保存每個數值的最新值（每分鐘寫入一次），不保存歷史，減少樹莓派 SD 卡寫入；歷史資料請看原資料庫 / Grafana。

`message` 參數可貼「訊息連結」（右鍵訊息 → 複製訊息連結），或在同一頻道直接貼訊息 ID。

伺服器管理員可在 **伺服器設定 → 整合 → dc-manager** 調整哪些身分組能使用各個指令。

## 疑難排解

- **身分組發不出去**：到伺服器設定把 bot 的身分組拖到要發放的身分組**上面**。`/settings show` 可檢查。
- **「唯一」模式的反應沒被收回**：bot 需要該頻道的「管理訊息」權限。
- **容器自己停了**：`docker compose logs` 看原因，最常見是 `.env` 的 `DISCORD_TOKEN` 沒填或錯誤。
- **啟動時出現 PrivilegedIntentsRequired**：Developer Portal 沒開 SERVER MEMBERS INTENT，開啟或設 `ENABLE_MEMBERS_INTENT=false`。
- **看不到斜線指令**：全域同步需要幾分鐘；也可以重新整理 Discord（Ctrl+R）。
- **提醒任務自己暫停了**：提醒頻道被刪除或 bot 失去發言權限，紀錄頻道會寫原因；修好後 `/reminder resume`。
- **提醒有發但沒通知到人**：身分組不允許被提及，見上方「週期提醒」最後一點。
- **儀器電腦送不進來**：先在儀器電腦開 `http://<樹莓派IP>:8080/api/v1/health`。連不上 → 檢查 `.env` 的 `ALARM_HTTP_ENABLED`、`ALARM_HTTP_PORT`、樹莓派防火牆；回 401 → token 錯誤或已 rotate。
- **一直跳「沒有收到資料」**：PostgreSQL 的 timestamp 若沒有時區，檢查 `alarm_sources.toml` 的 `timezone` 是否正確；`docker compose logs`（systemd 版用 `journalctl -u dc-manager`）可看查詢錯誤。

## 監控指標

`GET http://<主機>:9107/metrics`（Prometheus text format；不用 Docker 時用 `METRICS_HOST`、`METRICS_PORT` 調整，預設 `0.0.0.0:9107`）。

| 指標 | 型別 | 說明 |
| --- | --- | --- |
| `dcbot_up` | gauge | 已登入且與 Gateway 連線中為 1，斷線為 0 |
| `dcbot_guilds` | gauge | 目前服務的伺服器數 |
| `dcbot_latency_seconds` | gauge | Gateway 延遲（未連線時不輸出） |
| `dcbot_start_time_seconds` | gauge | 程序啟動的 Unix 時間 |
| `dcbot_log_messages_total{level="warning"\|"error"\|"critical"}` | counter | 累計日誌筆數（root logger，含 discord.py）；重啟歸零 |
| `dcbot_last_error_timestamp_seconds` | gauge | 最後一次 ERROR 以上日誌的 Unix 時間；從未發生就不輸出 |

注意 `LOG_LEVEL` 設得比 WARNING 高時，被過濾掉的日誌不會被計數。端點啟動失敗（例如 port 被占用）只會寫一筆 error log，bot 照常運作。

## 專案結構

```
dc_manager/
  __main__.py      啟動入口、log 設定
  bot.py           Bot 主體、指令同步、錯誤處理
  config.py        讀取 .env
  db.py            SQLite 資料表與 migration
  roles.py         發放 / 移除身分組
  utils.py         訊息連結解析、表情、權限檢查
  cogs/
    button_roles.py   按鈕面板
    reaction_roles.py 反應身分組
    settings.py       伺服器設定、自動身分組
    reminders.py      週期提醒（指令、排程、完成 / 延期按鈕）
    alarms.py         儀器警報（指令、已知悉 / 靜音按鈕、啟停）
    maintenance.py    自動清理、/help
  alarms/
    engine.py      警報規則引擎
    http_api.py    區網接收器
    pg.py          PostgreSQL 輪詢
  schedule.py      週期時間計算
  targets.py       提醒對象與管理權限（提醒、警報共用）
tools/send_reading.py  儀器電腦用的送數值腳本
Dockerfile, docker-compose.yml, docker/entrypoint.sh  Docker 部署
deploy/            樹莓派 systemd 服務與安裝腳本（不用 Docker 時）
tests/             pytest 測試
```

# IOM Auction Website (Flask + SQLite)

此專案係一個拍賣網站原型，後端用 `Flask`，資料庫用 `SQLite`。
目前已不需要 ODBC/SQL Server 先可運行本地版本。

## 1. 功能概覽

- 訪客可瀏覽拍賣、搜尋項目
- 會員可註冊、登入、出價
- 賣家可上架拍賣（含圖片上傳）
- 管理員可管理會員、拍賣狀態、授權 admin

## 2. 系統需求

- Python 3.10+（建議 3.11）
- `pip`
- Linux/macOS 或 Windows（PowerShell）

## 3. 快速開始

以下指令都喺專案根目錄（`app.py` 同層）執行。

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 初始化 DB（首次建議 reset）
python tools/init_sqlite_db.py --reset

# 啟動 DB mode
export USE_DB=1
python app.py
```

### Windows (PowerShell)

```powershell
py -m venv .venv
& ".\.venv\Scripts\Activate.ps1"
pip install -r requirements.txt

# 初始化 DB（首次建議 reset）
python tools/init_sqlite_db.py --reset

# 啟動 DB mode
$env:USE_DB = "1"
python app.py
```

啟動後預設網址：`http://127.0.0.1:5000`

## 4. 重要環境變數

| 變數 | 用途 |
| --- | --- |
| `USE_DB` | `1/true/yes` 時使用 SQLite；未設定時會走 demo fallback |
| `SQLITE_PATH` | 指定 `iom.db` 完整路徑（預設 `./iom.db`） |
| `PORT` | Gunicorn / 部署時使用的 port |
| `HOST` | `app.py` 本地啟動 host（預設 `127.0.0.1`） |
| `CURRENCY_SYMBOL` / `CURRENCY_LABEL` | 畫面貨幣顯示 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | 註冊確認 email SMTP 設定 |

## 5. 資料庫初始化與重置

```bash
# 預設路徑初始化
python tools/init_sqlite_db.py

# 強制重置
python tools/init_sqlite_db.py --reset

# 指定 DB 路徑
python tools/init_sqlite_db.py --path /tmp/iom.db
```

備註：

- 預設資料檔為 `iom.db`
- `iom.db` 已加入 `.gitignore`，避免提交真實資料

## 6. 常用工具腳本

| Script | 說明 | 範例 |
| --- | --- | --- |
| `tools/init_sqlite_db.py` | 建立/重置 `iom.db` | `python tools/init_sqlite_db.py --reset` |
| `tools/create_member.py` | 建立會員（可 `--activate`） | `python tools/create_member.py alice --password Secret123! --activate` |
| `tools/list_members.py` | 列出會員 | `python tools/list_members.py` |
| `tools/grant_revoke_admin.py` | 授權/撤銷 admin | `python tools/grant_revoke_admin.py grant 1` |
| `tools/reset_password.py` | 重設密碼 | `python tools/reset_password.py --username alice --password NewPass123!` |
| `tools/create_item_and_auction.py` | 建立測試 item + auction | `python tools/create_item_and_auction.py` |
| `tools/close_expired_auctions.py` | 批次關閉已過期拍賣 | `python tools/close_expired_auctions.py` |
| `tools/delete_auction.py` | 刪除拍賣與出價 | `python tools/delete_auction.py 1` |
| `tools/auto_place_bid.py` | 模擬登入及出價 | `python tools/auto_place_bid.py` |
| `tools/smoke_test.py` | 路由冒煙測試 | `python tools/smoke_test.py` |
| `tools/test_conn.py` | 測試 SQLite 連線 | `python tools/test_conn.py` |

舊 SQL Server / ODBC 工具主要放喺 `tools/legacy_sqlserver/`，只作參考用途。

## 7. 測試

```bash
python -m unittest discover -v
```

或指定單一測試：

```bash
python -m unittest tests.test_bids -v
```

## 8. 部署（Render 建議）

專案已有 `start.sh`：

1. 設定 `SQLITE_PATH`（預設 `./iom.db`）
2. 執行 `python tools/init_sqlite_db.py`
3. 啟動 `gunicorn`

### Render 設定建議

- Start Command 設為：`./start.sh`
- Environment 加上：`SQLITE_PATH=/tmp/iom.db`
- 如需持久化資料，使用 Persistent Disk，並把 `SQLITE_PATH` 改為 `/data/iom.db`

### Procfile 注意

目前 `Procfile` 係：

```procfile
web: gunicorn app:app
```

如果你希望部署時「每次先初始化 SQLite」，可改為：

```procfile
web: ./start.sh
```

## 9. 常見問題

- 啟動後 `/browse` 或 `/search` 500：通常係 DB 未初始化，先跑 `python tools/init_sqlite_db.py --reset`
- 註冊 email 無發送：未設定 `SMTP_*` 時系統會改為 log，不會真的寄出
- 看不到新圖片：確認檔案已存到 `static/uploads/`，並檢查瀏覽器快取

## 10. 其他備註

- `db_sqlserver.py` 保留舊版 SQL Server 實作以便參考
- 如要加欄位/表，可改 `db.py` 內 `_SCHEMA_SQL`，再重跑 DB 初始化

## 11. Render 上線 Checklist

### A. 上線前（本地）

1. `python -m unittest discover -v` 全部通過
2. `python tools/init_sqlite_db.py --reset` 可成功初始化
3. `python app.py` 本地可開到首頁、`/auctions`、`/search`
4. 確認 `start.sh` 可執行，且會先 init DB 再起 gunicorn
5. 若需要管理員帳號，先用工具腳本建立測試帳號並驗證可登入

### B. Render 服務設定

1. 建立 Web Service，連上 repository
2. Runtime 選 Python
3. Start Command 設為 `./start.sh`
4. Environment 先設 `USE_DB=1`
5. Environment 設 `SQLITE_PATH=/data/iom.db`（有 Persistent Disk）
6. 如果未掛 disk，暫時可用 `SQLITE_PATH=/tmp/iom.db`（重啟可能清資料）
7. 確認 `requirements.txt` 包含 `gunicorn`

### C. Persistent Disk（強烈建議）

1. 在 Render 掛載 Persistent Disk（例如 mount 到 `/data`）
2. `SQLITE_PATH` 指向 `/data/iom.db`
3. 首次部署後檢查資料檔是否已建立在 disk 路徑
4. 做一次 redeploy，確認資料不會消失

### D. 上線後驗證（Smoke）

1. 開首頁 `/`
2. 測試 `/search?key_word=test`
3. 測試 `/auctions`
4. 註冊新帳號 / 登入 / 登出
5. 建立一筆拍賣（含圖片）
6. 用另一帳號出價一次
7. 檢查 Render logs 無連續 500 錯誤

### E. 營運與備份

1. 每日或每週備份 `iom.db`（最少異地保存一份）
2. 變更 schema 前先備份，再部署
3. 設定 SMTP 時先用測試信箱驗證
4. 監控錯誤率、回應時間、磁碟容量

## 12. 將來搬 GCP Migration Checklist

### A. 架構決策

1. 運算平台建議用 Cloud Run（最貼近現有 gunicorn 模式）
2. 生產資料庫不要再用 SQLite，改用 Cloud SQL (PostgreSQL)
3. 圖片儲存由本地檔案改去 Cloud Storage
4. 密鑰與憑證放 Secret Manager

### B. 應用程式改造

1. 抽離 DB layer：以環境變數切換 SQLite / PostgreSQL
2. 將 `db.py` SQL 與 schema 遷移到 PostgreSQL 相容語法
3. 把 `static/uploads` 寫入流程改為上傳 Cloud Storage
4. Session secret、SMTP、DB URL 改用 Secret Manager / env 注入
5. 保留健康檢查路徑（例如 `/` 或新增 `/healthz`）

### C. 資料遷移

1. 盤點 SQLite schema（`member`, `item`, `auction`, `bid`, `item_image`, `category`）
2. 建立 PostgreSQL schema 與 index
3. 匯出 SQLite 資料並做欄位轉換
4. 匯入 Cloud SQL（先 staging）
5. 做資料對帳（筆數、關聯完整性、抽樣內容）

### D. GCP 基礎設置

1. 建立 GCP Project、Region、Billing
2. 啟用 Cloud Run、Cloud SQL Admin、Secret Manager、Cloud Storage API
3. 建立 Cloud SQL instance + database + user
4. 建立 Storage bucket（圖片）
5. 建立 service account，授最小權限 IAM

### E. 部署與切換

1. 先部署 staging（Cloud Run）
2. 將 staging 指向 Cloud SQL + Storage，完成功能測試
3. 壓力測試重點：登入、建立拍賣、出價、圖片上傳
4. 生產切換前凍結寫入，執行最終資料同步
5. 更新 DNS 或對外入口，完成 cutover
6. 保留 Render 版本作短期回滾方案

### F. 切換後觀測

1. 監控 Cloud Run error rate / latency / instance scaling
2. 監控 Cloud SQL CPU、連線數、慢查詢
3. 監控 Storage 錯誤與 egress 成本
4. 設定 alert（5xx、成本異常、磁碟/連線閾值）
5. 完成一輪備份與還原演練

## 13. Render x Cloudflare 連線檢查 Checklist

以下清單用於確認你個 domain 已正確由 Cloudflare 連去 Render，並且 HTTPS、快取、redirect 行為都正常。

### A. Render 端檢查

1. Render Web Service 狀態為 `Live`
2. Custom Domains 已加入：`hungjcc1223.com`（及 `www.hungjcc1223.com` 如有）
3. Domain 狀態為 `Verified` 或 `Active`
4. Start Command 使用 `./start.sh`（建議）
5. Environment 已設 `USE_DB=1`
6. 如用 Persistent Disk，`SQLITE_PATH=/data/iom.db`

### B. Cloudflare DNS 檢查

1. `@`（root）記錄指向 Render 提供的目標（A 或 CNAME flattening）
2. `www` 設 `CNAME` 指向 Render domain（或 root）
3. 無衝突舊記錄（重覆 A/CNAME、舊 hosting IP）
4. TTL 使用 Auto 或合理值（例如 300 秒）

### C. Proxy 與驗證流程

1. 首次驗證期間可先設 `DNS only`（灰雲）
2. Render 顯示 `Verified/Active` 後，再切回 `Proxied`（橙雲）
3. 切換後等待 DNS 傳播，再重測網站

### D. SSL / HTTPS 檢查

1. Cloudflare SSL/TLS mode 設 `Full (strict)`
2. 開啟 `Always Use HTTPS`
3. `https://hungjcc1223.com` 已經可以正常開啟且無憑證警告
4. `https://www.hungjcc1223.com` 行為符合預期（可開或 redirect）

### E. Redirect 與 Canonical 檢查

1. 決定 canonical domain（`www` 或 non-www 二選一）
2. 非 canonical 域名做 301 redirect 到 canonical
3. `http://` 會自動轉 `https://`
4. 網站內連結（logo/menu）都使用 canonical domain

### F. Cache / 安全檢查

1. 動態頁 bypass cache：`/user_login`, `/register`, `/auction/*`, `/admin*`
2. 靜態資源可快取：`/static/*`
3. 若登入狀態顯示異常，先清 Cloudflare cache 再重測
4. 啟用 Cloudflare WAF 後，確認未誤擋正常表單提交

### G. 功能 Smoke Test

1. 開啟首頁 `/`
2. 開啟 `/auctions` 及 `/search?key_word=test`
3. 測試註冊、登入、登出
4. 測試新增拍賣（含圖片）
5. 測試出價流程
6. 查看 Render logs，確認無持續 5xx

### H. 問題排查快速提示

1. Render 未驗證 domain：先把 Cloudflare proxy 改回灰雲再驗證
2. 有 SSL 錯誤：檢查 Cloudflare 模式是否 `Full (strict)`，不要用 `Flexible`
3. 跳去舊網站：清掉舊 DNS 記錄 + 等待 TTL
4. 登入後畫面不一致：檢查 cache rule 是否誤快取動態頁
# README — Quick Command Reference (SQLite Edition)

此專案現已完全改用 **SQLite** 作為本地資料庫，所有指令都可以直接在專案根目錄（`app.py` 所在位置）執行，毋須再設定 ODBC/pyodbc。

## 0. 建立虛擬環境（可選但建議）
```powershell
python -m venv .venv
& ".\.venv\Scripts\Activate.ps1"
python -m pip install -r requirements.txt
```

## 1. 初始化 / Reset SQLite DB
```powershell
# 第一次使用（或想 reset）
python tools\init_sqlite_db.py --reset

# 使用自訂路徑
python tools\init_sqlite_db.py --path "D:/data/iom.db"
```
- 預設會在 `iom/iom.db` 產生資料檔。
- 如要覆蓋指定路徑，可設 `SQLITE_PATH` 或使用 `--path` 參數。

## 2. 執行 Flask App
```powershell
set USE_DB=1
python app.py
```
- `USE_DB` 仍然存在，但現在使用 SQLite；未設定時會 fallback demo mode。

## 3. 重要工具指令
所有工具已透過 `db.get_connection()` 存取 SQLite，無需再設定 credential。

| 用途 | 指令 |
| --- | --- |
| 測試連線 | `python tools\test_conn.py` |
| 建立測試 item + auction | `python tools\create_item_and_auction.py` |
| 列出會員 | `python tools\list_members.py` |
| 調整 admin 權限 | `python tools\grant_revoke_admin.py grant 3` |
| 重設密碼 | `python tools\reset_password.py --username alice --password NewPass123!` |
| 冒煙測試 | `python tools\smoke_test.py` 或 `python tools\run_smoke_verbose.py` |
| 自動 bid | `python tools\auto_place_bid.py`（先啟動 app.py） |

> 舊有的 `odbc_schema_reader.py` / `show_pyodbc_drivers.py` 等純 SQL Server 工具仍保留作參考，但對 SQLite 不適用。

## 4. 常用環境變數
| 變數 | 用途 |
| --- | --- |
| `USE_DB` | 設為 `1` 即使用 SQLite DB；空值則進入 demo 模式 |
| `SQLITE_PATH` | 指定 `iom.db` 的完整路徑 |
| `CURRENCY_SYMBOL` / `CURRENCY_LABEL` | 控制顯示貨幣符號 |
| `SMTP_*` | 寄出註冊確認 email（可留空，預設 log） |

## 5. 單元測試
```powershell
python -m unittest tests.test_bids -v
python -m unittest discover -v
```

## 6. 其他備註
- `db_sqlserver.py` 保留舊版 SQL Server 連線實作，如需回滾可參考。
- `iom.db` 已加入 `.gitignore`（請勿提交真正數據）。
- 如果需要額外欄位/資料表，可在 `db.py` 的 `_SCHEMA_SQL` 裡擴充，再跑 `tools/init_sqlite_db.py --reset` 重新建庫。

如需更多 CLI 包裝或專用 script，通知我再增加。
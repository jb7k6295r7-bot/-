# tw-stock-data

台股資料的每日快照。**這個 repo 只放公開的市場資料**——沒有持股成本、張數、損益或任何個人資料。

## 為什麼有這個東西

Claude 的每日排程在凌晨無人值守時，每一條抓取網址都會跳出核可對話框，
沒有人按就逾時，結果是**完全拿不到資料**（2026-08-30、08-31 連續實測，一次都沒過）。

所以把「抓資料」搬到這裡——由你自己的 GitHub Actions 執行。
Claude 的排程只讀這個 repo 產出的**固定網址**檔案。

## 怎麼設定（一次就好）

1. 建一個**公開** repo（公開才不需要 token 就能讀 raw 檔）。
2. 把 `fetch.py`、`.github/workflows/daily.yml`、`data/latest/test.json` 放進去。
3. 到 repo 的 **Settings → Actions → General**，
   把 **Workflow permissions** 設成 **Read and write permissions**（腳本要 commit 回來）。
4. 到 **Actions** 分頁，如果看到「啟用 workflow」的提示就按下去。
5. 手動跑一次：Actions → 「每日台股資料」→ **Run workflow**。

## 產出

跑完之後 `data/latest/` 底下會有：

| 檔案 | 內容 |
|---|---|
| `<代號>.json` | 該檔的日K與三大法人，含 `rows`、`date_min`、`date_max`、來源網址 |
| `market.json` | 加權指數、成交統計、全市場三大法人（T86）、法人買賣金額 |
| `_manifest.json` | 這一輪的總表：誰成功、誰失敗、各自的最新資料日 |

固定網址長這樣（`<帳號>`、`<repo>` 換成你的）：

```
https://raw.githubusercontent.com/<帳號>/<repo>/main/data/latest/_manifest.json
https://raw.githubusercontent.com/<帳號>/<repo>/main/data/latest/7879.json
```

**檔名永遠不變，內容每天被覆蓋。**

## 這支腳本刻意不做的事

- **不推估、不內插、不補值。** 抓不到就在 `errors` 裡寫明原因。
- **不因為個別失敗就停整批**，但也**不會假裝成功**——`_manifest.json` 會列出每一項的狀態。
- **`stock_id` 逐筆核對，對不上就整批丟棄**。這是唯一能擋住「回傳別檔資料但價格看起來很正常」的防線。
- **`target_trading_day` 只是查詢用的猜測值**，真正的資料基準日以各檔的 `date_max` 為準。

## 要改追蹤的股票

編輯 `fetch.py` 最上面的 `STOCKS` 清單。只放代號。

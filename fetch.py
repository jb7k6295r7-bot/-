#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股資料抓取腳本 —— 在「你自己的」GitHub Actions 上執行。

為什麼存在：Claude 的排程在凌晨無人值守時，WebFetch 每一條網址都會跳核可對話框，
沒有人按就逾時，什麼都拿不到（2026-08-30 / 08-31 連續實測）。
這支腳本把「抓資料」搬到你自己這端——那是你本來就有權做的事——
Claude 的排程只讀這個 repo 產出的固定網址檔案。

設計原則（照專案的資料品質規範走）：
  1. 只用標準函式庫，不需要 pip install。
  2. 每一批資料都驗 stock_id 與日期，**驗不過就標 failed，絕不靜默填補**。
  3. 缺的就寫缺。任何欄位都不推估、不內插。
  4. 每個檔案都帶 meta（來源網址、抓取時間、筆數、最新日期），讓下游能自己複查。

輸出：
  data/latest/<代號>.json   每檔的日K與三大法人
  data/latest/market.json   大盤指數、成交統計、全市場三大法人
  data/latest/_manifest.json 這一輪的總表（誰成功誰失敗、基準日是哪天）
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# ────────────────────────────────────────────────────────────
# 設定：要抓哪些股票
# 只放代號。這裡不放持股成本、張數或任何個人資料——repo 是公開的。
# ────────────────────────────────────────────────────────────
STOCKS = [
    "7879",  # 益材科技（興櫃）
    "8289",  # 泰藝
    "2609",  # 陽明
    "6209",  # 今國光
    "3017",  # 奇鋐
    "8996",  # 高力
]

PRICE_START = "2025-01-01"   # 日K起日：要夠長才算得出 240MA
INST_START = "2026-01-01"    # 三大法人起日

TPE = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (compatible; tw-stock-data/1.0; +https://github.com/)"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "latest")

FINMIND = "https://api.finmindtrade.com/api/v4/data"
TWSE = "https://www.twse.com.tw/rwd/zh"


def now_tpe():
    return datetime.now(TPE)


def get(url, retries=3, timeout=30):
    """回傳 (bytes, error)。失敗不丟例外，讓呼叫端決定怎麼記錄。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), None
        except Exception as e:  # noqa: BLE001 — 這裡就是要吞下所有錯並記錄
            last = f"{type(e).__name__}: {e}"
            if i < retries - 1:
                time.sleep(2 * (i + 1))
    return None, last


def get_json(url, **kw):
    raw, err = get(url, **kw)
    if err:
        return None, err
    try:
        return json.loads(raw.decode("utf-8")), None
    except Exception as e:  # noqa: BLE001
        return None, f"JSON 解析失敗 {type(e).__name__}: {e}"


def finmind(dataset, data_id, start, end):
    url = (f"{FINMIND}?dataset={dataset}&data_id={data_id}"
           f"&start_date={start}&end_date={end}")
    d, err = get_json(url)
    if err:
        return None, url, err
    if not isinstance(d, dict) or d.get("msg") != "success":
        return None, url, f"回傳非 success：{str(d)[:200]}"
    rows = d.get("data") or []
    if not rows:
        return None, url, "回傳 0 筆"
    return rows, url, None


def check_stock_id(rows, code):
    """逐筆核對代號。這是唯一能擋住『張冠李戴』的防線，不可省。"""
    bad = {str(r.get("stock_id")) for r in rows} - {str(code)}
    if bad:
        return f"stock_id 不符，出現 {sorted(bad)[:5]}"
    return None


def date_range(rows):
    ds = sorted(str(r.get("date", "")) for r in rows if r.get("date"))
    return (ds[0], ds[-1]) if ds else ("", "")


def fetch_stock(code, today):
    """單一檔股票。任何一項失敗都照實記錄，不影響其他項。"""
    out = {
        "stock_id": code,
        "fetched_at": now_tpe().isoformat(timespec="seconds"),
        "price": None,
        "institutional": None,
        "errors": {},
    }

    rows, url, err = finmind("TaiwanStockPrice", code, PRICE_START, today)
    if err:
        out["errors"]["price"] = err
    else:
        bad = check_stock_id(rows, code)
        if bad:
            out["errors"]["price"] = bad          # 整批丟棄，不是只換欄位
        else:
            lo, hi = date_range(rows)
            out["price"] = {
                "source": url, "rows": len(rows),
                "date_min": lo, "date_max": hi, "data": rows,
            }

    rows, url, err = finmind(
        "TaiwanStockInstitutionalInvestorsBuySell", code, INST_START, today)
    if err:
        out["errors"]["institutional"] = err
    else:
        bad = check_stock_id(rows, code)
        if bad:
            out["errors"]["institutional"] = bad
        else:
            lo, hi = date_range(rows)
            out["institutional"] = {
                "source": url, "rows": len(rows),
                "date_min": lo, "date_max": hi, "data": rows,
            }

    return out


def fetch_market(today):
    """大盤：指數、成交統計、全市場三大法人。

    T86 經 WebFetch 會被靜默截斷（實測），從這裡抓則是完整的原始回應。
    """
    ymd = today.replace("-", "")
    out = {"fetched_at": now_tpe().isoformat(timespec="seconds"), "errors": {}}

    targets = {
        # 發行量加權股價指數（收盤指數與漲跌）
        "index": f"{TWSE}/afterTrading/MI_INDEX?date={ymd}&type=IND&response=json",
        # 成交金額、漲跌家數等大盤統計
        "summary": f"{TWSE}/afterTrading/MI_INDEX?date={ymd}&type=MS&response=json",
        # 全市場三大法人買賣超
        "t86": f"{TWSE}/fund/T86?date={ymd}&selectType=ALL&response=json",
        # 三大法人買賣金額總表
        "bfi82u": f"{TWSE}/fund/BFI82U?dayDate={ymd}&type=day&response=json",
    }
    for key, url in targets.items():
        d, err = get_json(url)
        if err:
            out["errors"][key] = err
            continue
        stat = (d or {}).get("stat")
        if stat and stat != "OK":
            # TWSE 對休市日／查無資料會回一段中文，照抄不改寫
            out["errors"][key] = f"stat={stat}"
            continue
        out[key] = {"source": url, "data": d}
    return out


def latest_trading_day_guess(now):
    """只用來決定要跟 TWSE 要哪一天，**不是**報告基準日的判定。

    真正的基準日以抓回來的資料為準（見 manifest 的 data_max）。
    """
    d = now
    if d.hour < 15:          # 當日收盤資料尚未齊全，往前一天
        d -= timedelta(days=1)
    while d.weekday() >= 5:  # 週六日往前推
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    now = now_tpe()
    today = latest_trading_day_guess(now)
    print(f"[tw-stock-data] 台北時間 {now:%Y-%m-%d %H:%M}，目標交易日 {today}")

    manifest = {
        "generated_at": now.isoformat(timespec="seconds"),
        "target_trading_day": today,
        "stocks": {},
        "market": {},
        "note": "date_max 才是真正的資料基準日；target_trading_day 只是查詢用的猜測值。",
    }

    for code in STOCKS:
        data = fetch_stock(code, today)
        path = os.path.join(OUT_DIR, f"{code}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        manifest["stocks"][code] = {
            "price_rows": (data["price"] or {}).get("rows"),
            "price_date_max": (data["price"] or {}).get("date_max"),
            "inst_rows": (data["institutional"] or {}).get("rows"),
            "inst_date_max": (data["institutional"] or {}).get("date_max"),
            "errors": data["errors"] or None,
        }
        print(f"  {code}: price={manifest['stocks'][code]['price_date_max']} "
              f"inst={manifest['stocks'][code]['inst_date_max']} "
              f"err={data['errors'] or '-'}")

    market = fetch_market(today)
    with open(os.path.join(OUT_DIR, "market.json"), "w", encoding="utf-8") as f:
        json.dump(market, f, ensure_ascii=False, separators=(",", ":"))
    manifest["market"] = {
        "ok": [k for k in ("index", "summary", "t86", "bfi82u") if k in market],
        "errors": market["errors"] or None,
    }
    print(f"  market: ok={manifest['market']['ok']} err={market['errors'] or '-'}")

    with open(os.path.join(OUT_DIR, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 全軍覆沒才視為執行失敗；個別失敗照實記錄在 manifest 裡，不讓整批停擺
    got = [c for c in STOCKS if manifest["stocks"][c]["price_date_max"]]
    if not got:
        print("[tw-stock-data] 所有個股都沒抓到，視為失敗", file=sys.stderr)
        return 1
    print(f"[tw-stock-data] 完成：{len(got)}/{len(STOCKS)} 檔有日K")
    return 0


if __name__ == "__main__":
    sys.exit(main())

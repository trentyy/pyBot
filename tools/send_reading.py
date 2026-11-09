"""把儀器數值送到 dc-manager（放在儀器電腦上執行，只用 Python 標準函式庫）。

送一次：
    python send_reading.py --url http://192.168.1.50:8080 --token dcm_xxx --metric furnace1.temp --value 852.3

每 30 秒讀 CSV 最後一列的某個欄位送出（適合儀器軟體只會寫 log 檔的情況）：
    python send_reading.py --url http://192.168.1.50:8080 --token dcm_xxx --metric furnace1.temp \
        --csv "C:/FurnaceLog/today.csv" --column Temperature --watch 30

token 也可以放在環境變數 DCM_TOKEN，避免出現在命令列紀錄裡。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def send(url: str, token: str, readings: list[dict]) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/v1/readings",
        data=json.dumps({"readings": readings}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("error", "")
        except ValueError:
            detail = ""
        raise RuntimeError(f"HTTP {e.code} {detail}") from None


def last_csv_value(path: Path, column: str, encoding: str) -> float:
    with path.open(newline="", encoding=encoding, errors="replace") as f:
        rows = list(csv.DictReader(f))
    for row in reversed(rows):
        raw = (row.get(column) or "").strip()
        if raw:
            return float(raw)
    raise ValueError(f"{path} 找不到欄位 {column} 的數值")


def main() -> int:
    p = argparse.ArgumentParser(description="送數值給 dc-manager")
    p.add_argument("--url", required=True, help="例如 http://192.168.1.50:8080")
    p.add_argument("--token", default=os.getenv("DCM_TOKEN"), help="/alarm source create 產生的 token")
    p.add_argument("--metric", required=True)
    p.add_argument("--value", type=float, help="直接送這個數值")
    p.add_argument("--csv", type=Path, help="從 CSV 檔最後一列讀數值")
    p.add_argument("--column", help="CSV 欄位名稱")
    p.add_argument("--encoding", default="utf-8-sig", help="CSV 編碼（中文 Windows 軟體可試 cp950）")
    p.add_argument("--watch", type=int, default=0, help="每幾秒送一次（0 = 只送一次）")
    args = p.parse_args()

    if not args.token:
        p.error("需要 --token 或環境變數 DCM_TOKEN")
    if args.value is None and not (args.csv and args.column):
        p.error("需要 --value，或 --csv 加 --column")

    while True:
        try:
            value = args.value if args.value is not None else last_csv_value(args.csv, args.column, args.encoding)
            result = send(args.url, args.token, [{"metric": args.metric, "value": value, "ts": int(time.time())}])
            print(time.strftime("%H:%M:%S"), args.metric, value, result)
        except Exception as e:  # 網路斷線等錯誤不要讓程式結束
            print(time.strftime("%H:%M:%S"), "失敗：", e, file=sys.stderr)
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())

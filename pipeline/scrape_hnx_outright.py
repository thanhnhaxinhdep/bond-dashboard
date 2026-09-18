"""
HNX CBONDS - Daily Scraper
Scrape bảng chào giá Outright, Repo, và thông tin trái phiếu từ cbonds.hnx.vn

Quy trình:
  - Scrape data qua WebSocket (không cần đăng nhập)
  - Lưu file CSV hàng ngày vào thư mục /daily/
  - Append vào SQLite (file mẹ) để tính toán dài hạn

Cài đặt (1 lần):
    python -m pip install websocket-client pandas openpyxl

Cách chạy:
    python scrape_hnx_outright.py
    python scrape_hnx_outright.py --wait 15 --out D:/Data/HNX
    python scrape_hnx_outright.py --export-excel
    python scrape_hnx_outright.py --export-excel --from-date 2026-06-01 --to-date 2026-06-30
"""

import argparse
import sqlite3
import sys
import time
import threading
import ssl
import pandas as pd
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import websocket

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─── Constants ───────────────────────────────────────────────────────────────

WS_URL        = "wss://cbonds.hnx.vn/board/ws"
TZ_VN         = timezone(timedelta(hours=7))
ALL_BOARD_IDS = [18, 1, 8, 5, 2, 6, 7, 4, 98, 56, 10]

BOARD_NAMES = {
    8:  "bond_info",
    56: "outright",
    98: "repo",
}

FIELD_MAPS = {
    8: {
        0:  "code",
        1:  "symbol",
        2:  "boardId",
        3:  "moneyTypeId",
        4:  "termRemainId",
        5:  "periodRemain",
        6:  "bondPeriod",
        7:  "issuerName",
        8:  "maturityDate",
        9:  "interestRate",
        10: "nePrice",         # giá khớp gần nhất
        11: "neQtty",          # KL khớp gần nhất
        12: "hiPrice",         # giá cao nhất
        13: "hiQtty",          # KL tại giá cao nhất
        14: "loPrice",         # giá thấp nhất
        15: "loQtty",          # KL tại giá thấp nhất
        16: "toValue",         # tổng giá trị GD
        17: "hiBuyPrice",      # giá mua tốt nhất
        18: "hiBuyQtty",       # KL mua tốt nhất
        19: "toBuyQtty",       # tổng KL đặt mua
        20: "loSellPrice",     # giá bán tốt nhất
        21: "loSellQtty",      # KL bán tốt nhất
        22: "toSellQtty",      # tổng KL đặt bán
        23: "toTradingQtty",   # tổng KL giao dịch ← Total Volume
        24: "parvalue",        # mệnh giá
        25: "termRemain",      # nhóm kỳ hạn còn lại
        26: "interestRateType",
        27: "ceilPrice",
        28: "floorPrice",
        29: "referencePrice",
        30: "buy1Price",
        31: "buy1Qtty",
        32: "buy2Price",
        33: "buy2Qtty",
        34: "buy3Price",
        35: "buy3Qtty",
        36: "exPrice",
        37: "exQtty",
        38: "sell1Price",
        39: "sell1Qtty",
        40: "sell2Price",
        41: "sell2Qtty",
        42: "sell3Price",
        43: "sell3Qtty",
        44: "toExecQtty",
        45: "toExecValue",
        46: "hiExPrice",
        47: "loExPrice",
    },
    56: {
        1:  "inquiryKey",
        2:  "symbol",
        3:  "sideText",
        4:  "repoPeriod",
        5:  "boardId",
        6:  "moneyTypeId",
        7:  "termRemainId",
        8:  "periodRemain",
        9:  "bondPeriod",
        10: "issuerName",
        11: "maturityDate",
        12: "interestRate",
        13: "parvalue",
        14: "termRemain",
        15: "interestRateType",
        16: "neParVal",
        17: "hiParVal",
        18: "loParVal",
        19: "toTradingValue",
        20: "isRemoved",
    },
    98: {
        1:  "repoKey",
        2:  "symbol",
        3:  "repoPeriod",
        4:  "boardId",
        5:  "moneyTypeId",
        6:  "termRemainId",
        7:  "periodRemain",
        8:  "bondPeriod",
        9:  "issuerName",
        10: "maturityDate",
        11: "interestRate",
        12: "parvalue",
        13: "termRemain",
        14: "interestRateType",
        15: "hiTradingValue",
        16: "hiRepoIntRate",
        17: "hiHedgeRatio",
        18: "hiToRepoIntQtty",
        19: "loTradingValue",
        20: "loRepoIntRate",
        21: "loHedgeRatio",
        22: "loToRepoIntQtty",
        23: "isRemoved",
    },
}

# ─── Parser ───────────────────────────────────────────────────────────────────

def parse_records(raw: str, field_map: dict) -> dict[str, dict]:
    """Parse '$'-separated records: 'fid*val*color|fid*val|...'"""
    result = {}
    for rec_str in raw.split("$"):
        if not rec_str.strip():
            continue
        record = {}
        key = None
        for frag in rec_str.split("|"):
            parts = frag.split("*")
            if not parts or not parts[0]:
                continue
            try:
                fid = int(parts[0])
            except ValueError:
                continue
            val = parts[1] if len(parts) > 1 else ""
            record[field_map.get(fid, f"f{fid}")] = val
            if fid == 1:
                key = val
        if key and record:
            result[key] = record
    return result

# ─── Scraper ──────────────────────────────────────────────────────────────────

class HNXScraper:
    def __init__(self, board_ids: list[int], wait_secs: int):
        self.board_ids     = board_ids
        self.wait_secs     = wait_secs
        self.data          = {bid: {} for bid in FIELD_MAPS}
        self.market_status = "unknown"
        self.market_date   = ""
        self._done         = threading.Event()

    def run(self):
        ws = websocket.WebSocketApp(
            WS_URL,
            on_open    = self._on_open,
            on_message = self._on_message,
            on_error   = lambda ws, e: print(f"[WS error] {e}"),
            on_close   = lambda ws, c, m: self._done.set(),
            header     = {
                "Origin":     "https://cbonds.hnx.vn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        t = threading.Thread(
            target=ws.run_forever,
            kwargs={"ping_interval": 30, "sslopt": {"cert_reqs": ssl.CERT_NONE}},
            daemon=True,
        )
        t.start()
        print(f"Cho {self.wait_secs}s de nhan du lieu...")
        self._done.wait(timeout=self.wait_secs + 5)
        ws.close()

    def _on_open(self, ws):
        print(f"Ket noi: {WS_URL}")
        for bid in ALL_BOARD_IDS:
            ws.send(f"11*{bid}")
        threading.Timer(self.wait_secs, self._done.set).start()

    def _on_message(self, ws, message: str):
        try:
            tilde = message.index("~")
        except ValueError:
            return
        header = message[:tilde]
        data   = message[tilde + 1:]
        try:
            board_id = int(header.split("*")[1])
        except (IndexError, ValueError):
            return

        if board_id == 2 and data:
            parts = {}
            for p in data.split("|"):
                if "*" in p:
                    k, v = p.split("*", 1)
                    try:
                        parts[int(k)] = v
                    except ValueError:
                        pass
            self.market_status = parts.get(2, "")
            self.market_date   = parts.get(3, "")

        if board_id in FIELD_MAPS and data.strip():
            if board_id in self.board_ids:
                records = parse_records(data, FIELD_MAPS[board_id])
                self.data[board_id].update(records)

    def get_dataframe(self, board_id: int) -> pd.DataFrame:
        records = self.data.get(board_id, {})
        if not records:
            return pd.DataFrame()
        df      = pd.DataFrame(list(records.values()))
        ordered = [v for v in FIELD_MAPS[board_id].values() if v in df.columns]
        extra   = [c for c in df.columns if c not in ordered]
        return df[ordered + extra]

# ─── Transform ────────────────────────────────────────────────────────────────

def transform(df: pd.DataFrame, name: str, now: datetime) -> pd.DataFrame:
    """Rename columns, select, add scrape timestamps."""
    df = df.copy()
    df["scrape_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
    df["scrape_day"]  = now.strftime("%Y-%m-%d")

    if name == "bond_info":
        rename_map = {
            "symbol":         "Symbol",
            "issuerName":     "Issuer Name",
            "maturityDate":   "Maturity Date",
            "interestRate":   "Interest Rate",
            "toValue":        "Total Value",
            "toTradingQtty":  "Total Volume",
            "nePrice":        "Last Price",
            "hiPrice":        "High Price",
            "loPrice":        "Low Price",
            "parvalue":       "Par Value",
            "termRemain":     "Term Remain",
        }
        keep = ["Symbol", "Issuer Name", "Maturity Date", "Interest Rate",
                "Par Value", "Term Remain", "Last Price", "High Price", "Low Price",
                "Total Value", "Total Volume", "scrape_time", "scrape_day"]

    elif name == "outright":
        rename_map = {
            "symbol":         "Symbol",
            "issuerName":     "Issuer Name",
            "maturityDate":   "Maturity Date",
            "interestRate":   "Interest Rate",
            "parvalue":       "Par Value",
            "toTradingValue": "Total Trading Value",
        }
        keep = ["Symbol", "Issuer Name", "Maturity Date", "Interest Rate",
                "Par Value", "Total Trading Value", "scrape_time", "scrape_day"]

    elif name == "repo":
        rename_map = {
            "symbol":          "Symbol",
            "issuerName":      "Issuer Name",
            "maturityDate":    "Maturity Date",
            "interestRate":    "Interest Rate",
            "parvalue":        "Par Value",
            "hiTradingValue":  "Highest Trading Value",
            "loTradingValue":  "Lowest Trading Value",
        }
        keep = ["Symbol", "Issuer Name", "Maturity Date", "Interest Rate",
                "Par Value", "Highest Trading Value", "Lowest Trading Value",
                "scrape_time", "scrape_day"]

    else:
        rename_map = {}
        keep = list(df.columns)

    df = df.rename(columns=rename_map)

    # Parse Maturity Date
    date_col = "Maturity Date" if "Maturity Date" in df.columns else None
    if date_col:
        df[date_col] = pd.to_datetime(
            df[date_col], format="%d/%m/%Y", errors="coerce"
        ).dt.date

    df = df[[c for c in keep if c in df.columns]]
    return df

# ─── Storage ─────────────────────────────────────────────────────────────────

def save_daily_csv(df: pd.DataFrame, name: str, daily_dir: Path, now: datetime) -> Path:
    ts   = now.strftime("%Y%m%d_%H%M%S")
    path = daily_dir / f"{name}_{ts}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def append_to_sqlite(df: pd.DataFrame, table: str, db_path: Path, today: str):
    """Append DataFrame vào SQLite, rồi dedup: giữ scrape trễ nhất mỗi Symbol/ngày."""
    with sqlite3.connect(db_path) as conn:
        df.to_sql(table, conn, if_exists="append", index=False)
        removed = deduplicate_table(conn, table)
        count = conn.execute(
            f"SELECT COUNT(*) FROM [{table}] WHERE scrape_day = ?", (today,)
        ).fetchone()[0]
    msg = f"  SQLite [{table}]: +{count} dong (ngay {today})"
    if removed:
        msg += f", xoa {removed} ban ghi cu"
    print(msg)


def deduplicate_table(conn: sqlite3.Connection, table: str) -> int:
    """Với mỗi Symbol + scrape_day, giữ bản ghi có scrape_time trễ nhất, xóa phần còn lại."""
    try:
        before = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
        conn.execute(f"""
            DELETE FROM [{table}]
            WHERE rowid NOT IN (
                SELECT MAX(rowid)
                FROM [{table}]
                GROUP BY Symbol, scrape_day
            )
        """)
        after = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
        return before - after
    except sqlite3.OperationalError:
        return 0


def deduplicate_db(db_path: Path) -> dict:
    """Chạy dedup trên toàn bộ database. Trả về số bản ghi đã xóa theo từng bảng."""
    results = {}
    with sqlite3.connect(db_path) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for table in tables:
            removed = deduplicate_table(conn, table)
            results[table] = removed
    return results


def export_excel(db_path: Path, output_dir: Path,
                 from_date: str = None, to_date: str = None):
    """Xuất tất cả bảng từ SQLite ra một file Excel nhiều sheet."""
    with sqlite3.connect(db_path) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]

        now       = datetime.now(TZ_VN)
        xlsx_path = output_dir / f"export_{now.strftime('%Y%m%d_%H%M%S')}.xlsx"

        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            for tbl in tables:
                query  = f"SELECT * FROM [{tbl}]"
                conds  = []
                params = []
                if from_date:
                    conds.append("scrape_day >= ?")
                    params.append(from_date)
                if to_date:
                    conds.append("scrape_day <= ?")
                    params.append(to_date)
                if conds:
                    query += " WHERE " + " AND ".join(conds)
                df = pd.read_sql(query, conn, params=params)
                df.to_excel(writer, sheet_name=tbl[:31], index=False)
                print(f"  Sheet [{tbl}]: {len(df)} dong")

    print(f"Xuat Excel: {xlsx_path}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="HNX CBONDS Daily Scraper")
    ap.add_argument("--wait",         type=int, default=5,
                    help="Giay cho nhan du lieu (default: 5)")
    ap.add_argument("--out",          type=str, default="D:/Data/HNX",
                    help="Thu muc goc luu du lieu (default: D:/Data/HNX)")
    ap.add_argument("--boards",       type=int, nargs="+", default=[8, 56, 98],
                    help="Board IDs (default: 8 56 98)")
    ap.add_argument("--export-excel", action="store_true",
                    help="Xuat SQLite ra Excel roi thoat")
    ap.add_argument("--from-date",    type=str, default=None,
                    help="Loc tu ngay YYYY-MM-DD (dung kem --export-excel)")
    ap.add_argument("--to-date",      type=str, default=None,
                    help="Loc den ngay YYYY-MM-DD (dung kem --export-excel)")
    args = ap.parse_args()

    root_dir  = Path(args.out)
    daily_dir = root_dir / "daily"
    db_path   = root_dir / "hnx_bonds.db"
    root_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(exist_ok=True)

    # Chế độ export Excel
    if args.export_excel:
        print(f"Xuat Excel tu {db_path} ...")
        export_excel(db_path, root_dir, from_date=args.from_date, to_date=args.to_date)
        return

    # Scrape
    now   = datetime.now(TZ_VN)
    today = now.strftime("%Y-%m-%d")

    scraper = HNXScraper(board_ids=args.boards, wait_secs=args.wait)
    scraper.run()

    print(f"\nTrang thai: {scraper.market_status} ({scraper.market_date})")
    print("=" * 60)

    for board_id in args.boards:
        if board_id not in FIELD_MAPS:
            continue
        name = BOARD_NAMES.get(board_id, f"board_{board_id}")
        raw_df = scraper.get_dataframe(board_id)

        if raw_df.empty:
            print(f"[{name}] Khong co du lieu")
            continue

        df = transform(raw_df, name, now)

        # 1. CSV hàng ngày
        csv_path = save_daily_csv(df, name, daily_dir, now)
        print(f"[{name}] {len(df)} dong -> daily/{csv_path.name}")

        # 2. Append SQLite
        append_to_sqlite(df, name, db_path, today)

    print("=" * 60)
    print(f"Hoan thanh! SQLite: {db_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tpdn_scraper.py  -  Scrape DU LIEU BEN TRONG file Excel dinh kem cua cac tin
                    "Ket qua giao dich Trai phieu doanh nghiep" tren HNX.

Nguon: https://hnx.vn/tin-tuc-su-kien-ttcbhnx.html  (sub-tab HNX_PUBLIC)
Moi tin "Ket qua giao dich Trai phieu doanh nghiep ngay DD/MM/YYYY" dinh 1 file .xls
chua ket qua giao dich TPDN toan thi truong ngay do (moi dong = 1 ma trai phieu):
    STT | Ma CK | Gia dong cua | KLGD(Khop lenh) | GTGD(Khop lenh)
        | KLGD(Thoa thuan) | GTGD(Thoa thuan) | Tong KLGD | Tong GTGD     (don vi: VND)

Luong:
  1. Liet ke cac tin ket qua GD (dung ttcb_scraper, loc tieu de).
  2. Voi moi ngay CHUA co trong file tong -> lay link file dinh kem -> tai .xls -> boc bang.
  3. Append vao tong + dedup theo (trading_date, bond_code).

Chay:
  python tpdn_scraper.py              # incremental: chi tai nhung NGAY moi (dung hang ngay)
  python tpdn_scraper.py --all        # backfill: tai tat ca (~1747 ngay, lau)
  python tpdn_scraper.py --days 30    # chi 30 ngay giao dich gan nhat
  python tpdn_scraper.py --force      # tai lai ca nhung ngay da co

Yeu cau: requests, pandas, xlrd  (xlrd de doc .xls cu)
"""

import argparse
import html as H
import io
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import time
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

try:
    import xlrd
except ImportError:
    xlrd = None
try:
    import openpyxl
except ImportError:
    openpyxl = None

import ttcb_scraper as ttcb  # tai dung logic liet ke tin

warnings.simplefilter("ignore")

BASE = "https://hnx.vn"
ATTACH_ENDPOINT = "/ModuleArticles/ArticlesCPEtfs/ArticlesFileAttach"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "tpdn_data"
MASTER_NAME = "TPDN_trading.csv"

TITLE_MATCH = "Kết quả giao dịch Trái phiếu"

COLUMNS = ["trading_date", "bond_code", "close_price",
           "kl_khoplenh", "gt_khoplenh", "kl_thoathuan", "gt_thoathuan",
           "total_volume", "total_value", "unit",
           "article_id", "post_date", "scrape_date", "scrape_time"]


# ------------------------------------------------------------- liet ke tin
def list_result_articles(session):
    """Tra ve list dict {article_id, trading_date, post_date, title} cac tin ket qua GD."""
    rows = ttcb.scrape_subtab(session, "HNX_PUBLIC", "HNX_PUBLIC")
    out = []
    for r in rows:
        if TITLE_MATCH not in r["title"]:
            continue
        m = re.search(r"ng[àa]y\s+(\d{2}/\d{2}/\d{4})", r["title"])
        if not m:
            continue
        try:
            td = datetime.strptime(m.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        out.append({"article_id": r["article_id"], "trading_date": td,
                    "post_date": r["post_date"], "title": r["title"]})
    # moi nhat truoc
    out.sort(key=lambda x: x["trading_date"], reverse=True)
    return out


# ------------------------------------------------------------- file dinh kem
def get_attachment_url(session, article_id):
    """Tra ve URL file spreadsheet dinh kem (uu tien .xls/.xlsx)."""
    r = session.post(BASE + ATTACH_ENDPOINT, data={"pArticlesID": article_id},
                     timeout=40, verify=session.verify)
    r.raise_for_status()
    r.encoding = "utf-8"
    hrefs = re.findall(r'href="([^"]+)"', r.text)
    hrefs = [H.unescape(h) for h in hrefs if h.lower().startswith("http")]
    spreads = [h for h in hrefs if h.lower().split("?")[0].endswith((".xls", ".xlsx"))]
    return (spreads or hrefs or [None])[0]


def download(session, url):
    r = session.get(url, timeout=60, verify=session.verify)
    r.raise_for_status()
    return r.content


# ------------------------------------------------------------- boc bang excel
def load_grid(content):
    """Doc file Excel (bytes) -> luoi 2D (list cac dong). Nhan dang .xls / .xlsx / HTML."""
    head = content[:8]
    # .xlsx / .xlsm = ZIP (PK)
    if head[:2] == b"PK":
        if openpyxl is None:
            raise RuntimeError("thieu openpyxl cho .xlsx")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
        return grid
    # .xls cu = OLE2 (D0CF11E0)
    if head[:4] == b"\xd0\xcf\x11\xe0":
        if xlrd is None:
            raise RuntimeError("thieu xlrd cho .xls")
        wb = xlrd.open_workbook(file_contents=content)
        sh = wb.sheet_by_index(0)
        return [[sh.cell_value(i, j) for j in range(sh.ncols)] for i in range(sh.nrows)]
    # HTML-table gia dang .xls
    if b"<table" in content[:4000].lower() or head[:1] in (b"<", b"\xef"):
        tables = pd.read_html(io.BytesIO(content))
        if tables:
            return tables[0].values.tolist()
    raise RuntimeError("dinh dang file khong nhan dang duoc")


def parse_tpdn(content):
    """Boc bang ket qua GD TPDN tu noi dung file Excel (bytes). Tra ve list dong dict.

    Ho tro 2 layout (deu cung THU TU cot):
      STT | Ma | Gia | KLGD_khop | GTGD_khop | KLGD_thoathuan | GTGD_thoathuan | Tong_KL | Tong_GT
    - Moi (2020+): 1 dong header 'Ma chung khoan' / 'Gia dong cua' ...
    - Cu  (2019) : header 2 dong 'Ma trai phieu' / 'Gia giao dich', nhan KLGD/GTGD tach dong.
    Nen map cot THEO VI TRI tinh tu cot 'Ma'.
    """
    grid = load_grid(content)
    ncols = max((len(r) for r in grid), default=0)

    def cell(i, j):
        row = grid[i]
        return row[j] if j < len(row) and row[j] is not None else ""

    # tim dong header + cot 'Ma ...'
    hdr = code_col = None
    for i in range(min(25, len(grid))):
        for j in range(ncols):
            c = str(cell(i, j)).lower().replace("\n", " ").strip()
            if c.startswith("mã") and ("trái phiếu" in c or "chứng khoán" in c or "ck" in c):
                hdr, code_col = i, j
                break
        if hdr is not None:
            break
    if hdr is None:
        return _parse_fallback(grid, ncols)  # layout la (vd ban tieng Anh)

    # map theo vi tri (cung thu tu o ca 2 format)
    pos = {
        "close_price": code_col + 1,
        "kl_khoplenh": code_col + 2,
        "gt_khoplenh": code_col + 3,
        "kl_thoathuan": code_col + 4,
        "gt_thoathuan": code_col + 5,
        "total_volume": code_col + 6,
        "total_value": code_col + 7,
    }
    if pos["total_value"] >= ncols:
        return _parse_fallback(grid, ncols)  # thieu cot -> thu map theo ten

    def num(i, key):
        v = cell(i, pos[key])
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).replace(",", "").replace(" ", "").strip())
        except (ValueError, AttributeError):
            return None

    rows = []
    for i in range(hdr + 1, len(grid)):
        code = str(cell(i, code_col)).strip()
        if not re.match(r"^[A-Za-z0-9]{6,}$", code):  # bo dong sub-header / 'TONG' / rong
            continue
        rows.append({
            "bond_code": code,
            "close_price": num(i, "close_price"),
            "kl_khoplenh": num(i, "kl_khoplenh"),
            "gt_khoplenh": num(i, "gt_khoplenh"),
            "kl_thoathuan": num(i, "kl_thoathuan"),
            "gt_thoathuan": num(i, "gt_thoathuan"),
            "total_volume": num(i, "total_volume"),
            "total_value": num(i, "total_value"),
        })
    if rows:
        return rows
    return _parse_fallback(grid, ncols)  # layout la -> vot theo ten cot


def _parse_fallback(grid, ncols):
    """Fallback cho layout dac biet: map cot theo TEN (gop header + dong ke tiep).
    Vot toi thieu: bond_code, total_volume, total_value, close_price (neu co)."""
    def cell(i, j):
        row = grid[i]
        return row[j] if j < len(row) and row[j] is not None else ""

    def coltext(hdr, j):
        t = str(cell(hdr, j))
        nx = cell(hdr + 1, j) if hdr + 1 < len(grid) else ""
        if isinstance(nx, str):
            t += " " + nx
        return t.lower().replace("\n", " ").replace("\xa0", " ").strip()

    hdr = code_col = None
    for i in range(min(25, len(grid))):
        for j in range(ncols):
            c = str(cell(i, j)).lower().replace("\n", " ").strip()
            if c.startswith("mã") or c == "code" or c.startswith("mã ck"):
                hdr, code_col = i, j
                break
        if hdr is not None:
            break
    if hdr is None:
        return []

    tv = tval = price = None
    for j in range(ncols):
        c = coltext(hdr, j)
        no_split = ("thỏa thuận" not in c and "thoa thuan" not in c
                    and "khớp" not in c and "khop" not in c
                    and "negotiation" not in c and "matching" not in c)
        if tv is None and ("tổng" in c or "total" in c) and ("kl" in c or "khối lượng" in c or "volume" in c) and no_split:
            tv = j
        if tval is None and ("tổng" in c or "total" in c) and ("gt" in c or "giá trị" in c or "value" in c) and no_split:
            tval = j
        if price is None and (("giá" in c and ("đóng cửa" in c or "giao dịch" in c)) or "closing price" in c):
            price = j
    if tv is None or tval is None:
        return []

    def num(i, j):
        if j is None:
            return None
        v = cell(i, j)
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).replace(",", "").replace(" ", "").strip())
        except (ValueError, AttributeError):
            return None

    rows = []
    for i in range(hdr + 1, len(grid)):
        code = str(cell(i, code_col)).strip()
        if not re.match(r"^[A-Za-z0-9]{6,}$", code):
            continue
        rows.append({
            "bond_code": code, "close_price": num(i, price),
            "kl_khoplenh": None, "gt_khoplenh": None,
            "kl_thoathuan": None, "gt_thoathuan": None,
            "total_volume": num(i, tv), "total_value": num(i, tval),
        })
    return rows


# ------------------------------------------------------------- tong hop
def load_existing_dates():
    master = DATA_DIR / MASTER_NAME
    if not master.exists():
        return set(), None
    df = pd.read_csv(master, dtype=str)
    return set(df["trading_date"].unique()), df


def dedup(df):
    if df.empty:
        return df
    df = df.drop_duplicates()
    ts = pd.to_datetime(df["scrape_date"] + " " + df["scrape_time"], errors="coerce")
    df = df.assign(_ts=ts).sort_values("_ts")
    df = df.drop_duplicates(subset=["trading_date", "bond_code"], keep="last").drop(columns="_ts")
    df = df.sort_values(["trading_date", "bond_code"], ascending=[False, True])
    return df.reset_index(drop=True)


def run(mode="incremental", days=None, force=False):
    if xlrd is None and openpyxl is None:
        print("Thieu thu vien doc Excel. Cai: python -m pip install xlrd openpyxl")
        return None

    session = ttcb.make_session()
    now = datetime.now()
    sd, st = now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")

    print("==> Liet ke cac tin 'Ket qua giao dich TPDN'...")
    articles = list_result_articles(session)
    print(f"    Tim thay {len(articles)} tin.")

    existing, _ = load_existing_dates()

    # chon tin can xu ly
    todo = articles
    if days:
        todo = articles[:days]
    if not force:
        todo = [a for a in todo if a["trading_date"] not in existing]
    if mode == "incremental" and not days and not force:
        # chi ngay moi (da loc o tren)
        pass

    print(f"    Se xu ly {len(todo)} tin (bo qua {len(articles)-len(todo)} da co / ngoai pham vi).")
    if not todo:
        print("    Khong co ngay moi. (Dung --all de backfill, --force de tai lai.)")
        return DATA_DIR / MASTER_NAME

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    master = DATA_DIR / MASTER_NAME
    FLUSH_EVERY = 50  # ghi vao file tong sau moi 50 ngay (an toan neu bi gian doan)

    buf = []
    ok = fail = total_written = 0

    def flush():
        nonlocal buf, total_written
        if not buf:
            return
        res = pd.DataFrame(buf, columns=COLUMNS)
        try:
            if master.exists():
                old = pd.read_csv(master, dtype=str)
                combined = pd.concat([old, res.astype(str)], ignore_index=True)
            else:
                combined = res.astype(str)
            combined = dedup(combined)
            combined.to_csv(master, index=False, encoding="utf-8-sig")
            total_written = len(combined)
        except PermissionError:
            fb = DATA_DIR / f"TPDN_trading _CANMERGE_{now:%Y%m%d_%H%M%S}.csv"
            res.to_csv(fb, index=False, encoding="utf-8-sig")
            print(f"  [!] File tong dang mo -> luu tam: {fb.name}")
        buf = []

    for k, a in enumerate(todo, 1):
        try:
            url = get_attachment_url(session, a["article_id"])
            if not url:
                fail += 1; print(f"  [!] {a['trading_date']}: khong co file dinh kem"); continue
            data = download(session, url)
            recs = parse_tpdn(data)
            if not recs:
                fail += 1; print(f"  [!] {a['trading_date']}: khong boc duoc bang"); continue
            for rec in recs:
                rec.update({"trading_date": a["trading_date"], "unit": "VND",
                            "article_id": a["article_id"], "post_date": a["post_date"],
                            "scrape_date": sd, "scrape_time": st})
            buf += recs
            ok += 1
            if k % FLUSH_EVERY == 0:
                flush()
                print(f"    ...{k}/{len(todo)} ngay | da luu file tong ({total_written} dong)")
            time.sleep(0.3)
        except Exception as e:
            fail += 1
            print(f"  [!] {a['trading_date']} (id {a['article_id']}): LOI - {e}")
    flush()

    print(f"\n==> Xong. Tai: {ok} ngay OK, {fail} loi.")
    print(f"    File tong: {master.name} ({total_written} dong)")
    print(f"    Thu muc  : {DATA_DIR.resolve()}")
    return master


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Backfill tat ca cac ngay")
    ap.add_argument("--days", type=int, help="Chi N ngay giao dich gan nhat")
    ap.add_argument("--force", action="store_true", help="Tai lai ca ngay da co")
    a = ap.parse_args()
    run(mode="all" if a.all else "incremental", days=a.days, force=a.force)

# -*- coding: utf-8 -*-
"""
Scrape ket qua xep hang tin nhiem (XHTN) tu TMR - Xep Hang Tin Nhiem Thien Minh.

Nguon: https://www.tmr.vn/xep-hang-tin-nhiem/
(trang cu /ket-qua-xep-hang-1/ da bi go - tu 10/2026 TMR doi web: bang TablePress
cu thay bang DataTables, nhung toan bo dong van nam san trong HTML, khong phan
trang phia server.)

Bang ket qua la <table class="all-reports-table">, moi dong 10 <td>:
  NGAY | TO CHUC PHAT HANH (link) | NGANH NGHE | LOAI XEP HANG | MA TRAI PHIEU |
  HANH DONG | XEP HANG (hang + mo ta) | TRIEN VONG | BAO CAO (link) | REPORT TYPE
Trang con 2 bang khac (tmr-rating-table__table = bang giai thich bac) phai loai tru.
Web moi khong con cot "Loai hinh" (Doanh nghiep / Dinh che Tai chinh).

Chay:
    python scrape_tmr.py
    python scrape_tmr.py --append
"""

import argparse
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://www.tmr.vn"
LIST_URL = f"{BASE}/xep-hang-tin-nhiem/"
TABLE_CLASS = "all-reports-table"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi,en;q=0.9",
}

CRA_NAME = "TMR"

# Dung de nhan dien dung bang neu TablePress doi id
EXPECTED_HEADERS = {"ngày", "tổ chức phát hành", "xếp hạng"}

COLUMNS = [
    "ngay_cong_bo", "to_chuc_phat_hanh", "linh_vuc", "loai_hinh",
    "trang_thai_xep_hang", "loai_y_kien", "ky_han", "ket_qua_xep_hang",
    "trien_vong", "ma_trai_phieu", "bao_cao_vi", "bao_cao_en",
    "link_to_chuc_phat_hanh", "issuer_id", "to_chuc_xhtn", "thoi_gian_scrape",
]

DASHES = {"—", "–", "-", "---", "--", ""}


def _txt(node):
    if node is None:
        return None
    s = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return None if s in DASHES else s


def norm_date(raw):
    """Chuan hoa ve DD/MM/YYYY, chap nhan ca '-' va '/'."""
    if not raw:
        return None
    m = re.search(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})", raw)
    if not m:
        return raw or None
    d, mth, y = m.groups()
    return f"{int(d):02d}/{int(mth):02d}/{y}"


def pick_table(soup):
    t = soup.find("table", class_=TABLE_CLASS)
    if t is not None:
        return t
    # Du phong: chon bang co header khop, khong lay bang giai thich bac
    for cand in soup.find_all("table"):
        heads = {th.get_text(" ", strip=True).lower()
                 for th in cand.find_all("th")}
        if EXPECTED_HEADERS <= heads:
            print(f"  ! khong thay table.{TABLE_CLASS}, dung bang id={cand.get('id')}",
                  file=sys.stderr)
            return cand
    raise RuntimeError("khong tim thay bang ket qua - website da doi cau truc")


def parse_rows(soup, scraped_at):
    table = pick_table(soup)
    body = table.find("tbody") or table
    rows = [x for x in body.find_all("tr") if x.find_all("td")]

    records, skipped = [], 0
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 9:
            skipped += 1
            continue

        ngay = norm_date(tds[0].get_text(" ", strip=True))
        ten = _txt(tds[1])
        linh_vuc = _txt(tds[2])
        ma_tp = _txt(tds[4])
        hanh_dong = _txt(tds[5])
        # 'Lan dau' / 'Cap nhat' -> gia tri cu ('Xep hang lan dau' / 'Cap nhat ket qua XHTN') cho khop cach viet cu / cac nguon khac
        hd = (hanh_dong or "").lower()
        trang_thai = ("Xếp hạng lần đầu" if hd == "lần đầu"
                      else "Cập nhật kết quả XHTN" if hd == "cập nhật"
                      else hanh_dong)
        # Cot xep hang = hang + doan mo ta dai ("A- Chat luong tin dung cao ...")
        # -> chi lay hang (AAA..D kem +/-)
        xh = _txt(tds[6])
        m = re.match(r"([A-D]{1,3}[+-]?)(?=\s|$)", xh or "")
        ket_qua = m.group(1) if m else xh
        trien_vong = _txt(tds[7])

        a = tds[8].find("a")
        href = (a.get("href") or "").strip() if a else ""
        if href.startswith("/"):
            href = BASE + href

        # 'Report type' (cot an): 'To chuc phat hanh' / 'Cong cu no'. Neu thieu thi
        # suy tu 'Loai xep hang' ('To chuc phat hanh dai han' ...).
        loai_y_kien = _txt(tds[9]) if len(tds) > 9 else None
        if not loai_y_kien:
            lx = _txt(tds[3]) or ""
            loai_y_kien = ("Tổ chức phát hành" if lx.lower().startswith("tổ chức")
                           else "Công cụ nợ" if lx.lower().startswith("công cụ") else lx or None)

        records.append({
            "ngay_cong_bo": ngay,
            "to_chuc_phat_hanh": ten,
            "linh_vuc": linh_vuc,
            "loai_hinh": None,       # web moi khong con cot nay
            "trang_thai_xep_hang": trang_thai,
            "loai_y_kien": loai_y_kien,
            "ky_han": None,
            "ket_qua_xep_hang": ket_qua,
            "trien_vong": trien_vong,
            "ma_trai_phieu": ma_tp,
            "bao_cao_vi": href or None,
            "bao_cao_en": None,
            "link_to_chuc_phat_hanh": None,
            "issuer_id": None,
            "to_chuc_xhtn": CRA_NAME,
            "thoi_gian_scrape": scraped_at,
        })

    if skipped:
        print(f"  ! bo qua {skipped} dong khong du 9 cot", file=sys.stderr)
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "output"))
    ap.add_argument("--append", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("- Tai danh sach XHTN TMR ...")
    with requests.Session() as s:
        r = s.get(LIST_URL, headers=HEADERS, timeout=60)
        r.raise_for_status()
        r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    rows = parse_rows(soup, scraped_at)
    print(f"  parse duoc {len(rows)} ban ghi")
    print("  i nguon khong cong bo tong so de doi chieu")

    if not rows:
        print("Khong lay duoc ban ghi nao.", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["ngay_cong_bo_dt"] = pd.to_datetime(
        df["ngay_cong_bo"], format="%d/%m/%Y", errors="coerce")
    n_bad = int(df["ngay_cong_bo_dt"].isna().sum())
    if n_bad:
        print(f"  ! {n_bad} dong khong parse duoc ngay", file=sys.stderr)

    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(args.out, f"tmr_xhtn_{stamp}.csv")
    xlsx_path = os.path.join(args.out, f"tmr_xhtn_{stamp}.xlsx")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False, sheet_name="XHTN")
    print(f"- Da ghi {csv_path}")
    print(f"- Da ghi {xlsx_path}")

    if args.append:
        hist = os.path.join(args.out, "tmr_xhtn_history.csv")
        df.to_csv(hist, mode="a", index=False,
                  header=not os.path.exists(hist), encoding="utf-8-sig")
        print(f"- Da noi them vao {hist}")

    print("\nToan bo ban ghi:")
    with pd.option_context("display.width", 220, "display.max_colwidth", 30,
                           "display.max_rows", 100):
        print(df[["ngay_cong_bo", "to_chuc_phat_hanh", "loai_hinh",
                  "loai_y_kien", "ket_qua_xep_hang", "trien_vong",
                  "ma_trai_phieu"]].to_string())


if __name__ == "__main__":
    main()

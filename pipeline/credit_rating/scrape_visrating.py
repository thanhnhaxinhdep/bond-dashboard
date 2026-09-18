# -*- coding: utf-8 -*-
"""
Scrape ket qua xep hang tin nhiem (XHTN) tu VIS Rating.

Nguon: https://visrating.com/ket-qua-xep-hang-tin-nhiem?page_size=50#merged

Trang render server-side (khong co AJAX rieng), chi can GET voi page_size lon
la lay het. Bang ket qua nam trong <div id="rr-table-scroll-merged">, moi dong
la <div class="rr-table-row"> gom dung 9 <span> cell.

Cot tren web: NGAY | DOANH NGHIEP | LOAI HINH | TRANG THAI | LOAI XH |
              XEP HANG | TRIEN VONG | TRAI PHIEU | BAO CAO

Chay:
    python scrape_visrating.py
    python scrape_visrating.py --append
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

BASE = "https://visrating.com"
LIST_URL = f"{BASE}/ket-qua-xep-hang-tin-nhiem"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi,en;q=0.9",
}

CRA_NAME = "VIS Rating"

# Website tu gioi han: "Bang hien thi toi da 1.000 ket qua"
SITE_CAP = 1000

COLUMNS = [
    "ngay_cong_bo", "to_chuc_phat_hanh", "linh_vuc", "loai_hinh",
    "trang_thai_xep_hang", "loai_y_kien", "ky_han", "ket_qua_xep_hang",
    "trien_vong", "ma_trai_phieu", "bao_cao_vi", "bao_cao_en",
    "link_to_chuc_phat_hanh", "issuer_id", "to_chuc_xhtn", "thoi_gian_scrape",
]

DASHES = {"—", "–", "-", "---", "--", ""}


def _clean(node):
    if node is None:
        return None
    txt = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return None if txt in DASHES else txt


def fetch_html(session, page_size=1000):
    r = session.get(LIST_URL, params={"page_size": page_size},
                    headers=HEADERS, timeout=120)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def parse_total(soup):
    """Doc 'Hien thi 1 - 50 trong 106 ket qua'."""
    txt = soup.get_text(" ", strip=True)
    m = re.search(r"Hi[eể]n th[iị]\s*[\d.,]+\s*-\s*[\d.,]+\s*trong\s*([\d.,]+)",
                  txt)
    return int(re.sub(r"[.,]", "", m.group(1))) if m else None


def split_loai_xh(raw):
    """'Cong cu no - Dai han' -> ('Cong cu no', 'Dai han')."""
    if not raw:
        return None, None
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", raw, maxsplit=1)]
    return (parts[0], parts[1]) if len(parts) == 2 else (raw, None)


def parse_rows(soup, scraped_at):
    cont = soup.find(id="rr-table-scroll-merged")
    if cont is None:
        raise RuntimeError(
            "khong tim thay #rr-table-scroll-merged - website da doi cau truc")

    records, skipped = [], 0
    for row in cont.find_all("div", class_="rr-table-row"):
        cells = [c for c in row.find_all("span", recursive=False)
                 if c.has_attr("class")
                 and ("rr-table-cell" in c["class"] or "rr-cell" in c["class"])]
        if len(cells) < 9:
            skipped += 1
            continue

        ngay = _clean(cells[0])

        a = cells[1].find("a")
        ten = _clean(a) if a else _clean(cells[1])
        link = a.get("href", "") if a else ""
        if link.startswith("/"):
            link = BASE + link
        m = re.search(r"\.(\d+)\.html", link or "")
        issuer_id = m.group(1) if m else None

        loai_hinh = _clean(cells[2])
        trang_thai = _clean(cells[3])
        doi_tuong, ky_han = split_loai_xh(_clean(cells[4]))
        ket_qua = _clean(cells[5])
        trien_vong = _clean(cells[6])
        ma_tp = _clean(cells[7])

        # Cot bao cao: cac <a class="rr-lang-link"> ghi VN / EN
        vi_url = en_url = None
        for a_ in cells[8].find_all("a"):
            label = a_.get_text(strip=True).upper()
            href = a_.get("href", "")
            if href.startswith("/"):
                href = BASE + href
            if label.startswith("VN"):
                vi_url = href
            elif label.startswith("EN"):
                en_url = href

        records.append({
            "ngay_cong_bo": ngay,
            "to_chuc_phat_hanh": ten,
            "linh_vuc": None,          # VIS Rating khong cong bo nganh o bang nay
            "loai_hinh": loai_hinh,
            "trang_thai_xep_hang": trang_thai,
            "loai_y_kien": doi_tuong,
            "ky_han": ky_han,
            "ket_qua_xep_hang": ket_qua,
            "trien_vong": trien_vong,
            "ma_trai_phieu": ma_tp,
            "bao_cao_vi": vi_url,
            "bao_cao_en": en_url,
            "link_to_chuc_phat_hanh": link or None,
            "issuer_id": issuer_id,
            "to_chuc_xhtn": CRA_NAME,
            "thoi_gian_scrape": scraped_at,
        })

    if skipped:
        print(f"  ! bo qua {skipped} dong khong du 9 cell", file=sys.stderr)
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "output"), help="thu muc xuat file")
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--append", action="store_true",
                    help="noi them vao visrating_xhtn_history.csv")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with requests.Session() as s:
        print("- Tai danh sach XHTN VIS Rating ...")
        html = fetch_html(s, args.page_size)
        soup = BeautifulSoup(html, "html.parser")

    rows = parse_rows(soup, scraped_at)
    print(f"  parse duoc {len(rows)} ban ghi")

    total = parse_total(soup)
    if total is None:
        print("  ! khong doc duoc tong so ket qua de doi chieu", file=sys.stderr)
    elif total == len(rows):
        print(f"  website bao co {total} ket qua -> KHOP")
    else:
        print(f"  website bao co {total} ket qua -> LECH "
              f"({total - len(rows):+d})", file=sys.stderr)

    if total and total >= SITE_CAP:
        print(f"  ! canh bao: cham tran {SITE_CAP} ket qua cua website, "
              f"co the thieu du lieu cu", file=sys.stderr)

    if not rows:
        print("Khong lay duoc ban ghi nao.", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["ngay_cong_bo_dt"] = pd.to_datetime(
        df["ngay_cong_bo"], format="%d/%m/%Y", errors="coerce")

    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(args.out, f"visrating_xhtn_{stamp}.csv")
    xlsx_path = os.path.join(args.out, f"visrating_xhtn_{stamp}.xlsx")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False, sheet_name="XHTN")
    print(f"- Da ghi {csv_path}")
    print(f"- Da ghi {xlsx_path}")

    if args.append:
        hist = os.path.join(args.out, "visrating_xhtn_history.csv")
        header = not os.path.exists(hist)
        df.to_csv(hist, mode="a", index=False, header=header,
                  encoding="utf-8-sig")
        print(f"- Da noi them vao {hist}")

    print("\nMau 5 dong dau:")
    with pd.option_context("display.width", 200, "display.max_colwidth", 26):
        print(df[["ngay_cong_bo", "to_chuc_phat_hanh", "loai_hinh",
                  "ket_qua_xep_hang", "trien_vong", "ma_trai_phieu",
                  "to_chuc_xhtn", "thoi_gian_scrape"]].head())


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Scrape ket qua xep hang tin nhiem (XHTN) tu S&I Ratings (SnI Ratings).

Nguon: https://sniratings.com.vn/ket-qua-xep-hang/

Trang la WordPress, bang ket qua duoc frontend nap qua REST API:
    /wp-json/wp/v2/rating?per_page=100&lang=vi
(khoi phan trang tren trang co thuoc tinh action="/wp/v2/rating?per_page=9")

LUU Y: site da ngu (WPML). Khong truyen lang thi API tra CA tieng Viet lan
tieng Anh (24 ban ghi = 12 vi + 12 en). Phai luon truyen lang=vi.

Truong dung tu API:
    publish_date            -> ngay cong bo (DD/MM/YYYY)
    title.rendered          -> ten to chuc
    taxonomies.corporate_model -> loai hinh (Doanh nghiep / To chuc tai chinh)
    taxonomies.industry     -> nganh nghe
    taxonomies.rating_type  -> 'Cong cu no - Lan dau' / 'Giam sat Xep hang' ...
    taxonomies.prospects    -> trien vong
    taxonomies.rating_tags  -> HTML boc bac xep hang, phai strip the
    report / report_details -> URL PDF, '#' nghia la khong co

Chay:
    python scrape_sniratings.py
    python scrape_sniratings.py --append
"""

import argparse
import html as html_mod
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

BASE = "https://sniratings.com.vn"
API_URL = f"{BASE}/wp-json/wp/v2/rating"
LIST_URL = f"{BASE}/ket-qua-xep-hang/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi,en;q=0.9",
}

CRA_NAME = "S&I Ratings"

# Gia tri API dung khi khong co file bao cao
NO_LINK = {"#", "", None}

COLUMNS = [
    "ngay_cong_bo", "to_chuc_phat_hanh", "linh_vuc", "loai_hinh",
    "trang_thai_xep_hang", "loai_y_kien", "ky_han", "ket_qua_xep_hang",
    "trien_vong", "ma_trai_phieu", "bao_cao_vi", "bao_cao_en",
    "bao_cao_chi_tiet", "link_to_chuc_phat_hanh", "issuer_id",
    "to_chuc_xhtn", "thoi_gian_scrape",
]


def _clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", html_mod.unescape(str(s))).strip()
    return s or None


def strip_html(s):
    """rating_tags la HTML boc bac xep hang -> lay text ben trong."""
    if not s:
        return None
    return _clean(BeautifulSoup(str(s), "html.parser").get_text(" ", strip=True))


def split_rating_type(raw):
    """
    'Cong cu no - Lan dau'      -> ('Cong cu no', 'Lan dau')
    'To chuc phat hanh - Lan dau' -> ('To chuc phat hanh', 'Lan dau')
    'Giam sat Xep hang'         -> (None, 'Giam sat Xep hang')
    """
    if not raw:
        return None, None
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", raw, maxsplit=1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, parts[0]


def fetch_items(session, lang="vi", per_page=100, max_pages=20):
    """Lay tat ca ban ghi qua REST API, tu dong lat trang theo X-WP-TotalPages."""
    items, page = [], 1
    while page <= max_pages:
        r = session.get(API_URL, headers=HEADERS, timeout=60, params={
            "per_page": per_page, "page": page, "lang": lang,
        })
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        total_pages = int(r.headers.get("X-WP-TotalPages") or 1)
        if page >= total_pages:
            break
        page += 1
    else:
        print(f"  ! da cham tran {max_pages} trang API", file=sys.stderr)
    return items


def cross_check(session):
    """
    Doi chieu voi trang HTML: khoi phan trang mang thuoc tinh
    total="<so trang>" va action="/wp/v2/rating?per_page=<n>".
    Tra ve (so_trang, per_page) hoac (None, None).
    """
    try:
        r = session.get(LIST_URL, headers={**HEADERS, "Accept": "text/html"},
                        timeout=60)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        pag = soup.find(class_="pagination")
        if pag is None:
            return None, None
        pages = int(pag.get("total")) if pag.get("total") else None
        m = re.search(r"per_page=(\d+)", pag.get("action") or "")
        return pages, (int(m.group(1)) if m else None)
    except Exception as e:
        print(f"  ! khong doi chieu duoc voi trang HTML ({e})", file=sys.stderr)
        return None, None


def parse_items(items, scraped_at):
    records = []
    for it in items:
        tax = it.get("taxonomies") or {}
        loai_y_kien, trang_thai = split_rating_type(_clean(tax.get("rating_type")))

        report = it.get("report")
        report = None if report in NO_LINK else _clean(report)
        details = it.get("report_details")
        details = None if details in NO_LINK else _clean(details)

        records.append({
            "ngay_cong_bo": _clean(it.get("publish_date")),
            "to_chuc_phat_hanh": _clean((it.get("title") or {}).get("rendered")),
            "linh_vuc": _clean(tax.get("industry")),
            "loai_hinh": _clean(tax.get("corporate_model")),
            "trang_thai_xep_hang": trang_thai,
            "loai_y_kien": loai_y_kien,
            "ky_han": None,
            "ket_qua_xep_hang": strip_html(tax.get("rating_tags")),
            "trien_vong": _clean(tax.get("prospects")),
            "ma_trai_phieu": None,   # nguon nay khong cong bo ma trai phieu
            "bao_cao_vi": report,
            "bao_cao_en": None,
            "bao_cao_chi_tiet": details,
            "link_to_chuc_phat_hanh": _clean(it.get("link")),
            "issuer_id": it.get("id"),
            "to_chuc_xhtn": CRA_NAME,
            "thoi_gian_scrape": scraped_at,
        })
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "output"))
    ap.add_argument("--lang", default="vi")
    ap.add_argument("--append", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with requests.Session() as s:
        print(f"- Goi REST API S&I Ratings (lang={args.lang}) ...")
        items = fetch_items(s, lang=args.lang)
        print(f"  API tra ve {len(items)} ban ghi")

        pages, per_page = cross_check(s)

    rows = parse_items(items, scraped_at)
    print(f"  parse duoc {len(rows)} ban ghi")

    # Trang HTML chi cho biet SO TRANG, nen chi kiem tra duoc khoang hop le
    if pages and per_page:
        lo, hi = (pages - 1) * per_page + 1, pages * per_page
        if lo <= len(rows) <= hi:
            print(f"  trang web co {pages} trang x {per_page}/trang "
                  f"({lo}-{hi}) -> KHOP")
        else:
            print(f"  ! trang web co {pages} trang x {per_page}/trang "
                  f"(du kien {lo}-{hi}) nhung API tra {len(rows)} -> LECH",
                  file=sys.stderr)
    else:
        print("  ! khong doc duoc khoi phan trang de doi chieu", file=sys.stderr)

    if not rows:
        print("Khong lay duoc ban ghi nao.", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["ngay_cong_bo_dt"] = pd.to_datetime(
        df["ngay_cong_bo"], format="%d/%m/%Y", errors="coerce")
    df = df.sort_values("ngay_cong_bo_dt", ascending=False,
                        na_position="last").reset_index(drop=True)

    n_bad = int(df["ngay_cong_bo_dt"].isna().sum())
    if n_bad:
        print(f"  ! {n_bad} dong khong parse duoc ngay", file=sys.stderr)

    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(args.out, f"sniratings_xhtn_{stamp}.csv")
    xlsx_path = os.path.join(args.out, f"sniratings_xhtn_{stamp}.xlsx")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False, sheet_name="XHTN")
    print(f"- Da ghi {csv_path}")
    print(f"- Da ghi {xlsx_path}")

    if args.append:
        hist = os.path.join(args.out, "sniratings_xhtn_history.csv")
        df.to_csv(hist, mode="a", index=False,
                  header=not os.path.exists(hist), encoding="utf-8-sig")
        print(f"- Da noi them vao {hist}")

    print("\nToan bo ban ghi:")
    with pd.option_context("display.width", 230, "display.max_colwidth", 32,
                           "display.max_rows", 100):
        print(df[["ngay_cong_bo", "to_chuc_phat_hanh", "loai_hinh", "linh_vuc",
                  "loai_y_kien", "trang_thai_xep_hang", "ket_qua_xep_hang",
                  "trien_vong"]].to_string())


if __name__ == "__main__":
    main()

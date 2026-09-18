# -*- coding: utf-8 -*-
"""
Scrape ket qua xep hang tin nhiem (XHTN) tu Saigon Ratings.

Nguon: https://saigonratings.com/ket-qua-xep-hang/

Trang dung TablePress + DataTables phan trang phia client, nghia la TOAN BO
cac dong da nam san trong HTML - chi can 1 request, khong phai lat trang.
Bang la <table id="tablepress-70">, moi dong 8 <td>.

Cot tren web: NGAY CONG BO | TO CHUC PHAT HANH | NGANH | LOAI HINH |
              TINH TRANG | BAC XEP HANG | TRIEN VONG | CHI TIET

Cac quirk cua nguon nay (deu da xu ly, xem ghi chu trong code):
  - Ngay co the kem hau to "(*)" (chu thich phi dich vu > 5% doanh thu)
  - Ngay chu yeu la DD-MM-YYYY nhung co dong dung DD/MM/YYYY
  - Cot LOAI HINH gop ma trai phieu vao qua the <br>
  - Bac xep hang / trien vong co the la "Bao mat", hoac rong

Chay:
    python scrape_saigonratings.py
    python scrape_saigonratings.py --append
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

BASE = "https://saigonratings.com"
LIST_URL = f"{BASE}/ket-qua-xep-hang/"
TABLE_ID = "tablepress-70"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi,en;q=0.9",
}

CRA_NAME = "Saigon Ratings"

COLUMNS = [
    "ngay_cong_bo", "to_chuc_phat_hanh", "linh_vuc", "loai_hinh",
    "trang_thai_xep_hang", "loai_y_kien", "ky_han", "ket_qua_xep_hang",
    "trien_vong", "ma_trai_phieu", "bao_cao_vi", "bao_cao_en",
    "link_to_chuc_phat_hanh", "issuer_id", "bao_mat", "phi_tren_5pc",
    "to_chuc_xhtn", "thoi_gian_scrape",
]

DASHES = {"—", "–", "-", "---", "--", ""}
CONFIDENTIAL = "Bảo mật"


def _txt(node):
    if node is None:
        return None
    s = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return None if s in DASHES else s


def _lines(td):
    """Tach noi dung 1 <td> thanh cac dong theo the <br>."""
    for br in td.find_all("br"):
        br.replace_with("\n")
    return [re.sub(r"\s+", " ", x).strip()
            for x in td.get_text().split("\n") if x.strip()]


def parse_date(raw):
    """'31-12-2024 (*)' -> ('31/12/2024', True). Chap nhan ca '-' va '/'."""
    if not raw:
        return None, False
    starred = "(*)" in raw or "（*）" in raw
    s = re.sub(r"\(\s*\*\s*\)", "", raw).strip()
    m = re.search(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})", s)
    if not m:
        return (s or None), starred
    d, mth, y = m.groups()
    return f"{int(d):02d}/{int(mth):02d}/{y}", starred


# Ma trai phieu: 1-6 chu HOA + 5-8 chu so
# (CII12504, LGPL12601, AQ112501, P5332601 - Parkland 53 chi co 1 chu cai)
MA_TP_RE = re.compile(r"\b([A-Z]{1,6}\d{5,8})\b")


def _co_ve_la_ma(s):
    """Du phong khi regex khong khop: 1 tu, khong khoang trang, co chu so."""
    return bool(s) and " " not in s and any(c.isdigit() for c in s) and len(s) <= 20


def parse_loai_hinh(td):
    """
    Cot LOAI HINH. Nguon dung LAN LON nhieu dang cho cung mot noi dung:
        'Xep hang<br>Cong cu no<br>CII12504'   (3 dong - dang goc)
        'Cong cu no <br>LGPL12601'             (2 dong, thieu 'Xep hang')
        'Xep hang Cong cu no VHM12615'         (1 dong, dinh lien)
        'Xep hang To chuc phat hanh'           (khong co ma)
    Nen KHONG dua vao so dong nua: bat ma trai phieu bang mau, phan con lai
    la loai y kien. Cach nay chiu duoc ca 3 dang tren lan dang moi ve sau.
    Tra ve (loai_y_kien, ma_trai_phieu).
    """
    ls = _lines(td)
    if not ls:
        return None, None

    text = re.sub(r"\s+", " ", " ".join(ls)).strip()
    ma = None
    m = MA_TP_RE.search(text)
    if m:
        ma = m.group(1)
        text = text.replace(ma, " ")
    elif len(ls) >= 3 and _co_ve_la_ma(ls[-1]):
        # dang 3 dong nhung ma khong khop mau -> lay dong cuoi (cach cu)
        ma = ls[-1]
        text = " ".join(ls[:-1])

    loai = re.sub(r"\s+", " ", text).strip()
    loai = re.sub(r"^Xếp hạng\s*", "", loai).strip() or None
    return loai, ma


def fetch_soup(session):
    r = session.get(LIST_URL, headers=HEADERS, timeout=90)
    r.raise_for_status()
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def parse_declared_total(soup):
    """Doc 'TONG SO LUOT XEP HANG: 304' o dau trang."""
    txt = soup.get_text(" ", strip=True)
    m = re.search(r"T[ỔO]NG S[ỐO] L[ƯU][ỢO]T X[ẾE]P H[ẠA]NG\s*:?\s*([\d.,]+)",
                  txt)
    return int(re.sub(r"[.,]", "", m.group(1))) if m else None


def parse_rows(soup, scraped_at):
    table = soup.find("table", id=TABLE_ID)
    if table is None:
        # TablePress co the doi id -> lay bang nhieu dong nhat lam du phong
        tables = soup.find_all("table")
        if not tables:
            raise RuntimeError("khong tim thay bang nao - website da doi cau truc")
        table = max(tables, key=lambda t: len(t.find_all("tr")))
        print(f"  ! khong thay #{TABLE_ID}, dung bang id={table.get('id')}",
              file=sys.stderr)

    body = table.find("tbody")
    rows = body.find_all("tr") if body else []

    records, skipped = [], 0
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 8:
            skipped += 1
            continue

        ngay, starred = parse_date(tds[0].get_text(" ", strip=True))
        ten = _txt(tds[1])
        nganh = _txt(tds[2])
        loai_y_kien, ma_tp = parse_loai_hinh(tds[3])
        trang_thai = _txt(tds[4])
        ket_qua = _txt(tds[5])
        trien_vong = _txt(tds[6])

        a = tds[7].find("a")
        href = (a.get("href") or "").strip() if a else ""
        if href.startswith("/"):
            href = BASE + href
        link_bao_cao = href or None

        records.append({
            "ngay_cong_bo": ngay,
            "to_chuc_phat_hanh": ten,
            "linh_vuc": nganh,
            "loai_hinh": None,       # Saigon Ratings khong phan Doanh nghiep/DCTC
            "trang_thai_xep_hang": trang_thai,
            "loai_y_kien": loai_y_kien,
            "ky_han": None,          # nguon nay khong tach ky han
            "ket_qua_xep_hang": ket_qua,
            "trien_vong": trien_vong,
            "ma_trai_phieu": ma_tp,
            "bao_cao_vi": link_bao_cao,
            "bao_cao_en": None,      # chi co ban tieng Viet
            "link_to_chuc_phat_hanh": None,
            "issuer_id": None,
            "bao_mat": (ket_qua == CONFIDENTIAL) or (trien_vong == CONFIDENTIAL),
            "phi_tren_5pc": starred,
            "to_chuc_xhtn": CRA_NAME,
            "thoi_gian_scrape": scraped_at,
        })

    if skipped:
        print(f"  ! bo qua {skipped} dong khong du 8 cot", file=sys.stderr)
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "output"), help="thu muc xuat file")
    ap.add_argument("--append", action="store_true",
                    help="noi them vao saigonratings_xhtn_history.csv")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with requests.Session() as s:
        print("- Tai danh sach XHTN Saigon Ratings ...")
        soup = fetch_soup(s)

    rows = parse_rows(soup, scraped_at)
    print(f"  parse duoc {len(rows)} ban ghi")

    declared = parse_declared_total(soup)
    if declared is None:
        print("  ! khong doc duoc 'TONG SO LUOT XEP HANG' de doi chieu",
              file=sys.stderr)
    elif declared == len(rows):
        print(f"  website bao co {declared} luot xep hang -> KHOP")
    else:
        # Hien tai website ghi 304 nhung bang co 305 dong -> lech san co cua nguon
        print(f"  ! website ghi {declared} luot nhung bang co {len(rows)} dong "
              f"({len(rows) - declared:+d}) - lech tu chinh nguon",
              file=sys.stderr)

    if not rows:
        print("Khong lay duoc ban ghi nao.", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["ngay_cong_bo_dt"] = pd.to_datetime(
        df["ngay_cong_bo"], format="%d/%m/%Y", errors="coerce")

    n_nodate = int(df["ngay_cong_bo_dt"].isna().sum())
    if n_nodate:
        print(f"  i {n_nodate} dong khong co ngay cong bo (nguon de trong)")

    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(args.out, f"saigonratings_xhtn_{stamp}.csv")
    xlsx_path = os.path.join(args.out, f"saigonratings_xhtn_{stamp}.xlsx")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False, sheet_name="XHTN")
    print(f"- Da ghi {csv_path}")
    print(f"- Da ghi {xlsx_path}")

    if args.append:
        hist = os.path.join(args.out, "saigonratings_xhtn_history.csv")
        header = not os.path.exists(hist)
        df.to_csv(hist, mode="a", index=False, header=header,
                  encoding="utf-8-sig")
        print(f"- Da noi them vao {hist}")

    print("\nMau 5 dong dau:")
    with pd.option_context("display.width", 200, "display.max_colwidth", 24):
        print(df[["ngay_cong_bo", "to_chuc_phat_hanh", "linh_vuc",
                  "loai_y_kien", "ket_qua_xep_hang", "trien_vong",
                  "ma_trai_phieu", "to_chuc_xhtn"]].head())


if __name__ == "__main__":
    main()

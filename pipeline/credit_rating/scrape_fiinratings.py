# -*- coding: utf-8 -*-
"""
Scrape ket qua xep hang tin nhiem (XHTN) tu FiinRatings.

Nguon: https://fiinratings.vn/vi/ket-qua-xep-hang/ket-qua-xep-hang-tin-nhiem.html
Endpoint AJAX: /vi/ratings/indexajax  -> tra ve cac <tr> HTML.

Chay:
    python scrape_fiinratings.py                  # xuat CSV + XLSX vao ./output
    python scrape_fiinratings.py --out D:\\data   # doi thu muc xuat
    python scrape_fiinratings.py --append         # noi them vao file lich su
"""

import argparse
import json
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

BASE = "https://fiinratings.vn"
LIST_URL = f"{BASE}/vi/ket-qua-xep-hang/ket-qua-xep-hang-tin-nhiem.html"
AJAX_URL = f"{BASE}/vi/ratings/indexajax"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Referer": LIST_URL,
    "Accept": "text/html, */*; q=0.01",
}

# Ten to chuc xep hang tin nhiem - co dinh cho nguon nay
CRA_NAME = "FiinRatings"

# Fallback neu khong doc duoc dropdown nganh tu trang chu
INDUSTRY_FALLBACK = {
    25: "Điện", 27: "Bất động sản", 28: "Xây dựng", 29: "Vật liệu xây dựng",
    30: "Xi măng", 31: "Thép", 32: "Khai khoáng", 33: "Nhựa", 34: "Cao su",
    35: "Sợi", 36: "Hóa chất chuyên dụng", 37: "Giấy và Lâm sản",
    38: "Công nghiệp và Máy móc", 39: "Ô tô và phụ tùng", 40: "Dầu khí",
    41: "Nước và tiện ích", 42: "Môi trường", 43: "Thực phẩm và đồ uống",
    44: "Hàng tiêu dùng", 45: "May mặc", 46: "Chăn nuôi", 47: "Thủy sản",
    48: "Nông nghiệp", 49: "Dược phẩm và Y tế", 50: "Bán lẻ",
    51: "Thương mại và Phân phối", 52: "Vận tải", 53: "Hạ tầng",
    54: "Du lịch và Giải trí", 55: "Truyền thông và Giải trí",
    56: "Công nghệ thông tin", 57: "Viễn thông",
    58: "Dịch vụ hỗ trợ kinh doanh", 59: "Dịch vụ Giáo dục", 60: "Ngân hàng",
    61: "Dịch vụ tài chính", 62: "Bảo hiểm", 63: "Quỹ và Công ty Đầu tư",
    64: "Đa ngành",
}

COLUMNS = [
    "ngay_cong_bo", "to_chuc_phat_hanh", "linh_vuc", "trang_thai_xep_hang",
    "loai_y_kien", "ket_qua_xep_hang", "trien_vong", "ma_trai_phieu",
    "bao_cao_vi", "bao_cao_en", "link_to_chuc_phat_hanh", "issuer_id",
    "to_chuc_xhtn", "thoi_gian_scrape",
]


def get_industry_map(session):
    """Doc bang anh xa industryId -> ten linh vuc tu dropdown loc tren trang."""
    try:
        html = session.get(LIST_URL, headers=HEADERS, timeout=30).text
        soup = BeautifulSoup(html, "html.parser")
        select = soup.find("select", id=re.compile("industry", re.I))
        if select is None:
            for s in soup.find_all("select"):
                opts = [o.get_text(strip=True) for o in s.find_all("option")]
                if "Bất động sản" in opts and "Ngân hàng" in opts:
                    select = s
                    break
        if select is None:
            raise ValueError("khong tim thay dropdown linh vuc")
        mapping = {}
        for o in select.find_all("option"):
            val, txt = o.get("value", "").strip(), o.get_text(strip=True)
            if val.isdigit() and txt:
                mapping[int(val)] = txt
        if len(mapping) < 10:
            raise ValueError("dropdown linh vuc qua ngan")
        return mapping
    except Exception as e:
        print(f"  ! khong doc duoc dropdown linh vuc ({e}), dung ban fallback",
              file=sys.stderr)
        return dict(INDUSTRY_FALLBACK)


def fetch_page(session, page, page_size):
    params = {
        "companyName": "", "industryId": "", "scoreId": "", "prospectsId": "",
        "opinionTypesId": "", "issuerTypesId": "",
        "page": page, "pageSize": page_size, "lang": "vi",
    }
    r = session.get(AJAX_URL, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def fetch_all_html(session, page_size=1000, max_pages=50):
    """Lay het cac <tr>, tu dong sang trang neu server gioi han pageSize."""
    parts, page = [], 1
    while page <= max_pages:
        html = fetch_page(session, page, page_size)
        if not re.search(r"<td", html, re.I):
            break
        parts.append(html)
        # Neu trang nay it hon page_size ban ghi -> da het
        if len(re.findall(r"<tr", html, re.I)) <= page_size:
            n_rows = len(re.findall(r"<tr", html, re.I))
            if n_rows < page_size:
                break
        page += 1
    else:
        print(f"  ! da cham tran {max_pages} trang", file=sys.stderr)
    return "".join(parts)


def parse_total(html):
    """Doc tong so ban ghi tu dong 'Hien thi 1 - N co M ban ghi' trong response.

    Response AJAX ma hoa tieng Viet bang HTML entity nen phai decode truoc.
    """
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = re.search(r"c[óo]\s*([\d.,]+)\s*b[ảa]n\s*ghi", text)
    return int(re.sub(r"[.,]", "", m.group(1))) if m else None


def _report_links(td):
    """Lay {vi, en} tu cac div JSON an trong cot Bao cao chi tiet."""
    vi = en = None
    if td is None:
        return vi, en
    for div in td.find_all("div"):
        txt = div.get_text(strip=True)
        if not (txt.startswith("{") and txt.endswith("}")):
            continue
        try:
            d = json.loads(txt)
        except json.JSONDecodeError:
            continue
        vi = vi or d.get("vi")
        en = en or d.get("en")
    return vi, en


def parse_rows(html, industry_map, scraped_at):
    soup = BeautifulSoup(f"<table>{html}</table>", "html.parser")
    records = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue

        ngay = tds[0].get_text(strip=True)

        a = tds[1].find("a")
        ten = (a.get_text(strip=True) if a else tds[1].get_text(strip=True))
        href = a.get("href", "") if a else ""
        link = (BASE + href) if href.startswith("/") else href
        m = re.search(r"-id(\d+)\.html", href or "")
        issuer_id = m.group(1) if m else None

        ind_id = tds[2].get("data-parent-industry-id")
        linh_vuc = tds[2].get_text(strip=True)
        if not linh_vuc and ind_id and str(ind_id).isdigit():
            linh_vuc = industry_map.get(int(ind_id), f"(id {ind_id})")

        trang_thai = tds[3].get_text(strip=True)
        loai_y_kien = tds[4].get_text(strip=True)

        # Cot diem xep hang co kem <span class=icon-info> -> bo di
        rating_td = tds[5]
        for sp in rating_td.find_all("span", class_="icon-info"):
            sp.decompose()
        ket_qua = rating_td.get_text(strip=True)

        trien_vong = tds[6].get_text(strip=True)

        ma_tp = tds[7].get_text(strip=True)
        if ma_tp in ("---", "--", "-", ""):
            ma_tp = None

        vi_url, en_url = _report_links(tds[8] if len(tds) > 8 else None)

        records.append({
            "ngay_cong_bo": ngay,
            "to_chuc_phat_hanh": ten,
            "linh_vuc": linh_vuc or None,
            "trang_thai_xep_hang": trang_thai or None,
            "loai_y_kien": loai_y_kien or None,
            "ket_qua_xep_hang": ket_qua or None,
            "trien_vong": trien_vong or None,
            "ma_trai_phieu": ma_tp,
            "bao_cao_vi": vi_url,
            "bao_cao_en": en_url,
            "link_to_chuc_phat_hanh": link or None,
            "issuer_id": issuer_id,
            "to_chuc_xhtn": CRA_NAME,
            "thoi_gian_scrape": scraped_at,
        })
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "output"), help="thu muc xuat file")
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--append", action="store_true",
                    help="noi them vao fiinratings_xhtn_history.csv")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with requests.Session() as s:
        print("- Doc bang anh xa linh vuc ...")
        industry_map = get_industry_map(s)
        print(f"  {len(industry_map)} linh vuc")

        print("- Tai danh sach XHTN ...")
        html = fetch_all_html(s, args.page_size)
        rows = parse_rows(html, industry_map, scraped_at)
        print(f"  parse duoc {len(rows)} ban ghi")

        total = parse_total(html)
        if total is None:
            print("  ! khong doc duoc tong so ban ghi de doi chieu",
                  file=sys.stderr)
        elif total == len(rows):
            print(f"  website bao co {total} ban ghi -> KHOP")
        else:
            print(f"  website bao co {total} ban ghi -> LECH "
                  f"({total - len(rows):+d})", file=sys.stderr)

    if not rows:
        print("Khong lay duoc ban ghi nao.", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["ngay_cong_bo_dt"] = pd.to_datetime(
        df["ngay_cong_bo"], format="%d/%m/%Y", errors="coerce")

    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(args.out, f"fiinratings_xhtn_{stamp}.csv")
    xlsx_path = os.path.join(args.out, f"fiinratings_xhtn_{stamp}.xlsx")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False, sheet_name="XHTN")
    print(f"- Da ghi {csv_path}")
    print(f"- Da ghi {xlsx_path}")

    if args.append:
        hist = os.path.join(args.out, "fiinratings_xhtn_history.csv")
        header = not os.path.exists(hist)
        df.to_csv(hist, mode="a", index=False, header=header,
                  encoding="utf-8-sig")
        print(f"- Da noi them vao {hist}")

    print("\nMau 5 dong dau:")
    with pd.option_context("display.width", 200, "display.max_colwidth", 28):
        print(df[["ngay_cong_bo", "to_chuc_phat_hanh", "linh_vuc",
                  "ket_qua_xep_hang", "trien_vong", "ma_trai_phieu",
                  "to_chuc_xhtn", "thoi_gian_scrape"]].head())


if __name__ == "__main__":
    main()

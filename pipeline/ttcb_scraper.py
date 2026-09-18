#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ttcb_scraper.py  -  Scrape toan bo "Thong tin cong bo" tu trang
                    https://hnx.vn/tin-tuc-su-kien-ttcbhnx.html

Trang co 3 sub-tab (cung 1 AJAX endpoint, khac tham so pArticlesType):
    ""               -> TTCB cap So HNX          (~57 tin)
    "HNX_PUBLIC"     -> TTCB cong ty dai chung   (~2090 tin, cap nhat hang ngay)
    "CORPORATE_BONDS"-> TTCB trai phieu DN (kho luu tru 2019-2021, ~4059 tin)

Endpoint: POST /ModuleArticles/ArticlesHNX/NextPageThongTinCongBoHNXPS
Moi tin co: STT, Ngay dang tin, Tieu de, Article ID, co/khong file dinh kem.
    - Chi tiet tin : POST /ModuleArticles/ArticlesCPEtfs/PopupTinCongBoDetail {pArticlesID}
    - File dinh kem: POST /ModuleArticles/ArticlesCPEtfs/ArticlesFileAttach   {pArticlesID}

Ket qua (thu muc ttcb_data/):
    TTCB_HNX _ <ngay>_<gio>.csv   -> snapshot moi lan chay
    TTCB_HNX.csv                   -> file tong: append + lam sach
        * trung article_id -> giu ban scrape moi nhat
        * dong trung lap hoan toan -> xoa

Chay:
    python ttcb_scraper.py                 # scrape ca 3 sub-tab
    python ttcb_scraper.py HNX_PUBLIC      # chi 1 sub-tab
"""

import html as H
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
    from urllib3.exceptions import InsecureRequestWarning
    warnings.simplefilter("ignore", InsecureRequestWarning)
except Exception:
    pass

BASE = "https://hnx.vn"
ENDPOINT = "/ModuleArticles/ArticlesHNX/NextPageThongTinCongBoHNXPS"
PAGE_SIZE = 100  # server bo qua gia tri qua lon (vd 1000) -> dung 100

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "ttcb_data"
MASTER_NAME = "TTCB_HNX.csv"

# sub-tab: (gia tri pArticlesType, nhan hien thi)
SUBTABS = [
    ("", "HNX"),
    ("HNX_PUBLIC", "HNX_PUBLIC"),
    ("CORPORATE_BONDS", "CORPORATE_BONDS"),
]

COLUMNS = ["subtab", "article_id", "post_date", "post_time", "title",
           "has_attachment", "scrape_date", "scrape_time"]


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": BASE + "/tin-tuc-su-kien-ttcbhnx.html",
        "Origin": BASE,
    })
    s.verify = True
    s._ssl_warned = False
    return s


def fetch(session, articles_type, page, rec=PAGE_SIZE):
    data = {
        "pFromDate": "", "pToDate": "", "pTitle": "", "category": "",
        "pNumPage": page, "pAction": 0, "pNumRecord": rec, "pOnload": 0,
        "pArticlesType": articles_type,
    }
    try:
        r = session.post(BASE + ENDPOINT, data=data, timeout=40, verify=session.verify)
    except requests.exceptions.SSLError:
        if session.verify:
            if not session._ssl_warned:
                print("  [!] Chuoi chung chi HNX khong day du -> ket noi khong xac thuc TLS.")
                session._ssl_warned = True
            session.verify = False
            r = session.post(BASE + ENDPOINT, data=data, timeout=40, verify=False)
        else:
            raise
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def parse_total(html):
    m = re.search(r"(\d[\d.,]*)\s*b\S*n\s*ghi", html)
    return int(re.sub(r"[.,]", "", m.group(1))) if m else None


def parse_rows(html, subtab_label):
    """Bóc tung dong tin tu HTML fragment."""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        if "<td" not in tr:
            continue
        mid = re.search(r"funcViewDetailArticlesByID\((\d+)", tr)
        if not mid:
            continue
        article_id = mid.group(1)
        # ngay + gio
        mdt = re.search(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?", tr)
        post_date = post_time = ""
        if mdt:
            try:
                post_date = datetime.strptime(mdt.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                post_date = mdt.group(1)
            post_time = mdt.group(2) or ""
        # tieu de: text trong the a.hrefViewDetail
        mt = re.search(r"hrefViewDetail[^>]*>(.*?)</a>", tr, re.S)
        title = ""
        if mt:
            title = H.unescape(re.sub(r"<.*?>", " ", mt.group(1)))
            title = re.sub(r"\s+", " ", title).strip()
        has_att = bool(re.search(r"funcShowFileAttach\(", tr))
        out.append({
            "subtab": subtab_label, "article_id": article_id,
            "post_date": post_date, "post_time": post_time, "title": title,
            "has_attachment": has_att,
        })
    return out


def scrape_subtab(session, articles_type, label):
    first = fetch(session, articles_type, 1)
    total = parse_total(first) or 0
    rows = parse_rows(first, label)
    n_pages = max(1, -(-total // PAGE_SIZE))
    for page in range(2, n_pages + 1):
        rows += parse_rows(fetch(session, articles_type, page), label)
        time.sleep(0.25)
    print(f"  [OK] {label:16s}: {len(rows):5d}/{total} tin")
    return rows


def dedup(df):
    if df.empty:
        return df
    df = df.drop_duplicates()
    ts = pd.to_datetime(df["scrape_date"] + " " + df["scrape_time"], errors="coerce")
    df = df.assign(_ts=ts).sort_values("_ts")
    df = df.drop_duplicates(subset=["subtab", "article_id"], keep="last").drop(columns="_ts")
    # sap xep: moi nhat truoc
    df = df.sort_values(["subtab", "post_date", "post_time"], ascending=[True, False, False])
    return df.reset_index(drop=True)


def run(subtabs=None):
    sel = SUBTABS
    if subtabs:
        want = {s.upper() for s in subtabs}
        sel = [(a, l) for (a, l) in SUBTABS if l.upper() in want or a.upper() in want]
        if not sel:
            print("Khong khop sub-tab nao. Chon: HNX / HNX_PUBLIC / CORPORATE_BONDS")
            return None

    session = make_session()
    now = datetime.now()
    sd, st = now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")
    print(f"==> Scrape TTCB HNX ({len(sel)} sub-tab)")

    all_rows = []
    for atype, label in sel:
        try:
            rows = scrape_subtab(session, atype, label)
            for r in rows:
                r["scrape_date"] = sd
                r["scrape_time"] = st
            all_rows += rows
        except Exception as e:
            print(f"  [!!] {label:16s}: LOI - {e}")

    result = pd.DataFrame(all_rows, columns=COLUMNS)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    snap = DATA_DIR / f"TTCB_HNX _ {stamp}.csv"
    result.to_csv(snap, index=False, encoding="utf-8-sig")

    master = DATA_DIR / MASTER_NAME
    try:
        if master.exists():
            old = pd.read_csv(master, dtype=str)
            combined = pd.concat([old, result.astype(str)], ignore_index=True)
        else:
            combined = result.astype(str)
        combined = dedup(combined)
        combined.to_csv(master, index=False, encoding="utf-8-sig")
        n_total = len(combined)
    except PermissionError:
        fb = DATA_DIR / f"TTCB_HNX _CANMERGE_{stamp}.csv"
        result.to_csv(fb, index=False, encoding="utf-8-sig")
        print(f"\n  [!] File tong dang mo -> luu tam: {fb.name} (dong roi chay lai de gop).")
        master, n_total = fb, len(result)

    print(f"\n==> Xong. Snapshot: {snap.name} ({len(result)} tin)")
    print(f"    File tong: {master.name} ({n_total} tin sau lam sach)")
    print(f"    Thu muc  : {DATA_DIR.resolve()}")
    return master


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.strip()]
    run(subtabs=args or None)

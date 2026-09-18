#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bondinfo_scraper.py  -  Lay THONG TIN TINH cua trai phieu (coupon schedule, rating...)
tu CBonds HNX de tinh Clean Price / Accrued Interest / YTM / Duration chuan.

Nguon (private/rieng le): https://cbonds.hnx.vn
    GET /to-chuc-phat-hanh/thong-tin-chi-tiet-trai-phieu?bond_code=<CODE>
    -> HTML dang <i class="input-title">Nhan</i> ... <b class="input-content">Gia tri</b>

Lay: Ngay phat hanh, Ngay dao han, Menh gia, Lai suat phat hanh (coupon %),
     Ky han tra lai (coupon frequency), Loai lai suat, Ket qua/Don vi XHTN (rating).

Cache: bondinfo.csv (chi scrape ma chua co / --refresh de lam lai).

Chay:
    python bondinfo_scraper.py CODE1 CODE2      # vai ma
    python bondinfo_scraper.py --private        # tat ca ma private trong hnx_bonds.db
    python bondinfo_scraper.py --dashboard      # cac ma dang co tren dashboard (window)
"""

import argparse
import csv
import html
import re
import sqlite3
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import time
import warnings
from pathlib import Path

import requests

warnings.simplefilter("ignore")

ROOT = Path(__file__).resolve().parent
PRIVATE_DB = ROOT / "data" / "HNX" / "hnx_bonds.db"
OUT = ROOT / "bondinfo.csv"
CB_DETAIL = "https://cbonds.hnx.vn/to-chuc-phat-hanh/thong-tin-chi-tiet-trai-phieu"

FIELDS = ["code", "issuer", "issue_date", "maturity_date", "face", "coupon_rate",
          "coupon_freq_raw", "coupon_freq", "rate_type", "rating_result",
          "rating_agency", "sector", "source", "fetched"]

HNX_DETAIL = "https://hnx.vn/vi-vn/cophieu-etfs/chi-tiet-chung-khoan-ny-{code}.html"


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"})
    return s


def _pairs(h):
    """Tra ve dict {label -> value} tu cac cap input-title / input-content."""
    out = {}
    for lab, val in re.findall(r'input-title">\s*([^<]+?)\s*</i>.*?input-content">\s*([^<]*?)\s*</b>', h, re.S):
        lab = html.unescape(lab).strip().rstrip(":")
        val = html.unescape(val).strip()
        if lab and lab not in out:
            out[lab] = val
    return out


def _to_iso(d):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def _freq(raw):
    """'6 Tháng' -> 2/nam ; '3 Tháng' -> 4 ; '1 Năm'/'12 Tháng' -> 1 ; '1 Tháng' -> 12."""
    if not raw:
        return None
    r = raw.lower()
    m = re.search(r"(\d+)\s*th[áa]ng", r)
    if m:
        mo = int(m.group(1))
        return round(12 / mo, 4) if mo else None
    m = re.search(r"(\d+)\s*n[ăa]m", r)
    if m:
        yr = int(m.group(1))
        return round(1 / yr, 4) if yr else None
    return None


def _num(x):
    s = re.sub(r"[^\d.]", "", str(x or "").replace(",", ""))
    try:
        return float(s)
    except ValueError:
        return None


def fetch_one(session, code):
    r = session.get(CB_DETAIL, params={"bond_code": code}, timeout=30, verify=False)
    r.raise_for_status()
    r.encoding = "utf-8"
    p = _pairs(r.text)

    def get(*keys):
        for k in p:
            kl = k.lower()
            if all(w in kl for w in keys):
                return p[k]
        return ""

    freq_raw = get("kỳ hạn", "trả lãi") or get("ky han", "tra lai")
    from datetime import date
    return {
        "code": code,
        "issuer": "",
        "issue_date": _to_iso(get("ngày phát hành") or get("ngay phat hanh")),
        "maturity_date": _to_iso(get("ngày đáo hạn") or get("ngay dao han")),
        "face": _num(get("mệnh giá") or get("menh gia")),
        "coupon_rate": _num(get("lãi suất phát hành") or get("lai suat phat hanh")),
        "coupon_freq_raw": freq_raw,
        "coupon_freq": _freq(freq_raw),
        "rate_type": get("loại lãi suất") or get("loai lai suat"),
        "rating_result": get("kết quả xhtn") or get("ket qua xhtn"),
        "rating_agency": get("đơn vị xhtn") or get("don vi xhtn"),
        "sector": "", "source": "cbonds",
        "fetched": date.today().isoformat(),
    }


def fetch_public(session, code):
    """Lay thong tin tinh bond CONG CHUNG tu hnx.vn (server-render dktimkiem)."""
    from datetime import date
    # GET trang chinh nhu browser thuong -> BO header XHR/ajax cua session (neu khong hnx.vn tra stub)
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml",
           "X-Requested-With": None, "Content-Type": None, "Origin": None}
    r = session.get(HNX_DETAIL.format(code=code), timeout=30, verify=False,
                    allow_redirects=True, headers=hdr)
    r.raise_for_status()
    r.encoding = "utf-8"
    pairs = dict(re.findall(
        r'dktimkiem_cell_title">\s*<label>([^<]+)</label>.*?dktimkiem_cell_content">([^<]*)<',
        r.text, re.S))
    p = {html.unescape(k).strip().rstrip(":").strip(): html.unescape(v).strip() for k, v in pairs.items()}

    def get(*keys):
        for k in p:
            if all(w in k.lower() for w in keys):
                return p[k]
        return ""

    def cpn_vn(x):  # hnx.vn: phay = thap phan ("9,175"->9.175, "7.575"->7.575, "10"->10)
        s = str(x or "").strip().replace(" ", "")
        if not s:
            return None
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None

    return {
        "code": code,
        "issuer": get("tên tcph") or get("ten tcph"),
        "issue_date": _to_iso(get("ngày phát hành") or get("ngay phat hanh")),
        "maturity_date": _to_iso(get("ngày đáo hạn") or get("ngay dao han")),
        "face": None,  # hnx.vn khong co menh gia -> suy tu gia o bond_app
        "coupon_rate": cpn_vn(get("lãi suất") or get("lai suat")),
        "coupon_freq_raw": "", "coupon_freq": None, "rate_type": get("loại trái phiếu") or get("loai trai phieu"),
        "rating_result": "", "rating_agency": "",
        "sector": get("tên ngành") or get("ten nganh"),
        "source": "hnx", "fetched": date.today().isoformat(),
    }


def load_cache():
    if not OUT.exists():
        return {}
    with open(OUT, encoding="utf-8-sig", newline="") as f:
        return {row["code"]: row for row in csv.DictReader(f)}


def save_cache(cache):
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in cache.values():
            w.writerow({k: row.get(k, "") for k in FIELDS})


def private_codes():
    if not PRIVATE_DB.exists():
        return []
    con = sqlite3.connect(str(PRIVATE_DB))
    codes = [r[0] for r in con.execute("SELECT DISTINCT Symbol FROM bond_info").fetchall()]
    con.close()
    return codes


def run(codes, refresh=False, public=False):
    cache = load_cache()
    todo = [c for c in codes if refresh or c not in cache]
    src = "hnx.vn (public)" if public else "cbonds (private)"
    print(f"==> {len(codes)} ma [{src}], can lay {len(todo)} (da cache {len(codes)-len(todo)}).")
    if not todo:
        return cache
    session = make_session()
    ok = fail = 0
    for i, c in enumerate(todo, 1):
        try:
            info = fetch_public(session, c) if public else fetch_one(session, c)
            cache[c] = info  # cache ca ket qua rong (da thu) de khong lay lai moi ngay
            if info["maturity_date"] or info["coupon_rate"]:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  [!] {c}: {e}")
        if i % 50 == 0:
            save_cache(cache)
            print(f"    ...{i}/{len(todo)} (ok={ok})")
        time.sleep(0.2)
    save_cache(cache)
    print(f"==> Xong: ok={ok}, fail={fail}. Luu: {OUT}")
    return cache


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--private", action="store_true", help="Tat ca ma private trong hnx_bonds.db")
    ap.add_argument("--public", action="store_true", help="Lay tu hnx.vn (bond cong chung)")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    codes = a.codes
    if a.private:
        codes = private_codes()
    if not codes:
        print("Cho danh sach ma, hoac --private."); sys.exit(1)
    run(codes, refresh=a.refresh, public=a.public)

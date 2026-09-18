#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ratings.py  -  Doc credit rating tu pipeline D:\\Nha\\Credit rating (chay hang ngay).

2 khoa join:
  - Theo MA trai phieu: cot `ma_trai_phieu` trong cac file agency (fiin/saigon/vis/sni/tmr).
  - Theo TEN issuer   : `to_chuc_phat_hanh` (agency) + `toan_bo_doanh_nghiep_cbonds*.csv`.

Tu dong chon file MOI NHAT moi agency. Tra ve:
  rating_of(code, issuer) -> {"r": ket qua, "o": trien vong, "a": to chuc XHTN} hoac None.
"""

import csv
import glob
import os
import re
import unicodedata
from pathlib import Path

RATING_DIR = Path(__file__).resolve().parent / "credit_rating"

AG_SHORT = {"fiin": "Fiin", "saigon": "SGR", "sgr": "SGR", "vis": "VIS",
            "s&i": "SNI", "sni": "SNI", "thanh khoi": "TMR", "tmr": "TMR",
            "fitch": "Fitch", "moody": "Moody's", "s&p": "S&P"}

# Cac to chuc cham thang QUOC TE. 5 CRA noi dia cham thang QUOC GIA.
# Cung ky hieu 'BB+' nhung KHAC NGHIA -> khong duoc cong gop vao mot bieu do.
AG_QUOC_TE = {"Fitch", "Moody's", "S&P"}
SC_NAT, SC_INTL = "nat", "intl"


def _scale(row, ag_short):
    """Thang diem cua 1 dong: uu tien cot `thang_diem` trong file, khong co
    thi suy tu ten to chuc XHTN."""
    v = str(row.get("thang_diem") or "").strip().lower()
    if v:
        return SC_INTL if "quốc tế" in v or "quoc te" in v else SC_NAT
    return SC_INTL if ag_short in AG_QUOC_TE else SC_NAT


def _short_ag(a):
    al = str(a or "").lower()
    for k, v in AG_SHORT.items():
        if k in al:
            return v
    # khong khop agency chuan -> co the la ten cong ty lot vao (toan_bo), gat bo
    if re.search(r"c(ô|o)ng ty|tnhh|c(ổ|o) ph(ầ|a)n|ctcp|jsc|corp", al):
        return ""
    return (a or "").split("(")[0].strip()[:12]


def _norm(s):
    s = str(s or "")
    s = re.sub(r"^[A-Z0-9]{2,10}\s*-\s*", "", s)          # bo prefix "ABB - "
    s = re.sub(r"\([^)]*\)", "", s)                       # bo (ABBANK)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(cong ty|cp|co phan|ctcp|tnhh|mtv|mot thanh vien|tap doan|ngan hang|"
               r"thuong mai|tmcp|corporation|joint stock|jsc|corp|company|ltd|viet nam)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _latest_per_agency(files):
    """Nhom file theo prefix truoc '_xhtn_', chon file co ngay lon nhat."""
    best = {}
    for f in files:
        base = os.path.basename(f)
        m = re.match(r"(.+?)_xhtn_(\d{8})", base)
        if not m:
            continue
        ag, dt = m.group(1), m.group(2)
        if ag not in best or dt > best[ag][0]:
            best[ag] = (dt, f)
    return [v[1] for v in best.values()]


EQUITY_AGENCIES = [("Fiin_xhtn", "Fiin"), ("VIS_xhtn", "VIS"), ("SGR_xhtn", "SGR"),
                   ("TMR_xhtn", "TMR"), ("SNI_xhtn", "SNI"), ("Fitch_xhtn", "Fitch")]


def load():
    """Tra ve (bybond, byissuer, byticker):
        bybond    = XH TRAI PHIEU (loai_y_kien 'Cong cu no', theo ma_trai_phieu)
        byissuer  = XH TO CHUC PHAT HANH (loai_y_kien 'To chuc phat hanh'/'Doanh nghiep'
                    + toan_bo + equity_xhtn + mapping), theo ten issuer da chuan hoa
        byticker  = XH TCPH theo ticker (equity_xhtn)"""
    bybond, byissuer, byticker = {}, {}, {}
    if not RATING_DIR.exists():
        return bybond, byissuer, byticker
    files = [f for f in glob.glob(str(RATING_DIR / "output" / "*_xhtn_*.csv"))
             if "history" not in f and "equity" not in f]
    for f in _latest_per_agency(files):
        try:
            for r in csv.DictReader(open(f, encoding="utf-8-sig")):
                res = (r.get("ket_qua_xep_hang") or "").strip()
                if not res:
                    continue
                ag = r.get("to_chuc_xhtn", "")
                rec = {"r": res, "o": (r.get("trien_vong") or "").strip(), "a": _short_ag(ag)}
                ly = (r.get("loai_y_kien") or "").lower()
                is_bond = ("công cụ nợ" in ly) or ("cong cu no" in ly)
                mtp = (r.get("ma_trai_phieu") or "").strip()
                if is_bond and mtp:                       # XH trai phieu
                    bybond[mtp] = rec
                elif not is_bond:                          # XH to chuc phat hanh
                    k = _norm(r.get("to_chuc_phat_hanh", ""))
                    if k and k not in byissuer:
                        byissuer[k] = rec
        except Exception:
            pass
    # toan bo doanh nghiep cbonds (issuer-level, ban moi nhat)
    tb = sorted(glob.glob(str(RATING_DIR / "toan_bo_doanh_nghiep_cbonds*.csv")))
    if tb:
        try:
            for r in csv.DictReader(open(tb[-1], encoding="utf-8-sig")):
                res = (r.get("Kết quả XHTN gần nhất") or "").strip()
                if not res:
                    continue
                k = _norm(r.get("Tên doanh nghiệp", ""))
                if k and k not in byissuer:
                    byissuer[k] = {"r": res, "o": "", "a": _short_ag(r.get("Đơn vị XHTN gần nhất", ""))}
        except Exception:
            pass

    # equity_xhtn: rating gop theo TICKER (co ca Fitch cho ngan hang/DN lon)
    eq = sorted(glob.glob(str(RATING_DIR / "output" / "equity_xhtn_*.csv")))
    tk2rec = {}
    if eq:
        try:
            for r in csv.DictReader(open(eq[-1], encoding="utf-8-sig")):
                rec = None
                for col, ag in EQUITY_AGENCIES:
                    v = (r.get(col) or "").strip()
                    if v:
                        rec = {"r": v, "o": "", "a": ag}
                        break
                if not rec:
                    continue
                tk = (r.get("ticker") or "").strip().upper()
                if tk:
                    byticker[tk] = rec
                    tk2rec[tk] = rec
                ko = _norm(r.get("to_chuc_duoc_cham", ""))
                if ko and ko not in byissuer:
                    byissuer[ko] = rec
        except Exception:
            pass

    # mapping_ticker_tay: alias ten to chuc -> ticker -> rating (xu ly ten lech / tieng Anh)
    mp = RATING_DIR / "mapping_ticker_tay.csv"
    if mp.exists():
        try:
            for r in csv.DictReader(open(mp, encoding="utf-8-sig")):
                tk = (r.get("ticker") or "").strip().upper()
                rec = tk2rec.get(tk)
                if not rec:
                    continue
                for nm in (r.get("ten_to_chuc"), r.get("ten_thong_dung_en")):
                    k = _norm(nm)
                    if k and k not in byissuer:
                        byissuer[k] = rec
        except Exception:
            pass

    return bybond, byissuer, byticker


def all_records():
    """Toan bo ban ghi rating (de embed vao tab Xep hang tin nhiem).
    Moi record: {ag, iss, code, grade, out, sec, date, typ('bond'/'issuer')}."""
    recs = []
    seen = set()
    if not RATING_DIR.exists():
        return recs
    files = [f for f in glob.glob(str(RATING_DIR / "output" / "*_xhtn_*.csv"))
             if "history" not in f and "equity" not in f]
    for f in _latest_per_agency(files):
        try:
            for r in csv.DictReader(open(f, encoding="utf-8-sig")):
                grade = (r.get("ket_qua_xep_hang") or "").strip()
                if not grade:
                    continue
                ly = (r.get("loai_y_kien") or "").lower()
                typ = "bond" if ("công cụ nợ" in ly or "cong cu no" in ly) else "issuer"
                iss = (r.get("to_chuc_phat_hanh") or "").strip()
                code = (r.get("ma_trai_phieu") or "").strip()
                key = (_norm(iss), code, grade, _short_ag(r.get("to_chuc_xhtn", "")))
                if key in seen:
                    continue
                seen.add(key)
                ag = _short_ag(r.get("to_chuc_xhtn", ""))
                recs.append({
                    "ag": ag, "iss": iss, "code": code,
                    "grade": grade, "out": (r.get("trien_vong") or "").strip(),
                    "sec": (r.get("linh_vuc") or "").strip(),
                    "date": (r.get("ngay_cong_bo") or "").strip(), "typ": typ,
                    "sc": _scale(r, ag),
                })
        except Exception:
            pass
    # bo sung issuer tu toan_bo (nhung DN chua co trong file agency)
    tb = sorted(glob.glob(str(RATING_DIR / "toan_bo_doanh_nghiep_cbonds*.csv")))
    if tb:
        try:
            for r in csv.DictReader(open(tb[-1], encoding="utf-8-sig")):
                grade = (r.get("Kết quả XHTN gần nhất") or "").strip()
                iss = (r.get("Tên doanh nghiệp") or "").strip()
                if not grade:
                    continue
                iss = re.sub(r"^[A-Z0-9]{2,10}\s*-\s*", "", iss)   # bo prefix ma
                ag = _short_ag(r.get("Đơn vị XHTN gần nhất", ""))
                key = (_norm(iss), "", grade, ag)
                if key in seen:
                    continue
                seen.add(key)
                recs.append({"ag": ag, "iss": iss, "code": "", "grade": grade, "out": "",
                             "sec": (r.get("Lĩnh vực hoạt động") or "").strip(),
                             "date": (r.get("Ngày hiệu lực") or "").strip(), "typ": "issuer",
                             "sc": _scale(r, ag)})
        except Exception:
            pass
    return recs


def all_events():
    """Toan bo EVENT cong bo xep hang (giu tung lan cong bo theo ngay, KHONG gop theo hang)
    -> de dung lich su + phat hien nang/ha hang. Nguon: file moi nhat/agency + *_history.csv.
    Moi event: {ag, iss, code, sec, grade, out, date, typ('bond'/'issuer'), status}."""
    evs, seen = [], set()
    if not RATING_DIR.exists():
        return evs
    sec_map = sectors()
    files = [f for f in glob.glob(str(RATING_DIR / "output" / "*_xhtn_*.csv"))
             if "equity" not in f]                       # ca dated snapshot + *_history
    latest = set(_latest_per_agency([f for f in files if "history" not in f]))
    use = list(latest) + [f for f in files if "history" in f]
    for f in use:
        try:
            for r in csv.DictReader(open(f, encoding="utf-8-sig")):
                grade = (r.get("ket_qua_xep_hang") or "").strip()
                if not grade:
                    continue
                iss = (r.get("to_chuc_phat_hanh") or "").strip()
                code = (r.get("ma_trai_phieu") or "").strip()
                date = (r.get("ngay_cong_bo") or "").strip()
                ag = _short_ag(r.get("to_chuc_xhtn", ""))
                key = (ag, _norm(iss), code, date, grade)
                if key in seen:
                    continue
                seen.add(key)
                ly = (r.get("loai_y_kien") or "").lower()
                typ = "bond" if ("công cụ nợ" in ly or "cong cu no" in ly) else "issuer"
                sec = (r.get("linh_vuc") or "").strip() or sec_map.get(_norm(iss), "")
                evs.append({
                    "ag": ag, "iss": iss, "code": code, "sec": sec, "grade": grade,
                    "out": (r.get("trien_vong") or "").strip(), "date": date, "typ": typ,
                    "status": (r.get("trang_thai_xep_hang") or "").strip(),
                    "sc": _scale(r, ag),
                })
        except Exception:
            pass
    return evs


def sectors():
    """issuer_norm -> linh vuc hoat dong (tu toan_bo_doanh_nghiep_cbonds)."""
    out = {}
    tb = sorted(glob.glob(str(RATING_DIR / "toan_bo_doanh_nghiep_cbonds*.csv")))
    if tb:
        try:
            for r in csv.DictReader(open(tb[-1], encoding="utf-8-sig")):
                sec = (r.get("Lĩnh vực hoạt động") or "").strip()
                k = _norm(r.get("Tên doanh nghiệp", ""))
                if k and sec and k not in out:
                    out[k] = sec
        except Exception:
            pass
    return out


def _ticker_of(code):
    m = re.match(r"^([A-Za-z]+)", str(code or ""))
    return m.group(1).upper() if m else ""


def bond_rating_of(bybond, code):
    """XH TRAI PHIEU (chi theo ma trai phieu)."""
    return bybond.get(code)


def issuer_rating_of(byissuer, byticker, code, issuer):
    """XH TO CHUC PHAT HANH (theo ten issuer, roi prefix ma = ticker)."""
    k = _norm(issuer)
    if k and k in byissuer:
        return byissuer[k]
    tk = _ticker_of(code)              # prefix ma bond ~ ticker (SHB125010 -> SHB)
    if tk and tk in byticker:
        return byticker[tk]
    return None

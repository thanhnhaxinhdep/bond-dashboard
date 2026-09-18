# -*- coding: utf-8 -*-
"""
Gan ket qua XHTN cua 6 to chuc xep hang vao danh sach co phieu (theo ticker).

Dau vao:
    tickers_equity.txt               - danh sach ma co phieu
    toan_bo_doanh_nghiep_cbonds*.csv - danh ba cbonds, cot 'Ten doanh nghiep'
                                       co dang 'TICKER - Ten cong ty'
                                       -> dung de KHOP TU DONG
    mapping_ticker_tay.csv           - cac truong hop RA TAY (file nay do
                                       nguoi dung sua, script khong ghi de)
    output/xhtn_tong_hop_*.csv       - ban gop 5 CRA noi dia (merge_xhtn.py)
    fitch-daily/data/snapshot-*.json - snapshot Fitch

Dau ra:
    output/equity_xhtn_<ngay>.xlsx / .csv
    Cot dau la ticker, sau do moi to chuc XHTN 2 cot: <ten>_xhtn, <ten>_ngay

Vi sao tach auto / tay:
    Khop tu dong duoc chay LAI moi lan, nen to chuc phat hanh moi xuat hien
    se tu vao bang ma khong phai sua gi. Chi nhung ten cbonds viet khac
    (hoac ten tieng Anh cua Fitch) moi can ghi tay mot lan.

    KHONG dung khop mo (fuzzy) o day: da thu nguong 0.90 va no khop nham
    'Chung khoan LPBank' -> 'Chung khoan VPBank', 'DSC' -> 'DNSE',
    'KAFI' -> 'AIS'. Ten ngan khac nghia nhung gan giong ve chu.

Quy tac chon khi 1 ticker co nhieu ban ghi cua cung mot to chuc XHTN:
    - Uu tien xep hang TO CHUC PHAT HANH; neu khong co thi lay xep hang
      CONG CU NO va ghi chu lai.
    - Trong cung loai thi lay ban ghi co NGAY CONG BO MOI NHAT.

Chay:
    python build_equity_ratings.py
    python build_equity_ratings.py --goi-y     # in them goi y ten chua khop
"""

import argparse
import glob
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import unicodedata
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")

AGENCIES = ["FiinRatings", "VIS Rating", "Saigon Ratings", "TMR",
            "S&I Ratings", "Fitch Ratings"]

SHORT = {
    "FiinRatings": "Fiin",
    "VIS Rating": "VIS",
    "Saigon Ratings": "SGR",
    "TMR": "TMR",
    "S&I Ratings": "SNI",
    "Fitch Ratings": "Fitch",
}

FITCH_IDR = "Long Term Issuer Default Rating"
LOAI_TCPH = "Tổ chức phát hành"

# 'WR'/'WD' = da rut xep hang, khong phai bac tin nhiem
DA_RUT = {"WR", "WD"}


# --------------------------------------------------------------- chuan hoa ---
def bo_dau(s):
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D")


# Cum chi loai hinh phap ly - bo di de con lai phan dinh danh rieng
LEGAL = [
    r"ngan hang thuong mai co phan", r"ngan hang tmcp", r"nh tmcp",
    r"tong cong ty co phan", r"tong cong ty", r"tong cty",
    r"cong ty co phan tap doan", r"cong ty co phan", r"cong ty cp",
    r"cong ty tnhh mtv", r"cong ty tnhh mot thanh vien", r"cong ty tnhh",
    r"cong ty tai chinh tnhh mtv", r"cong ty tai chinh",
    r"cong ty chung khoan", r"cong ty",
    r"ctcp", r"cty", r"tnhh", r"jsc", r"joint stock company",
    r"tap doan", r"ngan hang", r"quy dau tu", r"\bmtv\b",
]


def chuan(s):
    """Chuan hoa ten de so khop: bo dau, bo ngoac, bo cum loai hinh phap ly."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = bo_dau(s).lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for p in LEGAL:
        s = re.sub(r"\b" + p + r"\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ------------------------------------------------------------------ doc file --
def newest(pattern):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def ghi_an_toan(path, ghi):
    """
    Goi ghi(duong_dan). Neu file dang mo trong Excel (Windows khoa file) thi
    ghi ra ban '<ten>-moi.xlsx' va bao lai, thay vi lam hong ca lan chay.
    """
    try:
        ghi(path)
        return path
    except PermissionError:
        goc, ext = os.path.splitext(path)
        alt = f"{goc}-moi{ext}"
        ghi(alt)
        print(f"  ! {os.path.basename(path)} dang mo (Excel dang khoa) "
              f"-> da ghi ra {os.path.basename(alt)}", file=sys.stderr)
        return alt


def load_tickers(path):
    """
    Doc danh sach ma. Nhan 2 dinh dang:
      - .tsv co 2 cot bloomberg_ticker / ticker  -> tra ve ca ma Bloomberg
      - .txt chi liet ke ma, cach nhau khoang trang
    Tra ve (danh sach ticker, dict ticker -> ma Bloomberg).
    """
    if path.lower().endswith((".tsv", ".csv")):
        sep = "\t" if path.lower().endswith(".tsv") else ","
        df = pd.read_csv(path, sep=sep)
        df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
        df = df.drop_duplicates("ticker")
        bbg = (dict(zip(df["ticker"], df["bloomberg_ticker"]))
               if "bloomberg_ticker" in df.columns else {})
        return list(df["ticker"]), bbg

    txt = open(path, encoding="utf-8").read()
    seen, out = set(), []
    for t in re.split(r"[\s,;]+", txt):
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out, {}


CB_PAT = re.compile(r"^\s*([A-Z0-9]{2,6})\s*-\s*(.+)$")


def load_cbonds(path):
    """Danh ba cbonds -> {ten da chuan hoa: ticker}."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    m = {}
    for s in df["Tên doanh nghiệp"].dropna():
        mt = CB_PAT.match(str(s))
        if not mt:
            continue
        k = chuan(mt.group(2))
        if k:
            m.setdefault(k, mt.group(1).strip().upper())
    return m


def load_tay(path):
    """Cac anh xa ghi tay. Uu tien cao hon khop tu dong."""
    if not os.path.exists(path):
        return {}, pd.DataFrame(columns=["ticker", "ten_to_chuc", "nguon",
                                         "ghi_chu"])
    df = pd.read_csv(path, encoding="utf-8-sig")
    m = {}
    for _, r in df.iterrows():
        k = chuan(r["ten_to_chuc"])
        if k:
            m[k] = str(r["ticker"]).strip().upper()
    return m, df


def load_cra(path):
    df = pd.read_csv(path)
    df["ngay_dt"] = pd.to_datetime(df["ngay_cong_bo"], format="%d/%m/%Y",
                                   errors="coerce")
    return df


def load_fitch(path):
    """Boc Long Term IDR cua tung entity tu snapshot Fitch."""
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for _, ents in (d.get("entities") or {}).items():
        for e in ents:
            idr = [r for r in e.get("ratings", [])
                   if r.get("rating_type") == FITCH_IDR]
            if not idr:
                continue
            idr.sort(key=lambda r: r.get("date_iso") or "", reverse=True)
            r0 = idr[0]
            outlook = (r0.get("outlook_watch") or "").replace("Outlook ", "")
            rows.append({
                "to_chuc_phat_hanh": e.get("entity_name"),
                "to_chuc_xhtn": "Fitch Ratings",
                "bac_xhtn_chuan": r0.get("rating"),
                "loai_xhtn_chuan": LOAI_TCPH,
                "ngay_dt": pd.to_datetime(r0.get("date_iso"), errors="coerce"),
                "trien_vong_chuan": outlook.strip() or None,
            })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------- chon ----
def pick(sub):
    """Chon 1 ban ghi dai dien: uu tien To chuc phat hanh, roi ngay moi nhat."""
    issuer = sub[sub["loai_xhtn_chuan"] == LOAI_TCPH]
    if len(issuer):
        pool, note = issuer, None
    else:
        pool, note = sub, "chi co XH cong cu no"
    pool = pool.sort_values("ngay_dt", ascending=False, na_position="last")
    return pool.iloc[0], note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=os.path.join(HERE, "equity_list.tsv"))
    ap.add_argument("--tay", default=os.path.join(HERE, "mapping_ticker_tay.csv"))
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--goi-y", action="store_true", dest="goi_y",
                    help="in danh sach ten chua khop de ra tay tiep")
    ap.add_argument("--chi-co-rating", action="store_true", dest="chi_co",
                    help="chi giu cac ma co it nhat 1 xep hang")
    args = ap.parse_args()

    tickers, bbg = load_tickers(args.tickers)
    tickerset = set(tickers)
    print(f"- {len(tickers)} ma co phieu  <- {os.path.basename(args.tickers)}")

    cb_path = newest(os.path.join(HERE, "toan_bo_doanh_nghiep_cbonds*.csv"))
    auto = load_cbonds(cb_path) if cb_path else {}
    print(f"- khop tu dong: {len(auto)} ten tu "
          f"{os.path.basename(cb_path) if cb_path else '(khong co cbonds)'}")

    tay, tay_df = load_tay(args.tay)
    print(f"- khop tay    : {len(tay)} ten tu {os.path.basename(args.tay)}")

    name2ticker = dict(auto)
    name2ticker.update(tay)          # ghi tay de len tren khop tu dong

    cra_path = newest(os.path.join(args.out, "xhtn_tong_hop_*.csv"))
    if not cra_path:
        print("Thieu xhtn_tong_hop_*.csv - chay merge_xhtn.py truoc.",
              file=sys.stderr)
        sys.exit(1)
    cra = load_cra(cra_path)
    print(f"- CRA noi dia : {len(cra)} ban ghi  <- {os.path.basename(cra_path)}")

    # Tu 09/2026 Fitch da di qua fitch_to_csv.py -> merge_xhtn.py nen nam san
    # trong bang gop. Chi doc snapshot JSON khi bang gop CHUA co Fitch,
    # de khong nap trung.
    if "Fitch Ratings" in set(cra.get("to_chuc_xhtn", pd.Series(dtype=str))):
        fitch = pd.DataFrame()
        n = int((cra["to_chuc_xhtn"] == "Fitch Ratings").sum())
        print(f"- Fitch       : {n} dong (da co san trong bang gop)")
    else:
        fitch_path = newest(os.path.join(HERE, "fitch-daily", "data",
                                         "snapshot-*.json"))
        if fitch_path:
            fitch = load_fitch(fitch_path)
            print(f"- Fitch       : {len(fitch)} entity co IDR  "
                  f"<- {os.path.basename(fitch_path)}")
        else:
            fitch = pd.DataFrame()
            print("  ! khong thay snapshot Fitch - bo qua cot Fitch",
                  file=sys.stderr)

    allr = pd.concat([cra, fitch], ignore_index=True, sort=False)
    allr["k"] = allr["to_chuc_phat_hanh"].map(chuan)
    allr["ticker"] = allr["k"].map(name2ticker)

    out = pd.DataFrame({"ticker": tickers})
    if bbg:
        out.insert(0, "bloomberg_ticker", out.ticker.map(bbg))
    for ag in AGENCIES:
        out[f"{SHORT[ag]}_xhtn"] = pd.NA
        out[f"{SHORT[ag]}_ngay"] = pd.NA

    notes, ten_cham = {}, {}
    matched = allr[allr.ticker.notna()]
    for (tk, ag), sub in matched.groupby(["ticker", "to_chuc_xhtn"]):
        if tk not in tickerset or ag not in SHORT:
            continue
        row, note = pick(sub)
        i = out.index[out.ticker == tk][0]
        s = SHORT[ag]
        bac = row.get("bac_xhtn_chuan")
        out.at[i, f"{s}_xhtn"] = bac
        d = row.get("ngay_dt")
        out.at[i, f"{s}_ngay"] = d.strftime("%d/%m/%Y") if pd.notna(d) else None
        # giu ten phap nhan tieng Viet de doi chieu (uu tien nguon noi dia)
        nm = row.get("to_chuc_phat_hanh")
        if isinstance(nm, str) and (tk not in ten_cham or ag != "Fitch Ratings"):
            ten_cham[tk] = nm
        if note:
            notes.setdefault(tk, []).append(f"{s}: {note}")
        if str(bac).upper() in DA_RUT:
            notes.setdefault(tk, []).append(f"{s}: da rut xep hang")

    rating_cols = [f"{SHORT[a]}_xhtn" for a in AGENCIES]
    out["so_agency"] = out[rating_cols].notna().sum(axis=1)
    out["to_chuc_duoc_cham"] = out.ticker.map(ten_cham)
    out["ghi_chu"] = out.ticker.map(lambda t: "; ".join(notes.get(t, [])) or None)

    if args.chi_co:
        out = out[out.so_agency > 0].reset_index(drop=True)

    # ---------------------------------------------------------- bao cao ----
    print("\n- Ket qua --------------------------------------------------")
    n_any = int((out.so_agency > 0).sum())
    print(f"  ticker co it nhat 1 xep hang : {n_any}/{len(out)}")
    print(f"  ticker khong co xep hang nao : {len(out) - n_any}")
    for ag in AGENCIES:
        print(f"    {ag:<16}: {int(out[f'{SHORT[ag]}_xhtn'].notna().sum())}")
    multi = out[out.so_agency > 1]
    print(f"  ticker duoc >1 to chuc xep hang: {len(multi)}")
    if len(multi):
        print(multi[["ticker"] + rating_cols].to_string(index=False))

    if args.goi_y:
        chua = sorted(set(allr[allr.ticker.isna()]
                          ["to_chuc_phat_hanh"].dropna()))
        print(f"\n- {len(chua)} ten CHUA khop ticker (them vao "
              f"{os.path.basename(args.tay)} neu can):")
        for n in chua:
            print("   ", n)

    stamp = datetime.now().strftime("%Y%m%d")
    csv_p = os.path.join(args.out, f"equity_xhtn_{stamp}.csv")
    xlsx_p = os.path.join(args.out, f"equity_xhtn_{stamp}.xlsx")
    def _xlsx(p):
        with pd.ExcelWriter(p, engine="openpyxl") as xw:
            out.to_excel(xw, index=False, sheet_name="Equity XHTN")
            if len(tay_df):
                tay_df.to_excel(xw, index=False, sheet_name="Anh xa tay")

    csv_p = ghi_an_toan(csv_p, lambda p: out.to_csv(
        p, index=False, encoding="utf-8-sig"))
    xlsx_p = ghi_an_toan(xlsx_p, _xlsx)
    print(f"\n- Da ghi {csv_p}")
    print(f"- Da ghi {xlsx_p}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Gop ket qua XHTN tu 5 to chuc xep hang tin nhiem VN ve mot bang chuan hoa.

Nguon (chay cac script scrape_*.py truoc):
    FiinRatings | VIS Rating | Saigon Ratings | TMR | S&I Ratings

Script doc file CSV moi nhat cua tung nguon trong ./output, chuan hoa tu vung
roi xuat 1 file gop. Cot GOC luon duoc giu nguyen (hau to _goc) ben canh cot
da chuan hoa, de con doi chieu khi nghi ngo.

Chay:
    python merge_xhtn.py
    python merge_xhtn.py --date 20260813      # chi lay file cua ngay do
    python merge_xhtn.py --strict             # thoat loi neu co gia tri la
"""

import argparse
import glob
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

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# prefix file -> ten to chuc XHTN
SOURCES = {
    "fiinratings": "FiinRatings",
    "visrating": "VIS Rating",
    "saigonratings": "Saigon Ratings",
    "tmr": "TMR",
    "sniratings": "S&I Ratings",
    "fitch": "Fitch Ratings",        # tu fitch_to_csv.py (snapshot fitch-daily)
}

# Fitch cham thang QUOC TE, 5 CRA noi dia cham thang QUOC GIA.
# Cung ky hieu 'BB+' nhung khac nghia -> khong so sanh truc tiep duoc.
THANG_QUOC_TE = {"Fitch Ratings"}

# ---------------------------------------------------------------- tu vung ---
# Moi bang duoi day map GIA TRI GOC (chuan hoa lowercase, bo dau cach thua)
# ve GIA TRI CHUAN. Gia tri la se duoc bao ra thay vi nuot im.

# Loai y kien: xep hang to chuc phat hanh hay xep hang cong cu no
LOAI_XHTN = {
    "xhtn tổ chức phát hành": "Tổ chức phát hành",
    "tổ chức phát hành": "Tổ chức phát hành",
    "doanh nghiệp": "Tổ chức phát hành",      # VIS Rating goi kieu nay
    "xhtn công cụ nợ": "Công cụ nợ",
    "công cụ nợ": "Công cụ nợ",
    # Fitch: cac rating khong phai Long Term IDR (Viability, Support, xgs...)
    "xếp hạng thành phần": "Xếp hạng thành phần",
}

# Hanh dong xep hang -> (hanh dong chuan, da het hieu luc)
HANH_DONG = {
    "xhtn lần đầu": ("Lần đầu", False),
    "xếp hạng lần đầu": ("Lần đầu", False),
    "lần đầu": ("Lần đầu", False),
    "xếp hạng lần đầu (hết hạn)": ("Lần đầu", True),
    "xếp hạng lần đầu (cập nhật thông tin)": ("Lần đầu", False),
    "cập nhật kết quả xhtn": ("Cập nhật", False),
    "cập nhật": ("Cập nhật", False),
    "giám sát theo dõi": ("Giám sát", False),
    "giám sát xếp hạng": ("Giám sát", False),
    "gia hạn giám sát xếp hạng": ("Giám sát", False),
    "giám sát xếp hạng (hết hạn)": ("Giám sát", True),
    "kết thúc": ("Kết thúc", True),
    "nâng bậc": ("Nâng bậc", False),
    "thay đổi triển vọng": ("Thay đổi triển vọng", False),
    "không tiếp tục thực hiện xếp hạng lần đầu": ("Không thực hiện", True),
    # --- Fitch (tieng Anh) ---
    "new rating": ("Lần đầu", False),
    "publish": ("Lần đầu", False),          # cong bo lan dau ra cong chung
    "affirmed": ("Giữ nguyên", False),
    "review - no action": ("Giám sát", False),
    "upgrade": ("Nâng bậc", False),
    "downgrade": ("Hạ bậc", False),
    "withdrawn": ("Kết thúc", True),
    "outlook revision": ("Thay đổi triển vọng", False),
}

# Trien vong -> (trien vong chuan, co dang theo doi ngan han)
# Can cu: dropdown loc cua FiinRatings map ca 'Thuận lợi' lan 'Tích cực' ve
# value="Positive", va 'Không thuận lợi' ve value="Negative".
TRIEN_VONG = {
    "ổn định": ("Ổn định", False),
    "thuận lợi": ("Tích cực", False),
    "tích cực": ("Tích cực", False),
    "không thuận lợi": ("Tiêu cực", False),
    "tiêu cực": ("Tiêu cực", False),
    "theo dõi ngắn hạn xhtn: không thuận lợi": ("Tiêu cực", True),
    "bảo mật": (None, False),          # khong cong bo -> null, co co bao_mat
    # --- Fitch (tieng Anh) ---
    "stable": ("Ổn định", False),
    "positive": ("Tích cực", False),
    "negative": ("Tiêu cực", False),
    "rating watch negative": ("Tiêu cực", True),
    "rating watch positive": ("Tích cực", True),
    "rating watch evolving": (None, True),
}

LOAI_HINH = {
    "doanh nghiệp": "Doanh nghiệp",
    "định chế tài chính": "Định chế tài chính",
    "tổ chức tài chính": "Định chế tài chính",
}

# Thang bac chuan (dai han) -> thu tu de sap xep / so sanh
THANG_BAC = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
             "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
             "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D"]
BAC_RANK = {b: i + 1 for i, b in enumerate(THANG_BAC)}

# Gia tri khong phai bac xep hang thuc su
BAC_DAC_BIET = {
    "WR": "Đã rút xếp hạng",
    "WD": "Đã rút xếp hạng",          # ky hieu cua Fitch
    "SD": "Vỡ nợ một phần",
    "NR": "Không xếp hạng",
}

# Thang NGAN HAN cua Fitch (F1+/F1/F2/F3) - khong cung thang voi dai han,
# khong duoc gan thu tu de tranh lot vao bang xep hang dai han.
BAC_NGAN_HAN = {"F1+", "F1", "F2", "F3"}

CONFIDENTIAL = "bảo mật"


def key(s):
    """Chuan hoa chuoi de tra bang: lowercase, gom khoang trang, NFC."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = unicodedata.normalize("NFC", str(s))
    return re.sub(r"\s+", " ", s).strip().lower() or None


class Unknown:
    """Gom cac gia tri khong co trong bang map de bao cuoi lan chay."""

    def __init__(self):
        self.hits = {}

    def add(self, col, val, source):
        self.hits.setdefault((col, val, source), 0)
        self.hits[(col, val, source)] += 1

    def report(self):
        if not self.hits:
            print("\n[OK] Khong co gia tri la - toan bo tu vung da duoc map.")
            return False
        print("\n[!] CO GIA TRI CHUA MAP - can bo sung vao bang tu vung:",
              file=sys.stderr)
        for (col, val, src), n in sorted(self.hits.items(),
                                         key=lambda x: -x[1]):
            print(f"    {col:<22} {val!r:<45} [{src}] x{n}", file=sys.stderr)
        return True


UNK = Unknown()


def map_loai_xhtn(v, src):
    k = key(v)
    if k is None:
        return None
    if k in LOAI_XHTN:
        return LOAI_XHTN[k]
    UNK.add("loai_y_kien", v, src)
    return None


def map_hanh_dong(v, src):
    k = key(v)
    if k is None:
        return None, None
    if k in HANH_DONG:
        return HANH_DONG[k]
    UNK.add("trang_thai_xep_hang", v, src)
    return None, None


def map_trien_vong(v, src):
    k = key(v)
    if k is None:
        return None, False, False
    if k in TRIEN_VONG:
        tv, watch = TRIEN_VONG[k]
        return tv, watch, (k == CONFIDENTIAL)
    UNK.add("trien_vong", v, src)
    return None, False, False


def map_loai_hinh(v, src):
    k = key(v)
    if k is None:
        return None
    if k in LOAI_HINH:
        return LOAI_HINH[k]
    UNK.add("loai_hinh", v, src)
    return None


def map_bac(v, src):
    """
    Chuan hoa bac xep hang.
    Tra ve (bac_chuan, rank, ghi_chu, bao_mat).
    - Bo tien to 'vn' cua Saigon Ratings (vnBBB- -> BBB-)
    - 'Bao mat' -> None + co bao_mat
    - 'WR'/'WD' -> giu nguyen, khong co rank (khong phai bac tin nhiem)
    - Fitch: bo hau to '(xgs)', thang ngan han F1..F3 khong gan thu tu,
      chu THUONG (bb+, b...) la quy uoc rieng cua Fitch cho xep hang thanh
      phan (Viability / Support) - van quy ve bac goc nhung ghi chu lai.
    """
    k = key(v)
    if k is None:
        return None, None, None, False
    if k == CONFIDENTIAL:
        return None, None, None, True

    raw = re.sub(r"\s+", "", str(v))
    raw = re.sub(r"^vn", "", raw, flags=re.IGNORECASE)   # thang quoc gia cua SGR

    ghi_chu = None
    if raw.lower().endswith("(xgs)"):                    # ban loai tru ho tro CP
        raw = raw[:-5]
        ghi_chu = "Bản (xgs) - loại trừ hỗ trợ Chính phủ"
    if raw and raw.islower() and raw.upper() in BAC_RANK:
        ghi_chu = "; ".join(filter(None, [
            ghi_chu, "Chữ thường - xếp hạng thành phần của Fitch"]))

    up = raw.upper()

    if up in BAC_DAC_BIET:
        return up, None, BAC_DAC_BIET[up], False
    if up in BAC_NGAN_HAN:
        return up, None, "Thang ngắn hạn - không cùng thang dài hạn", False
    if up in BAC_RANK:
        return up, BAC_RANK[up], ghi_chu, False

    UNK.add("ket_qua_xep_hang", v, src)
    return up or None, None, None, False


# ----------------------------------------------------------------- doc file --
def newest_file(out_dir, prefix, date=None):
    if date:
        p = os.path.join(out_dir, f"{prefix}_xhtn_{date}.csv")
        return p if os.path.exists(p) else None
    files = sorted(glob.glob(os.path.join(out_dir, f"{prefix}_xhtn_*.csv")))
    files = [f for f in files if re.search(r"_\d{8}\.csv$", f)]
    return files[-1] if files else None


def load_sources(out_dir, date=None):
    frames = []
    for prefix, name in SOURCES.items():
        path = newest_file(out_dir, prefix, date)
        if path is None:
            print(f"  ! thieu file cua {name} (prefix {prefix}) - bo qua",
                  file=sys.stderr)
            continue
        df = pd.read_csv(path)
        df["_file"] = os.path.basename(path)
        # Phong truong hop file cu thieu cot to_chuc_xhtn
        if "to_chuc_xhtn" not in df.columns:
            df["to_chuc_xhtn"] = name
        frames.append(df)
        print(f"  {name:<16} {len(df):>4} dong  <- {os.path.basename(path)}")
    if not frames:
        print("Khong doc duoc nguon nao.", file=sys.stderr)
        sys.exit(1)
    return frames


def normalize(frames):
    df = pd.concat(frames, ignore_index=True, sort=False)

    # Dam bao cac cot tuy chon deu ton tai
    for c in ["loai_hinh", "ky_han", "linh_vuc", "ma_trai_phieu",
              "bao_cao_vi", "bao_cao_en", "bao_cao_chi_tiet",
              "link_to_chuc_phat_hanh", "issuer_id", "bao_mat",
              "phi_tren_5pc", "loai_rating_goc"]:
        if c not in df.columns:
            df[c] = pd.NA

    src = df["to_chuc_xhtn"]

    # Thang diem: Fitch = quoc te, 5 CRA noi dia = quoc gia. Cot nay phai co
    # truoc khi ai do so sanh 'BB+' cua Fitch voi 'BB+' cua Fiin.
    if "thang_diem" in df.columns:
        df["thang_diem"] = df["thang_diem"].where(
            df["thang_diem"].notna(),
            src.map(lambda a: "Quốc tế" if a in THANG_QUOC_TE else "Quốc gia"))
    else:
        df["thang_diem"] = src.map(
            lambda a: "Quốc tế" if a in THANG_QUOC_TE else "Quốc gia")

    df["loai_xhtn_chuan"] = [map_loai_xhtn(v, s)
                             for v, s in zip(df["loai_y_kien"], src)]

    hd = [map_hanh_dong(v, s) for v, s in zip(df["trang_thai_xep_hang"], src)]
    df["hanh_dong_chuan"] = [x[0] for x in hd]
    df["da_het_hieu_luc"] = [bool(x[1]) if x[1] is not None else False
                             for x in hd]

    tv = [map_trien_vong(v, s) for v, s in zip(df["trien_vong"], src)]
    df["trien_vong_chuan"] = [x[0] for x in tv]
    df["theo_doi_ngan_han"] = [x[1] for x in tv]
    tv_confid = [x[2] for x in tv]

    bac = [map_bac(v, s) for v, s in zip(df["ket_qua_xep_hang"], src)]
    df["bac_xhtn_chuan"] = [x[0] for x in bac]
    df["bac_thu_tu"] = [x[1] for x in bac]
    df["ghi_chu_bac"] = [x[2] for x in bac]
    bac_confid = [x[3] for x in bac]

    df["loai_hinh_chuan"] = [map_loai_hinh(v, s)
                             for v, s in zip(df["loai_hinh"], src)]

    # bao_mat: gop tu cot san co (Saigon Ratings) + suy ra tu bac/trien vong
    def as_bool(col):
        """NA -> False, giu nguyen bool; tranh downcast ngam cua pandas."""
        return df[col].map(lambda v: bool(v) if pd.notna(v) else False) \
                      .astype(bool)

    df["bao_mat"] = (as_bool("bao_mat")
                     | pd.Series(bac_confid, index=df.index)
                     | pd.Series(tv_confid, index=df.index))
    df["phi_tren_5pc"] = as_bool("phi_tren_5pc")

    df["ngay_cong_bo_dt"] = pd.to_datetime(
        df["ngay_cong_bo"], format="%d/%m/%Y", errors="coerce")

    return df


FINAL_COLS = [
    # dinh danh
    "to_chuc_xhtn", "ngay_cong_bo", "ngay_cong_bo_dt", "to_chuc_phat_hanh",
    # da chuan hoa
    "thang_diem", "loai_hinh_chuan", "linh_vuc", "loai_xhtn_chuan",
    "hanh_dong_chuan", "bac_xhtn_chuan", "bac_thu_tu", "trien_vong_chuan",
    # co
    "da_het_hieu_luc", "theo_doi_ngan_han", "bao_mat", "phi_tren_5pc",
    "ghi_chu_bac",
    # chi tiet
    "ma_trai_phieu", "ky_han", "loai_rating_goc", "bao_cao_vi", "bao_cao_en",
    "bao_cao_chi_tiet", "link_to_chuc_phat_hanh", "issuer_id",
    # gia tri goc de doi chieu
    "loai_hinh", "loai_y_kien", "trang_thai_xep_hang", "ket_qua_xep_hang",
    "trien_vong",
    # truy vet
    "thoi_gian_scrape", "_file",
]

RENAME_GOC = {
    "loai_hinh": "loai_hinh_goc",
    "loai_y_kien": "loai_y_kien_goc",
    "trang_thai_xep_hang": "trang_thai_goc",
    "ket_qua_xep_hang": "bac_xhtn_goc",
    "trien_vong": "trien_vong_goc",
    "_file": "file_nguon",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--date", help="chi lay file cua ngay YYYYMMDD")
    ap.add_argument("--strict", action="store_true",
                    help="thoat voi ma loi neu con gia tri chua map")
    args = ap.parse_args()

    print("- Doc cac nguon ...")
    frames = load_sources(args.out, args.date)

    print("\n- Chuan hoa tu vung ...")
    df = normalize(frames)

    df = df[[c for c in FINAL_COLS if c in df.columns]].rename(
        columns=RENAME_GOC)
    df = df.sort_values(["ngay_cong_bo_dt", "to_chuc_xhtn",
                         "to_chuc_phat_hanh"],
                        ascending=[False, True, True],
                        na_position="last").reset_index(drop=True)

    print(f"  tong {len(df)} ban ghi tu {df.to_chuc_xhtn.nunique()} to chuc XHTN")

    # --- chan doan ---
    print("\n- Kiem tra ---------------------------------------------------")
    n_no_date = int(df.ngay_cong_bo_dt.isna().sum())
    n_no_bac = int(df.bac_xhtn_chuan.isna().sum())
    n_no_loai = int(df.loai_xhtn_chuan.isna().sum())
    n_no_hd = int(df.hanh_dong_chuan.isna().sum())
    print(f"  thieu ngay cong bo   : {n_no_date}")
    print(f"  thieu bac xep hang   : {n_no_bac}  (gom {int(df.bao_mat.sum())} dong bao mat)")
    print(f"  thieu loai XHTN      : {n_no_loai}")
    print(f"  thieu hanh dong      : {n_no_hd}")

    dup_cols = ["to_chuc_xhtn", "to_chuc_phat_hanh", "ngay_cong_bo",
                "loai_xhtn_chuan", "ma_trai_phieu", "bac_xhtn_goc"]
    dups = df[df.duplicated(dup_cols, keep=False)]
    print(f"  dong trung khoa       : {len(dups)}")
    if len(dups):
        print("    (khoa = to chuc XHTN + ten + ngay + loai + ma TP + bac)")
        with pd.option_context("display.width", 200, "display.max_colwidth", 30):
            print(dups[["to_chuc_xhtn", "ngay_cong_bo", "to_chuc_phat_hanh",
                        "loai_xhtn_chuan", "bac_xhtn_goc"]].head(12).to_string())

    has_unknown = UNK.report()

    # --- xuat ---
    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(args.out, f"xhtn_tong_hop_{stamp}.csv")
    xlsx_path = os.path.join(args.out, f"xhtn_tong_hop_{stamp}.xlsx")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Tong hop")
        (df.groupby(["to_chuc_xhtn", "bac_xhtn_chuan"]).size()
           .rename("so_luot").reset_index()
           .to_excel(xw, index=False, sheet_name="Theo bac"))
        (df.groupby(["to_chuc_xhtn", "hanh_dong_chuan"]).size()
           .rename("so_luot").reset_index()
           .to_excel(xw, index=False, sheet_name="Theo hanh dong"))
    print(f"\n- Da ghi {csv_path}")
    print(f"- Da ghi {xlsx_path}")

    print("\nPhan bo theo to chuc XHTN:")
    print(df.groupby("to_chuc_xhtn").agg(
        so_luot=("to_chuc_phat_hanh", "size"),
        so_to_chuc=("to_chuc_phat_hanh", "nunique"),
        tu_ngay=("ngay_cong_bo_dt", "min"),
        den_ngay=("ngay_cong_bo_dt", "max"),
    ).to_string())

    if has_unknown and args.strict:
        sys.exit(2)


if __name__ == "__main__":
    main()

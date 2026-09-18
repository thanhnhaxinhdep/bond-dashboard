# -*- coding: utf-8 -*-
"""
Chuyen snapshot JSON cua fitch-daily -> CSV dung schema cua cac agency khac,
de Fitch chay chung duong ong voi 5 CRA noi dia.

    fitch-daily/data/snapshot-YYYY-MM-DD.json  ->  output/fitch_xhtn_YYYYMMDD.csv

Sau buoc nay:
  - merge_xhtn.py     tu gop Fitch vao xhtn_tong_hop (da them "fitch" vao SOURCES)
  - ratings.py cua dashboard bond tu nhat file nay qua glob *_xhtn_*.csv,
    KHONG can sua code ben do.

MAC DINH CHI LAY "Long Term Issuer Default Rating" (32 dong).
Vi sao: snapshot co 177 dong nhung phan lon la xep hang THANH PHAN
(Viability, Government/Shareholder Support, Country Ceiling, ban (xgs),
va thang ngan han F2). Cac loai do khong cung khai niem voi "ket qua XHTN"
cua 5 CRA noi dia; do het vao bang gop se lam sai moi thong ke (phan bo hang,
dem nang/ha hang). Muon lay du 177 dong thi chay voi --tat-ca.

LUU Y THANG DIEM: Fitch cham thang QUOC TE, 5 CRA noi dia cham thang QUOC GIA.
Cung ky hieu "BB+" nhung khac nghia. Cot `thang_diem` danh dau viec nay.

Chay:
    python fitch_to_csv.py
    python fitch_to_csv.py --tat-ca        # lay ca xep hang thanh phan
"""

import argparse
import glob
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "fitch-daily", "data")
OUT_DIR = os.path.join(HERE, "output")

CRA_NAME = "Fitch Ratings"
IDR = "Long Term Issuer Default Rating"

# Bao dong neu snapshot qua cu (scraper Fitch chay rieng, de bi quen)
CANH_BAO_NGAY = 10

# Cot y het cac file agency khac, cong 3 cot rieng cua Fitch o cuoi
COLUMNS = [
    "ngay_cong_bo", "to_chuc_phat_hanh", "linh_vuc", "loai_hinh",
    "trang_thai_xep_hang", "loai_y_kien", "ky_han", "ket_qua_xep_hang",
    "trien_vong", "ma_trai_phieu", "bao_cao_vi", "bao_cao_en",
    "link_to_chuc_phat_hanh", "issuer_id", "to_chuc_xhtn", "thoi_gian_scrape",
    "thang_diem", "loai_rating_goc", "ngay_snapshot",
]


def newest_snapshot():
    files = sorted(glob.glob(os.path.join(SNAP_DIR, "snapshot-*.json")))
    return files[-1] if files else None


def convert(path, tat_ca=False):
    d = json.load(open(path, encoding="utf-8"))
    ngay_snap = d.get("ngay") or ""
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows, bo_qua = [], 0
    for _, ents in (d.get("entities") or {}).items():
        for e in ents:
            for r in e.get("ratings", []):
                rtype = (r.get("rating_type") or "").strip()
                if not tat_ca and rtype != IDR:
                    bo_qua += 1
                    continue
                grade = (r.get("rating") or "").strip()
                if not grade:
                    continue

                iso = r.get("date_iso") or ""
                try:
                    ngay = datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
                except ValueError:
                    ngay = None

                # 'Outlook Stable' -> 'Stable'; 'Rating Watch Negative' giu nguyen
                ow = (r.get("outlook_watch") or "").strip()
                trien_vong = ow[len("Outlook "):].strip() if ow.startswith("Outlook ") else ow

                rows.append({
                    "ngay_cong_bo": ngay,
                    "to_chuc_phat_hanh": (e.get("entity_name") or "").strip(),
                    "linh_vuc": None,
                    # entity_type cua Fitch la 'Ultimate Parent'..., khong phai
                    # Doanh nghiep / Dinh che tai chinh -> de trong cho khoi sai
                    "loai_hinh": None,
                    "trang_thai_xep_hang": (r.get("action") or "").strip() or None,
                    "loai_y_kien": ("Tổ chức phát hành" if rtype == IDR
                                    else "Xếp hạng thành phần"),
                    "ky_han": "Dài hạn" if rtype == IDR else None,
                    "ket_qua_xep_hang": grade,
                    "trien_vong": trien_vong or None,
                    "ma_trai_phieu": None,
                    "bao_cao_vi": None,
                    "bao_cao_en": None,
                    "link_to_chuc_phat_hanh": e.get("entity_url"),
                    "issuer_id": e.get("entity_id"),
                    "to_chuc_xhtn": CRA_NAME,
                    "thoi_gian_scrape": scraped_at,
                    "thang_diem": "Quốc tế",
                    "loai_rating_goc": rtype,
                    "ngay_snapshot": ngay_snap,
                })
    return rows, bo_qua, ngay_snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--tat-ca", action="store_true", dest="tat_ca",
                    help="lay ca xep hang thanh phan (Viability, Support, xgs...)")
    args = ap.parse_args()

    snap = newest_snapshot()
    if not snap:
        print(f"Khong tim thay snapshot trong {SNAP_DIR}", file=sys.stderr)
        print("Chay scraper Fitch truoc: cd fitch-daily && node fitch-daily.js",
              file=sys.stderr)
        sys.exit(1)

    print(f"- Doc {os.path.basename(snap)}")
    rows, bo_qua, ngay_snap = convert(snap, args.tat_ca)
    if not rows:
        print("Snapshot khong co dong rating nao.", file=sys.stderr)
        sys.exit(1)

    print(f"  lay {len(rows)} dong" +
          (f", bo qua {bo_qua} dong xep hang thanh phan" if bo_qua else ""))

    # Snapshot cu -> canh bao (scraper Fitch chay rieng, khong nam trong bat)
    if ngay_snap:
        try:
            tuoi = (datetime.now() - datetime.strptime(ngay_snap, "%Y-%m-%d")).days
            if tuoi > CANH_BAO_NGAY:
                print(f"  ! SNAPSHOT CU {tuoi} NGAY (ngay {ngay_snap}). "
                      f"Chay lai: cd fitch-daily && node fitch-daily.js",
                      file=sys.stderr)
            else:
                print(f"  snapshot ngay {ngay_snap} ({tuoi} ngay truoc)")
        except ValueError:
            pass

    os.makedirs(args.out, exist_ok=True)
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["ngay_cong_bo_dt"] = pd.to_datetime(df["ngay_cong_bo"],
                                           format="%d/%m/%Y", errors="coerce")

    stamp = datetime.now().strftime("%Y%m%d")
    path = os.path.join(args.out, f"fitch_xhtn_{stamp}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"- Da ghi {path}")

    hist = os.path.join(args.out, "fitch_xhtn_history.csv")
    df.to_csv(hist, mode="a", index=False,
              header=not os.path.exists(hist), encoding="utf-8-sig")
    print(f"- Da noi them vao {hist}")

    print("\nPhan bo hang (Long Term IDR):")
    print(df["ket_qua_xep_hang"].value_counts().to_string())


if __name__ == "__main__":
    main()

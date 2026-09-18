"""Xoa cac file dated (vd fiinratings_xhtn_20260918.csv/.xlsx) cu hon ban MOI
NHAT cua tung prefix trong output/, GIU NGUYEN moi file *_history.csv (accumulator
that su). Chay sau merge_xhtn.py + build_equity_ratings.py moi lan build, de
thu muc output/ khong phinh to vo han qua tung ngay commit len git (repo cong
khai, chay CI hang ngay).

Chay: python cleanup_dated_outputs.py [--out DIR]
"""
import argparse
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATED_RE = re.compile(r"^(.*_xhtn)_(\d{8})(-moi)?\.(csv|xlsx)$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "output"))
    args = ap.parse_args()

    by_prefix_ext = defaultdict(list)  # (prefix, ext) -> [(date, fname), ...]
    for fname in os.listdir(args.out):
        if "history" in fname:
            continue
        m = DATED_RE.match(fname)
        if not m:
            continue
        prefix, datestr, _moi, ext = m.groups()
        by_prefix_ext[(prefix, ext)].append((datestr, fname))

    removed = 0
    for (_prefix, _ext), items in by_prefix_ext.items():
        items.sort()  # tang dan theo ngay (chuoi YYYYMMDD so sanh duoc truc tiep)
        for _datestr, fname in items[:-1]:
            os.remove(os.path.join(args.out, fname))
            removed += 1

    print(f"Da xoa {removed} file dated cu, giu lai ban moi nhat moi nguon (+ history.csv).")


if __name__ == "__main__":
    main()

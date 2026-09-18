#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bond_app.py  -  App tong hop du lieu Trai phieu Doanh nghiep HNX hang ngay.

Gop 2 thi truong:
  - Rieng le (private placement) : D:\\Data\\HNX\\hnx_bonds.db  (task CBONDS 3:03PM tu cap nhat)
  - Cong chung (public offering)  : tpdn_data/TPDN_trading.csv  (tpdn_scraper.py)

=> Tim Top 20 trai phieu theo GIA TRI GIAO DICH (ngay moi nhat)
=> Sinh 1 dashboard HTML TUONG TAC tu chua (bond_dashboard.html) va tu mo len.

Chay:
  python bond_app.py                 # cap nhat public + dung dashboard + mo len
  python bond_app.py --no-update     # khong scrape lai, chi dung tu data hien co
  python bond_app.py --no-open       # dung dashboard nhung khong tu mo (cho scheduler chay ngam)

Yeu cau: pandas (+ tpdn_scraper cho phan cap nhat public).
"""

import argparse
import json
import os
import sqlite3
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import datetime, date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PRIVATE_DB = ROOT / "data" / "HNX" / "hnx_bonds.db"
PUBLIC_CSV = ROOT / "tpdn_data" / "TPDN_trading.csv"
OUT_HTML = ROOT / "bond_dashboard.html"
STATE_FILE = ROOT / "ttcb_data" / "dashboard_state.json"
PUBLISH_DIR = ROOT.parent  # repo GitHub Pages (chinh la repo dang chua pipeline/ nay)
RATING_RUNNER = ROOT / "credit_rating" / "run-rating-daily.sh"  # khong dung o CI - rating chay rieng tung buoc trong workflow
TREND_DAYS = 120  # so ngay hien tren bieu do xu huong

# Thong tin lien he hien tren banner (khi nguoi xem can du lieu moi). SUA TAI DAY.
CONTACT_INFO = "Liên hệ: nha.nguyen@koreainvestment.com.vn — Zalo/Whatsapp"
REQUEST_COOLDOWN_MIN = 30  # phut: nhip cho phep bam nut yeu cau


# ------------------------------------------------------------------ helpers
def num(x):
    s = str(x).replace(",", "").strip()
    if s in ("", "nan", "None", "-", "NaN"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ load data
def load_private():
    if not PRIVATE_DB.exists():
        return pd.DataFrame(), None
    con = sqlite3.connect(str(PRIVATE_DB))
    df = pd.read_sql("SELECT * FROM bond_info", con)
    con.close()
    df = df.sort_values("scrape_time").drop_duplicates(["scrape_day", "Symbol"], keep="last")
    df["tv"] = df["Total Value"].map(num)
    df["vol"] = df["Total Volume"].map(num)
    df["price"] = df["Last Price"].map(num)
    return df, (df["scrape_day"].max() if len(df) else None)


def load_public():
    if not PUBLIC_CSV.exists():
        return pd.DataFrame(), None
    df = pd.read_csv(PUBLIC_CSV, dtype=str)
    df["tv"] = df["total_value"].map(num)
    df["vol"] = df["total_volume"].map(num)
    df["price"] = df["close_price"].map(num)
    return df, (df["trading_date"].max() if len(df) else None)


def daily_turnover(df, datecol):
    g = df.groupby(datecol)["tv"].sum()
    return {d: float(v) for d, v in g.items()}


def load_bondinfo():
    """Doc bondinfo.csv (tu bondinfo_scraper.py): code -> coupon schedule + rating."""
    import csv
    path = ROOT / "bondinfo.csv"
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            out[r["code"]] = r
    return out


def build_payload(priv, priv_day, pub, pub_day):
    """Nhung du lieu theo NGAY cho ca 2 thi truong trong cua so co ca private+public,
    de dashboard cho chon 1 ngay bat ky hoac 1 khoang thoi gian."""
    # --- cua so = pham vi ngay cua private (noi ca 2 cung ton tai) ---
    if len(priv):
        pdates = sorted(priv["scrape_day"].dropna().unique())
        wmin, wmax = pdates[0], pdates[-1]
    else:
        wmin = wmax = None

    pub_w = pub[(pub["trading_date"] >= wmin) & (pub["trading_date"] <= wmax)] if wmin else pub.iloc[0:0]

    meta = {}     # code -> {iss, cp, mt, m}
    recs = []     # {d, c, p, v, val}  (chi dong CO giao dich)
    listed = {"private": {}, "public": {}}

    for d, g in priv.groupby("scrape_day"):
        listed["private"][d] = int(len(g))
    for d, g in pub_w.groupby("trading_date"):
        listed["public"][d] = int(len(g))

    for _, r in priv[priv["tv"] > 0].iterrows():
        code = r["Symbol"]
        if code not in meta:
            meta[code] = {"iss": str(r.get("Issuer Name", "") or ""),
                          "cp": str(r.get("Interest Rate", "") or ""),
                          "mt": str(r.get("Maturity Date", "") or ""),
                          "par": int(num(r.get("Par Value", ""))), "m": "private"}
        recs.append({"d": r["scrape_day"], "c": code,
                     "p": int(r["price"]), "v": int(r["vol"]), "val": int(r["tv"])})
    for _, r in pub_w[pub_w["tv"] > 0].iterrows():
        code = r["bond_code"]
        if code not in meta:
            meta[code] = {"iss": "", "cp": "", "mt": "", "par": 0, "m": "public"}
        recs.append({"d": r["trading_date"], "c": code,
                     "p": int(r["price"]), "v": int(r["vol"]), "val": int(r["tv"])})

    # nhung coupon schedule + rating tu bondinfo.csv (cbonds) de tinh clean price / YTM chuan
    info = load_bondinfo()
    for code, m in meta.items():
        bi = info.get(code)
        if not bi:
            continue
        if bi.get("coupon_freq"):
            try:
                m["frq"] = float(bi["coupon_freq"])
            except ValueError:
                pass
        if bi.get("issue_date"):
            m["isd"] = bi["issue_date"]
        if bi.get("maturity_date") and not m.get("mt"):
            m["mt"] = bi["maturity_date"]
        if bi.get("coupon_rate") and not str(m.get("cp") or "").strip():
            m["cp"] = bi["coupon_rate"]
        if bi.get("issuer") and not str(m.get("iss") or "").strip():
            m["iss"] = bi["issuer"]
        if bi.get("sector"):
            m["sector"] = bi["sector"]
        if bi.get("rating_result"):
            m["rt"] = bi["rating_result"]

    # credit rating tu pipeline D:\Nha\Credit rating (theo ma bond hoac issuer)
    rating_records = []
    rating_events = []
    try:
        import ratings
        rb, ri, rk = ratings.load()
        rating_records = ratings.all_records()
        rating_events = ratings.all_events()
        sec = ratings.sectors()
        for code, m in meta.items():
            bd = ratings.bond_rating_of(rb, code)          # XH trai phieu
            if bd:
                m["rt_b"], m["rt_b_ag"], m["rt_b_out"] = bd["r"], bd["a"], bd["o"]
            iss = ratings.issuer_rating_of(ri, rk, code, m.get("iss", ""))  # XH TCPH
            if iss:
                m["rt_i"], m["rt_i_ag"], m["rt_i_out"] = iss["r"], iss["a"], iss["o"]
            if not m.get("sector"):
                s = sec.get(ratings._norm(m.get("iss", "")))
                if s:
                    m["sector"] = s
    except Exception as e:
        print(f"  [!] Bo qua ratings: {e}")

    dpriv = {x["d"] for x in recs if meta[x["c"]]["m"] == "private"}
    dpub = {x["d"] for x in recs if meta[x["c"]]["m"] == "public"}
    dates = sorted(dpriv | dpub)
    dates_both = sorted(dpriv & dpub)
    default_date = dates_both[-1] if dates_both else (dates[-1] if dates else None)

    now = datetime.now()
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_ms": int(now.timestamp() * 1000),
        "contact": CONTACT_INFO,
        "cooldown_min": REQUEST_COOLDOWN_MIN,
        "window": {"min": wmin, "max": wmax},
        "dates": dates, "dates_both": dates_both, "default_date": default_date,
        "latest_private": priv_day, "latest_public": pub_day,
        "meta": meta, "recs": recs, "listed": listed,
        "rating_records": rating_records,
        "rating_events": rating_events,
    }


# ------------------------------------------------------------------ HTML
HTML_TEMPLATE = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Bond Market Dashboard — HNX</title>
<style>
:root{
  --bg:#f4f6fb; --panel:#ffffff; --ink:#0f172a; --muted:#64748b; --line:#e2e8f0;
  --priv:#2563eb; --pub:#f59e0b; --accent:#0ea5e9; --good:#16a34a;
  --shadow:0 1px 3px rgba(15,23,42,.08),0 1px 2px rgba(15,23,42,.04);
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#0b1120; --panel:#131c31; --ink:#e6edf7; --muted:#93a1b8; --line:#26324a;
         --priv:#60a5fa; --pub:#fbbf24; --accent:#38bdf8; --shadow:0 1px 3px rgba(0,0,0,.4);}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif;font-size:14px;}
.wrap{max-width:1280px;margin:0 auto;padding:20px 18px 60px;}
header.top{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px;justify-content:space-between;margin-bottom:18px}
h1{font-size:22px;margin:0;letter-spacing:.2px}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
.asof{font-size:12px;color:var(--muted);text-align:right;line-height:1.5}
.asof b{color:var(--ink)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow)}
.card .lbl{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.card .val{font-size:24px;font-weight:700;margin-top:6px}
.card .foot{font-size:12px;color:var(--muted);margin-top:4px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;box-shadow:var(--shadow);margin-bottom:20px}
.panel h2{font-size:16px;margin:0 0 2px}
.panel .hint{color:var(--muted);font-size:12px;margin-bottom:14px}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
.chip{border:1px solid var(--line);background:transparent;color:var(--muted);padding:6px 12px;border-radius:999px;cursor:pointer;font-size:13px}
.chip.active{background:var(--ink);color:var(--panel);border-color:var(--ink)}
input.search{flex:1;min-width:180px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.3px;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--panel)}
th:first-child,td:first-child,td.l,th.l{text-align:left}
td.l.issuer{white-space:normal;color:var(--muted);max-width:280px;font-size:12px}
tbody tr:hover{background:color-mix(in srgb,var(--accent) 8%,transparent)}
.badge{font-size:11px;padding:2px 8px;border-radius:6px;font-weight:600}
.b-priv{background:color-mix(in srgb,var(--priv) 18%,transparent);color:var(--priv)}
.b-pub{background:color-mix(in srgb,var(--pub) 22%,transparent);color:var(--pub)}
.barwrap{position:relative}
.tablescroll{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:8px}
.rk{color:var(--muted);font-variant-numeric:tabular-nums}
.mono{font-variant-numeric:tabular-nums}
.legend{display:flex;gap:16px;font-size:12px;color:var(--muted);margin:2px 0 10px}
svg text{fill:var(--muted)}
.footer{color:var(--muted);font-size:12px;text-align:center;margin-top:24px}
.pill{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.langsw{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:6px}
.lang{border:0;background:var(--panel);color:var(--muted);padding:5px 11px;cursor:pointer;font-size:12px;font-weight:600}
.lang.active{background:var(--ink);color:var(--panel)}
.datebar{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;box-shadow:var(--shadow);margin-bottom:20px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
.datebar label{font-size:12px;color:var(--muted);margin-right:2px}
.datebar input[type=date]{padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font-size:13px;font-family:inherit}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;background:var(--panel);color:var(--muted);padding:6px 12px;cursor:pointer;font-size:13px;font-weight:600}
.seg button.active{background:var(--accent);color:#fff}
.btn{border:1px solid var(--accent);background:transparent;color:var(--accent);padding:6px 12px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600}
.selnote{font-size:12px;color:var(--muted);margin-left:auto}
.selnote b{color:var(--ink)}
.hidden{display:none}
.reqbar{background:linear-gradient(0deg,var(--panel),var(--panel));border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:12px;padding:10px 16px;box-shadow:var(--shadow);margin-bottom:20px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
.reqbar .rc{font-size:13px}
.reqbar .rc b{color:var(--ink)}
#req-btn{border:1px solid var(--line);background:var(--bg);color:var(--muted);padding:7px 14px;border-radius:8px;font-size:13px;font-weight:600;cursor:not-allowed}
#req-btn.on{border-color:var(--accent);background:var(--accent);color:#fff;cursor:pointer}
.req-msg{font-size:12px;color:var(--muted)}
.req-msg b{color:var(--accent)}
.calcgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px 16px;align-items:center;margin-bottom:14px}
.calcgrid label{font-size:12px;color:var(--muted)}
.calcgrid input,.calcgrid select{width:100%;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font-size:13px;font-family:inherit}
.calcout{display:flex;flex-wrap:wrap;gap:10px}
.calcout .kpi{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.calcout .kpi .k{font-size:11px;color:var(--muted)}
.calcout .kpi .v{font-size:18px;font-weight:700;margin-top:3px}
.tabbar{display:flex;gap:6px;border-bottom:2px solid var(--line);margin-bottom:18px}
.tabbtn{border:0;background:transparent;color:var(--muted);padding:10px 18px;cursor:pointer;font-size:15px;font-weight:700;border-bottom:3px solid transparent;margin-bottom:-2px}
.tabbtn.active{color:var(--ink);border-bottom-color:var(--accent)}
.gradechip{display:inline-block;padding:2px 8px;border-radius:6px;font-weight:700;font-size:12px}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <h1 id="t-title"></h1>
      <div class="sub" id="t-sub"></div>
    </div>
    <div style="text-align:right">
      <div class="langsw">
        <button class="lang" data-lang="vi">VI</button><button class="lang" data-lang="en">EN</button>
      </div>
      <div class="asof" id="asof"></div>
    </div>
  </header>

  <div class="tabbar">
    <button class="tabbtn active" data-view="market" id="tab-market-btn"></button>
    <button class="tabbtn" data-view="rating" id="tab-rating-btn"></button>
  </div>

  <div class="reqbar">
    <span class="rc" id="req-contact"></span>
    <button id="req-btn"></button>
    <span class="req-msg" id="req-msg"></span>
  </div>

  <div id="view-market">
  <div class="datebar">
    <div class="seg">
      <button data-mode="day" class="active" id="m-day"></button>
      <button data-mode="range" id="m-range"></button>
    </div>
    <span id="single-wrap"><label id="lb-day"></label><input type="date" id="d-day"/></span>
    <span id="range-wrap" class="hidden">
      <label id="lb-from"></label><input type="date" id="d-from"/>
      <label id="lb-to"></label><input type="date" id="d-to"/>
    </span>
    <button class="btn" id="btn-latest"></button>
    <span class="selnote" id="selnote"></span>
  </div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2 id="t-top-h"></h2>
    <div class="hint" id="t-top-hint"></div>
    <div class="controls">
      <button class="chip active" data-mk="all"></button>
      <button class="chip" data-mk="private"><span class="dot" style="background:var(--priv)"></span><span></span></button>
      <button class="chip" data-mk="public"><span class="dot" style="background:var(--pub)"></span><span></span></button>
    </div>
    <div class="barwrap"><svg id="bars" width="100%" preserveAspectRatio="xMinYMin meet"></svg></div>
  </div>

  <div class="panel">
    <h2 id="t-detail-h"></h2>
    <div class="hint" id="t-detail-hint"></div>
    <div class="controls">
      <input class="search" id="lookup" style="max-width:260px"/>
      <span class="pill" id="lookup-res"></span>
      <span style="margin-left:auto"></span>
      <label id="lb-setpriv" style="font-size:12px;color:var(--muted)"></label>
      <input type="number" id="set-priv" value="0" min="0" max="5" style="width:52px;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)"/>
      <label id="lb-setpub" style="font-size:12px;color:var(--muted)"></label>
      <input type="number" id="set-pub" value="1" min="0" max="5" style="width:52px;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)"/>
      <button class="btn" id="exp-detail"></button>
    </div>
    <div class="tablescroll" style="max-height:460px">
      <table id="dtbl">
        <thead><tr>
          <th data-dk="rank">#</th><th class="l" data-dk="code"></th>
          <th class="l" data-dk="loai"></th><th class="l" data-dk="issuer"></th>
          <th class="l" data-dk="sector"></th>
          <th data-dk="coupon"></th><th data-dk="maturity"></th><th data-dk="rem"></th>
          <th data-dk="dirty"></th><th data-dk="chg"></th><th data-dk="clean"></th>
          <th data-dk="cy"></th><th data-dk="ytm"></th><th data-dk="mod"></th><th data-dk="mac"></th>
          <th data-dk="rt_b"></th><th data-dk="rt_i"></th><th data-dk="value"></th>
        </tr></thead>
        <tbody id="dtbody"></tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2 id="t-calc-h"></h2>
    <div class="hint" id="t-calc-hint"></div>
    <div class="controls" style="margin-bottom:8px">
      <label id="cl-code" style="font-size:12px;color:var(--muted)"></label>
      <input id="c-code" class="search" style="max-width:220px" placeholder="BAF126003"/>
      <span class="pill" id="c-code-res"></span>
    </div>
    <div class="calcgrid">
      <label id="cl-price"></label><input type="number" id="c-price" step="any"/>
      <label id="cl-face"></label><input type="number" id="c-face" step="any"/>
      <label id="cl-coupon"></label><input type="number" id="c-coupon" step="any"/>
      <label id="cl-freq"></label>
      <select id="c-freq"><option value="1">1</option><option value="2" selected>2</option><option value="4">4</option><option value="12">12</option></select>
      <label id="cl-settle"></label><input type="date" id="c-settle"/>
      <label id="cl-mat"></label><input type="date" id="c-mat"/>
    </div>
    <div class="calcout" id="c-out"></div>
  </div>

  <div class="panel">
    <h2 id="t-trend-h"></h2>
    <div class="hint" id="t-trend-hint"></div>
    <div class="legend">
      <span><span class="dot" style="background:var(--priv)"></span><span data-lg="private"></span></span>
      <span><span class="dot" style="background:var(--pub)"></span><span data-lg="public"></span></span>
    </div>
    <svg id="trend" width="100%" preserveAspectRatio="xMinYMin meet"></svg>
  </div>

  <div class="panel">
    <h2 id="t-tbl-h"></h2>
    <div class="controls">
      <button class="chip active" data-mk2="all"></button>
      <button class="chip" data-mk2="private"></button>
      <button class="chip" data-mk2="public"></button>
      <input class="search" id="q"/>
      <span class="pill" id="count"></span>
      <button class="btn" id="exp-full" style="margin-left:auto"></button>
    </div>
    <div class="tablescroll">
      <table id="tbl">
        <thead><tr>
          <th data-k="rank">#</th><th class="l" data-k="code"></th>
          <th class="l" data-k="market"></th><th class="l" data-k="issuer"></th>
          <th data-k="coupon"></th><th data-k="maturity"></th>
          <th data-k="price"></th><th data-k="volume"></th>
          <th data-k="value"></th><th data-k="pct"></th>
        </tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </div>

  </div><!-- /view-market -->

  <div id="view-rating" hidden>
    <div class="controls" style="margin-bottom:10px">
      <span class="selnote" id="rt-sc-lbl"></span>
      <div class="seg">
        <button data-rs="all" class="active" id="rt-sc-all"></button>
        <button data-rs="nat" id="rt-sc-nat"></button>
        <button data-rs="intl" id="rt-sc-intl"></button>
      </div>
      <span class="hint" id="rt-sc-note" style="margin:0"></span>
    </div>
    <div class="panel" style="border:1px solid var(--accent)">
      <h2 id="rt-chg-h"></h2>
      <div class="hint" id="rt-chg-hint"></div>
      <div class="controls">
        <div class="seg">
          <button data-per="d" class="active" id="rt-per-d"></button>
          <button data-per="w" id="rt-per-w"></button>
          <button data-per="m" id="rt-per-m"></button>
          <button data-per="y" id="rt-per-y"></button>
        </div>
        <span class="selnote" id="rt-chg-win"></span>
      </div>
      <div class="cards" id="rt-chg-cards" style="margin-top:12px"></div>
      <div class="tablescroll" style="max-height:300px;margin-top:8px">
        <table id="rt-chg-tbl"><thead><tr>
          <th data-ck="date"></th><th class="l" data-ck="iss"></th><th class="l" data-ck="code"></th>
          <th data-ck="move"></th><th data-ck="from"></th><th data-ck="to"></th><th data-ck="ag"></th>
        </tr></thead><tbody id="rt-chg-body"></tbody></table>
      </div>
    </div>

    <div class="cards" id="rt-cards"></div>
    <div class="panel">
      <h2 id="rt-dist-h"></h2>
      <div class="hint" id="rt-dist-hint"></div>
      <div class="controls">
        <button class="chip active" data-rd="sector" id="rt-rd-sector"></button>
        <button class="chip" data-rd="grade" id="rt-rd-grade"></button>
        <span style="margin-left:auto"></span>
        <span class="legend" style="margin:0">
          <span><span class="dot" style="background:var(--good)"></span><span id="rt-lg-ig"></span></span>
          <span><span class="dot" style="background:#f59e0b"></span><span id="rt-lg-hy"></span></span>
          <span id="rt-lg-intl-wrap" hidden><span class="dot" style="background:#8b5cf6"></span><span id="rt-lg-intl"></span></span>
        </span>
      </div>
      <div class="barwrap"><svg id="rt-dist" width="100%" preserveAspectRatio="xMinYMin meet"></svg></div>
      <div class="barwrap"><svg id="rt-sec" width="100%" preserveAspectRatio="xMinYMin meet" hidden></svg></div>
    </div>
    <div class="panel">
      <h2 id="rt-recent-h"></h2>
      <div class="hint" id="rt-recent-hint"></div>
      <div class="tablescroll" style="max-height:320px">
        <table id="rt-recent-tbl"><thead><tr>
          <th data-rk="date"></th><th class="l" data-rk="iss"></th><th class="l" data-rk="code"></th>
          <th data-rk="typ"></th><th data-rk="grade"></th><th data-rk="out"></th><th data-rk="ag"></th>
        </tr></thead><tbody id="rt-recent-body"></tbody></table>
      </div>
    </div>
    <div class="panel">
      <h2 id="rt-dir-h"></h2>
      <div class="controls">
        <button class="chip active" data-rf="all"></button>
        <button class="chip" data-rf="bond"></button>
        <button class="chip" data-rf="issuer"></button>
        <select id="rt-ag-sel" style="padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)"></select>
        <input class="search" id="rt-q"/>
        <span class="pill" id="rt-count"></span>
        <button class="btn" id="rt-exp"></button>
      </div>
      <div class="tablescroll" style="max-height:520px">
        <table id="rt-dir-tbl"><thead><tr>
          <th class="l" data-rk="iss"></th><th class="l" data-rk="code"></th><th class="l" data-rk="sec"></th>
          <th data-rk="typ"></th><th data-rk="grade"></th><th data-rk="out"></th><th data-rk="ag"></th><th data-rk="date"></th>
        </tr></thead><tbody id="rt-dir-body"></tbody></table>
      </div>
    </div>

    <div class="panel">
      <h2 id="rt-hist-h"></h2>
      <div class="hint" id="rt-hist-hint"></div>
      <div class="controls">
        <input class="search" id="rt-hist-q" style="max-width:340px"/>
        <span class="pill" id="rt-hist-res"></span>
      </div>
      <div id="rt-hist-out"></div>
    </div>
  </div><!-- /view-rating -->

  <div class="footer" id="foot"></div>
</div>

<script>
const D = %%DATA%%;
const esc = s => (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

const L = {
  vi:{ title:'Thị trường Trái phiếu Doanh nghiệp — HNX',
    sub:'Top 20 theo giá trị giao dịch · gộp thị trường Riêng lẻ & Công chúng',
    private:'Riêng lẻ', public:'Công chúng', all:'Tất cả', updated:'Cập nhật',
    reqNeed:'Cần dữ liệu mới hơn?', reqBtn:'🔄 Cập nhật ngay',
    reqFresh:tm=>'✓ Dữ liệu vừa cập nhật lúc '+tm, reqSent:'✓ Đã gửi yêu cầu cập nhật — dữ liệu mới sẽ có sau vài phút, tải lại trang để xem.',
    reqFail:'Không gửi được yêu cầu. Vui lòng liên hệ trực tiếp:',
    detailH:'🔎 Chi tiết Top 20 (tra cứu mã)',
    detailHint:'Gõ mã để lọc & tự nhận Private/Công chúng. Các cột Clean Price/YTM/Duration/Rating/Danh mục sẽ bổ sung ở bước sau (cần file nội bộ).',
    lookupPh:'Nhập mã trái phiếu... (vd BAF126003)', thLoai:'Loại', thRem:'Còn lại (năm)',
    notTraded:'không giao dịch trong lựa chọn', dloai:m=>m==='private'?'Riêng lẻ':'Công chúng',
    dth:{rank:'#',code:'Mã',loai:'Loại',issuer:'Tổ chức phát hành',sector:'Ngành',coupon:'Coupon',maturity:'Đáo hạn',rem:'Còn lại (năm)',dirty:'Dirty Price',chg:'Δ Clean %',clean:'Clean Price',cy:'Current Yield',ytm:'YTM',mod:'Mod. Dur.',mac:'Mac. Dur.',rt_b:'XH Trái phiếu',rt_i:'XH TCPH',value:'GTGD (tỷ)'},
    setPriv:'Settle Private (T+)', setPub:'Settle Public (T+)', expBtn:'⬇ Excel',
    calcH:'🧮 Máy tính trái phiếu (Clean · YTM · Duration)',
    calcHint:'Nhập mã để tự điền số có sẵn, hoặc tự nhập tay. Clean = Dirty − Lãi dồn tích (Act/365); coupon đều, hoàn vốn ở mệnh giá — tham khảo.',
    clCode:'Mã (tự điền):', clPrice:'Dirty price (VND)', clFace:'Mệnh giá (VND)', clCoupon:'Coupon (%/năm)', clFreq:'Số kỳ trả/năm',
    clSettle:'Ngày định giá (settle)', clMat:'Ngày đáo hạn',
    outRem:'Còn lại (năm)', outAI:'Lãi dồn tích', outClean:'Clean Price', outCY:'Current Yield', outYTM:'YTM', outMac:'Mac. Duration', outMod:'Mod. Duration', outNeed:'Nhập đủ giá, mệnh giá, coupon, ngày settle & đáo hạn.',
    win:'Dữ liệu', modeDay:'Theo ngày', modeRange:'Theo khoảng', latest:'Mới nhất (đủ 2 TT)',
    lbDay:'Ngày:', lbFrom:'Từ:', lbTo:'Đến:', noData:'— Không có dữ liệu trong lựa chọn —',
    selDay:d=>'Phiên <b>'+d+'</b>', selRange:(a,b,n)=>'<b>'+a+'</b> → <b>'+b+'</b> ('+n+' ngày GD)',
    cTotal:'Tổng GTGD', cShare:'Top 20 chiếm', traded:'mã có GD', ofSession:'tổng GTGD',
    bondsVol:'mã · KL', topH:'🏆 Top 20 trái phiếu theo giá trị giao dịch',
    topHint:'Theo lựa chọn ngày/khoảng ở trên. Nhấp chip để lọc; giá trị tính bằng tỷ đồng.',
    trendH:'📈 Giá trị giao dịch theo ngày', trendHint:'Toàn bộ cửa sổ dữ liệu; vùng tô = lựa chọn hiện tại (tỷ đồng/ngày).',
    tblH:'📋 Toàn bộ trái phiếu có giao dịch', search:'Tìm mã hoặc tổ chức phát hành...',
    bonds:'mã', unit:'tỷ', axis:'tỷ đ',
    th:{rank:'#',code:'Mã',market:'TT',issuer:'Tổ chức phát hành',sector:'Ngành',coupon:'Lãi suất',maturity:'Đáo hạn',price:'Giá',volume:'KLGD',value:'GTGD (tỷ)',pct:'% GTGD'},
    tabMarket:'📊 Thị trường', tabRating:'⭐ Xếp hạng tín nhiệm', rtKtcph:'TCPH được xếp hạng', rtKtp:'Trái phiếu được xếp hạng', rtKag:'Số tổ chức XHTN', rtKcov:'Bond GD có xếp hạng', rtDistH:'📊 Phân bố hạng tín nhiệm', rtDistHint:'Số lượng theo hạng (gộp các tổ chức, bỏ tiền tố vn). Xanh = Đầu tư (≥BBB-), Vàng = Đầu cơ.', rtDistHintSec:'Số lượng theo ngành, tách Đầu tư (xanh) / Đầu cơ (vàng). Top 16 ngành theo tổng số bản ghi.', rtIG:'Đầu tư (IG)', rtHY:'Đầu cơ (HY)', rtRecentH:'🕒 Xếp hạng công bố gần đây', rtRecentHint:'20 kết quả mới nhất theo ngày công bố.', rtDirH:'📇 Danh bạ xếp hạng', rtSearch:'Tìm tổ chức phát hành hoặc mã...', rtCount:'kết quả', rtAllAg:'Tất cả tổ chức XHTN', rth:{date:'Ngày',iss:'Tổ chức phát hành',code:'Mã TP',sec:'Ngành',typ:'Loại',grade:'Hạng',out:'Triển vọng',ag:'Tổ chức XHTN'}, typBond:'Trái phiếu', typIssuer:'TCPH', rtAll:'Tất cả', rtBond:'XH Trái phiếu', rtIssuer:'XH TCPH', rtKcovNA:'chỉ tính khi chọn Tất cả thang', rtScLbl:'Thang điểm:', rtScAll:'Tất cả', rtScNat:'Trong nước', rtScIntl:'Quốc tế', rtScNote:'Thang trong nước (Fiin/VIS/SGR/SNI/TMR) và thang quốc tế (Fitch, Moody) KHÔNG so sánh trực tiếp được — cùng ký hiệu nhưng khác nghĩa.', rtLgIntl:'Quốc tế', rtDistMix:' Cột tím = thang quốc tế, tách riêng vì không cộng gộp được với thang trong nước.', 
    rtChgH:'🔔 Biến động xếp hạng', rtChgHint:'Số hành động xếp hạng theo kỳ (tính đến ngày công bố mới nhất). Nâng/Hạ = so với lần công bố trước của cùng tổ chức/mã & cùng đơn vị XHTN.', perD:'Ngày', perW:'Tuần', perM:'Tháng', perY:'Năm', chgWin:'Cửa sổ', chgUp:'⬆ Nâng hạng', chgDown:'⬇ Hạ hạng', chgNew:'🆕 Xếp hạng mới', chgOut:'↺ Đổi triển vọng', chgAff:'= Giữ nguyên', chgWd:'✕ Hết hạn/Kết thúc', cth:{date:'Ngày',iss:'Tổ chức phát hành',code:'Mã TP',move:'Động thái',from:'Hạng cũ',to:'Hạng mới',ag:'Tổ chức XHTN'}, mvUp:'Nâng', mvDown:'Hạ', mvNew:'Mới', mvSame:'Giữ', mvWd:'Kết thúc', mvOut:'Triển vọng', rdGrade:'Theo hạng', rdSector:'Theo ngành', secOther:'Khác / chưa rõ', rtHistH:'🔎 Lịch sử xếp hạng theo tổ chức / mã', rtHistHint:'Nhập tên tổ chức phát hành hoặc mã trái phiếu để xem toàn bộ lịch sử công bố xếp hạng (nâng/hạ/giữ).', rtHistPh:'VD: Vinhomes, KBC, hoặc BAF126003...', rtHistNone:'Không tìm thấy. Thử tên ngắn gọn hơn hoặc mã trái phiếu.', rtHistTip:'Gõ để tìm...', rtHistEnt:'tổ chức/mã', rtHistEv:'lần công bố', 
    foot:'Nguồn: HNX (hnx.vn + hệ thống TPDN riêng lẻ) · Dashboard tự sinh bởi bond_app.py', loc:'vi-VN' },
  en:{ title:'Corporate Bond Market — HNX',
    sub:'Top 20 by trading value · Private placement & Public offering combined',
    private:'Private', public:'Public', all:'All', updated:'Updated',
    reqNeed:'Need fresher data?', reqBtn:'🔄 Update now',
    reqFresh:tm=>'✓ Data just updated at '+tm, reqSent:'✓ Update requested — fresh data in a few minutes, reload to see it.',
    reqFail:'Could not send the request. Please contact directly:',
    detailH:'🔎 Top 20 details (lookup)',
    detailHint:'Type a code to filter & auto-detect Private/Public. Clean Price/YTM/Duration/Rating/Portfolio columns coming next phase (need internal files).',
    lookupPh:'Enter bond code... (e.g. BAF126003)', thLoai:'Type', thRem:'Rem. (yrs)',
    notTraded:'not traded in selection', dloai:m=>m==='private'?'Private':'Public',
    dth:{rank:'#',code:'Code',loai:'Type',issuer:'Issuer',sector:'Sector',coupon:'Coupon',maturity:'Maturity',rem:'Rem. (yrs)',dirty:'Dirty Price',chg:'Δ Clean %',clean:'Clean Price',cy:'Current Yield',ytm:'YTM',mod:'Mod. Dur.',mac:'Mac. Dur.',rt_b:'Bond rating',rt_i:'Issuer rating',value:'Value (bn)'},
    setPriv:'Settle Private (T+)', setPub:'Settle Public (T+)', expBtn:'⬇ Excel',
    calcH:'🧮 Bond calculator (Clean · YTM · Duration)',
    calcHint:'Enter a code to auto-fill, or type your own. Clean = Dirty − Accrued (Act/365); level-coupon, redemption at par — indicative.',
    clCode:'Code (auto-fill):', clPrice:'Dirty price (VND)', clFace:'Face value (VND)', clCoupon:'Coupon (%/yr)', clFreq:'Payments/yr',
    clSettle:'Settlement date', clMat:'Maturity date',
    outRem:'Rem. (yrs)', outAI:'Accrued Int.', outClean:'Clean Price', outCY:'Current Yield', outYTM:'YTM', outMac:'Mac. Duration', outMod:'Mod. Duration', outNeed:'Enter price, face, coupon, settlement & maturity dates.',
    win:'Data', modeDay:'Single day', modeRange:'Date range', latest:'Latest (both mkts)',
    lbDay:'Day:', lbFrom:'From:', lbTo:'To:', noData:'— No data in selection —',
    selDay:d=>'Session <b>'+d+'</b>', selRange:(a,b,n)=>'<b>'+a+'</b> → <b>'+b+'</b> ('+n+' trading days)',
    cTotal:'Total value', cShare:'Top 20 share', traded:'bonds traded', ofSession:'of total value',
    bondsVol:'bonds · vol', topH:'🏆 Top 20 bonds by trading value',
    topHint:'Based on the day/range selected above. Click a chip to filter; value in billion VND.',
    trendH:'📈 Daily trading value', trendHint:'Full data window; shaded = current selection (billion VND/day).',
    tblH:'📋 All traded bonds', search:'Search code or issuer...',
    bonds:'bonds', unit:'bn', axis:'bn',
    th:{rank:'#',code:'Code',market:'Mkt',issuer:'Issuer',sector:'Sector',coupon:'Coupon',maturity:'Maturity',price:'Price',volume:'Volume',value:'Value (bn)',pct:'% value'},
    tabMarket:'📊 Market', tabRating:'⭐ Credit Ratings', rtKtcph:'Issuers rated', rtKtp:'Bonds rated', rtKag:'Rating agencies', rtKcov:'Traded bonds rated', rtDistH:'📊 Rating distribution', rtDistHint:'Count by grade (merged across agencies, vn prefix removed). Green = Investment (≥BBB-), Amber = Speculative.', rtDistHintSec:'Count by sector, split Investment (green) / Speculative (amber). Top 16 sectors by total records.', rtIG:'Investment (IG)', rtHY:'Speculative (HY)', rtRecentH:'🕒 Recent rating actions', rtRecentHint:'20 latest by announcement date.', rtDirH:'📇 Rating directory', rtSearch:'Search issuer or code...', rtCount:'results', rtAllAg:'All agencies', rth:{date:'Date',iss:'Issuer',code:'Bond',sec:'Sector',typ:'Type',grade:'Grade',out:'Outlook',ag:'Agency'}, typBond:'Bond', typIssuer:'Issuer', rtAll:'All', rtBond:'Bond rating', rtIssuer:'Issuer rating', rtKcovNA:'only shown for All scales', rtScLbl:'Rating scale:', rtScAll:'All', rtScNat:'National', rtScIntl:'International', rtScNote:'National scale (Fiin/VIS/SGR/SNI/TMR) and international scale (Fitch, Moody) are NOT directly comparable — same symbols, different meaning.', rtLgIntl:'International', rtDistMix:' Violet bars = international scale, shown separately because the two scales cannot be added together.', 
    rtChgH:'🔔 Rating changes', rtChgHint:'Rating actions per period (up to the latest announcement date). Up/Down = vs the previous announcement for the same issuer/bond & same agency.', perD:'Day', perW:'Week', perM:'Month', perY:'Year', chgWin:'Window', chgUp:'⬆ Upgrades', chgDown:'⬇ Downgrades', chgNew:'🆕 New ratings', chgOut:'↺ Outlook change', chgAff:'= Affirmed', chgWd:'✕ Withdrawn/Ended', cth:{date:'Date',iss:'Issuer',code:'Bond',move:'Action',from:'From',to:'To',ag:'Agency'}, mvUp:'Up', mvDown:'Down', mvNew:'New', mvSame:'Affirm', mvWd:'Ended', mvOut:'Outlook', rdGrade:'By grade', rdSector:'By sector', secOther:'Other / N/A', rtHistH:'🔎 Rating history by issuer / bond', rtHistHint:'Type an issuer name or bond code to see its full rating announcement history (up/down/affirm).', rtHistPh:'e.g. Vinhomes, KBC, or BAF126003...', rtHistNone:'No match. Try a shorter name or a bond code.', rtHistTip:'Type to search...', rtHistEnt:'entities', rtHistEv:'announcements', 
    foot:'Source: HNX (hnx.vn + private-placement bond system) · Dashboard generated by bond_app.py', loc:'en-US' }
};
let lang='vi'; try{ lang=localStorage.getItem('bond_lang')||'vi'; }catch(e){}
const t=()=>L[lang];
const fmtB=v=>(v/1e9).toLocaleString(t().loc,{maximumFractionDigits:2,minimumFractionDigits:2});
const fmtN=v=>Math.round(v).toLocaleString(t().loc);
const mkOf=c=>(D.meta[c]||{}).m||'private';
const mkColor=m=>m==='private'?'var(--priv)':'var(--pub)';
const mkLabel=m=>m==='private'?t().private:t().public;

// ---- bond math (theo lich coupon that; Act/365; hoan von o menh gia) ----
function parsePct(x){const v=parseFloat(String(x==null?'':x).replace('%','').replace(',','.'));return isFinite(v)?v/100:null;}
function yearsBetween(a,b){const d1=new Date(a),d2=new Date(b);if(isNaN(d1)||isNaN(d2))return null;return (d2-d1)/(365.25*864e5);}
function mkUTC(iso){const p=String(iso).slice(0,10).split('-');return new Date(Date.UTC(+p[0],+p[1]-1,+p[2]));}
function addMonthsD(dt,mo){return new Date(Date.UTC(dt.getUTCFullYear(),dt.getUTCMonth()+mo,dt.getUTCDate()));}
function bizAdd(iso,n){ // cong n ngay lam viec (chi tru cuoi tuan)
  let d=mkUTC(iso), k=0; if(n<=0){ while(d.getUTCDay()===0||d.getUTCDay()===6) d=new Date(d.getTime()+864e5); return d; }
  while(k<n){ d=new Date(d.getTime()+864e5); if(d.getUTCDay()!==0&&d.getUTCDay()!==6) k++; } return d;
}
// Tra ve {ai, clean, ytm, cy, mac, mod, lastCoupon}. Neu thieu freq -> uoc luong tu so nam.
function priceAnalytics(dirty,par,cr,freq,settleISO,matISO){
  if(!(dirty>0)||!(par>0)||cr==null||!matISO||!settleISO)return null;
  freq=freq||2; const stepMo=Math.max(1,Math.round(12/freq));
  const settle=mkUTC(settleISO), mat=mkUTC(matISO);
  if(mat<=settle)return null;
  let dates=[], d=new Date(mat);
  while(d>settle){ dates.push(new Date(d)); d=addMonthsD(d,-stepMo); }
  dates.reverse();                          // coupon dates tuong lai (tang dan), cuoi = dao han
  const lastCoupon=new Date(d);             // ky coupon <= settle
  const cpnPer=cr/freq*par, aiDays=(settle-lastCoupon)/864e5;
  const ai=par*cr*aiDays/365, clean=dirty-ai;
  const cfs=dates.map(dt=>({t:(dt-settle)/(365*864e5), cf:cpnPer+(dt.getTime()===mat.getTime()?par:0)}));
  const pvAt=y=>cfs.reduce((s,c)=>s+c.cf/Math.pow(1+y/freq,freq*c.t),0);
  let lo=-0.5,hi=3.0,mid=0.1;
  for(let i=0;i<200;i++){mid=(lo+hi)/2;const f=pvAt(mid)-dirty;if(Math.abs(f)<1)break;if(f>0)lo=mid;else hi=mid;}
  const ytm=mid; let Dm=0,P=0;
  cfs.forEach(c=>{const pv=c.cf/Math.pow(1+ytm/freq,freq*c.t);Dm+=c.t*pv;P+=pv;});
  const mac=P>0?Dm/P:null, mod=mac!=null?mac/(1+ytm/freq):null, cy=cr*par/dirty;
  return {ai,clean,ytm,cy,mac,mod,lastCoupon:lastCoupon.toISOString().slice(0,10)};
}
// settlement mac dinh: private = trading date (T+0), public = ngay lam viec ke (T+1). Cho chinh.
let SETTLE={private:0, public:1};
// suy menh gia (face) cho public (hnx.vn khong cho) tu gia GD ~ gan par
function inferFace(price){
  if(!(price>0)) return 0;
  const cands=[100000,1000000,10000000,100000000,1000000000];
  let best=cands[0], bd=1e9;
  for(const c of cands){ const d=Math.abs(Math.log(price/c)); if(d<bd){bd=d;best=c;} }
  return best;
}

// chuoi gia theo ma (de tinh thay doi Dirty price)
const SERIES={};
for(const r of D.recs){(SERIES[r.c]=SERIES[r.c]||[]).push({d:r.d,p:r.p});}
for(const c in SERIES) SERIES[c].sort((a,b)=>a.d<b.d?-1:1);
function priorRec(code){  // {d,p} cua phien lien truoc lua chon, hoac null
  const s=SERIES[code]; if(!s)return null;
  const ref = sel.mode==='day'?sel.day:sel.from;
  let prev=null; for(const x of s){ if(x.d<ref) prev=x; else break; } return prev||null;
}

// ---- selection state ----
let sel={mode:'day', day:D.default_date, from:D.default_date, to:D.default_date};
let barMk='all', mk2='all', sortK='value', sortDir=-1;
let ROWS=[], KPI=null;

// aggregate recs -> rows for current selection
function computeSelection(){
  let inSel;
  if(sel.mode==='day') inSel=r=>r.d===sel.day;
  else inSel=r=>r.d>=sel.from && r.d<=sel.to;
  const agg={};  // code -> {value,volume, lastD, lastP}
  for(const r of D.recs){ if(!inSel(r)) continue;
    let a=agg[r.c]; if(!a){a={value:0,volume:0,lastD:'',lastP:0};agg[r.c]=a;}
    a.value+=r.val; a.volume+=r.v; if(r.d>=a.lastD){a.lastD=r.d;a.lastP=r.p;} }
  let rows=Object.keys(agg).map(c=>{const a=agg[c],m=mkOf(c),M=D.meta[c]||{};
    return {code:c,market:m,issuer:M.iss||'',coupon:M.cp||'',maturity:M.mt||'',
            price:a.lastP,volume:a.volume,value:a.value};});
  rows.sort((x,y)=>y.value-x.value);
  const tot=rows.reduce((s,x)=>s+x.value,0)||1;
  rows.forEach((x,i)=>{x.rank=i+1; x.pct=Math.round(x.value/tot*10000)/100;});
  ROWS=rows;
  // KPI
  const K={private:{turnover:0,volume:0,traded:0},public:{turnover:0,volume:0,traded:0}};
  rows.forEach(x=>{const k=K[x.market]; k.turnover+=x.value; k.volume+=x.volume; k.traded++;});
  // listed (only meaningful for single day)
  K.private.listed = sel.mode==='day' ? (D.listed.private[sel.day]||0) : null;
  K.public.listed  = sel.mode==='day' ? (D.listed.public[sel.day]||0) : null;
  KPI=K;
}

function applyStatic(){
  const T=t();
  document.getElementById('t-title').textContent=T.title;
  document.getElementById('t-sub').textContent=T.sub;
  document.getElementById('m-day').textContent=T.modeDay;
  document.getElementById('m-range').textContent=T.modeRange;
  document.getElementById('lb-day').textContent=T.lbDay;
  document.getElementById('lb-from').textContent=T.lbFrom;
  document.getElementById('lb-to').textContent=T.lbTo;
  document.getElementById('btn-latest').textContent=T.latest;
  document.getElementById('t-top-h').textContent=T.topH;
  document.getElementById('t-top-hint').textContent=T.topHint;
  document.getElementById('t-trend-h').textContent=T.trendH;
  document.getElementById('t-trend-hint').textContent=T.trendHint;
  document.getElementById('t-tbl-h').textContent=T.tblH;
  document.getElementById('q').placeholder=T.search;
  document.getElementById('foot').textContent=T.foot;
  document.querySelector('[data-mk="all"]').textContent=T.all;
  document.querySelector('[data-mk="private"] span:last-child').textContent=T.private;
  document.querySelector('[data-mk="public"] span:last-child').textContent=T.public;
  document.querySelector('[data-mk2="all"]').textContent=T.all;
  document.querySelector('[data-mk2="private"]').textContent=T.private;
  document.querySelector('[data-mk2="public"]').textContent=T.public;
  document.querySelector('[data-lg="private"]').textContent=T.private;
  document.querySelector('[data-lg="public"]').textContent=T.public;
  document.querySelectorAll('#tbl th').forEach(th=>{th.textContent=T.th[th.dataset.k];});
  document.querySelectorAll('#dtbl th').forEach(th=>{th.textContent=T.dth[th.dataset.dk];});
  document.getElementById('t-detail-h').textContent=T.detailH;
  document.getElementById('t-detail-hint').textContent=T.detailHint;
  document.getElementById('lookup').placeholder=T.lookupPh;
  document.getElementById('t-calc-h').textContent=T.calcH;
  document.getElementById('t-calc-hint').textContent=T.calcHint;
  document.getElementById('cl-code').textContent=T.clCode;
  document.getElementById('cl-price').textContent=T.clPrice;
  document.getElementById('cl-face').textContent=T.clFace;
  document.getElementById('cl-coupon').textContent=T.clCoupon;
  document.getElementById('cl-freq').textContent=T.clFreq;
  document.getElementById('cl-settle').textContent=T.clSettle;
  document.getElementById('cl-mat').textContent=T.clMat;
  document.getElementById('lb-setpriv').textContent=T.setPriv;
  document.getElementById('lb-setpub').textContent=T.setPub;
  document.getElementById('exp-detail').textContent=T.expBtn;
  document.getElementById('exp-full').textContent=T.expBtn;
  // --- rating tab labels ---
  document.getElementById('tab-market-btn').textContent=T.tabMarket;
  document.getElementById('tab-rating-btn').textContent=T.tabRating;
  document.getElementById('rt-dist-h').textContent=T.rtDistH;
  document.getElementById('rt-dist-hint').textContent=T.rtDistHint;
  document.getElementById('rt-lg-ig').textContent=T.rtIG;
  document.getElementById('rt-lg-hy').textContent=T.rtHY;
  document.getElementById('rt-recent-h').textContent=T.rtRecentH;
  document.getElementById('rt-recent-hint').textContent=T.rtRecentHint;
  document.getElementById('rt-dir-h').textContent=T.rtDirH;
  document.getElementById('rt-q').placeholder=T.rtSearch;
  document.getElementById('rt-exp').textContent=T.expBtn;
  document.getElementById('rt-sc-lbl').textContent=T.rtScLbl;
  document.getElementById('rt-sc-all').textContent=T.rtScAll;
  document.getElementById('rt-sc-nat').textContent=T.rtScNat;
  document.getElementById('rt-sc-intl').textContent=T.rtScIntl;
  document.getElementById('rt-sc-note').textContent=T.rtScNote;
  document.getElementById('rt-lg-intl').textContent=T.rtLgIntl;
  document.querySelector('[data-rf="all"]').textContent=T.rtAll;
  document.querySelector('[data-rf="bond"]').textContent=T.rtBond;
  document.querySelector('[data-rf="issuer"]').textContent=T.rtIssuer;
  document.querySelectorAll('#rt-recent-tbl th').forEach(th=>{th.textContent=T.rth[th.dataset.rk];});
  document.querySelectorAll('#rt-dir-tbl th').forEach(th=>{th.textContent=T.rth[th.dataset.rk];});
  document.getElementById('rt-chg-h').textContent=T.rtChgH;
  document.getElementById('rt-chg-hint').textContent=T.rtChgHint;
  document.getElementById('rt-per-d').textContent=T.perD;
  document.getElementById('rt-per-w').textContent=T.perW;
  document.getElementById('rt-per-m').textContent=T.perM;
  document.getElementById('rt-per-y').textContent=T.perY;
  document.getElementById('rt-rd-grade').textContent=T.rdGrade;
  document.getElementById('rt-rd-sector').textContent=T.rdSector;
  document.getElementById('rt-hist-h').textContent=T.rtHistH;
  document.getElementById('rt-hist-hint').textContent=T.rtHistHint;
  document.getElementById('rt-hist-q').placeholder=T.rtHistPh;
  document.querySelectorAll('#rt-chg-tbl th').forEach(th=>{th.textContent=T.cth[th.dataset.ck];});
  { const asel=document.getElementById('rt-ag-sel'); if(asel&&asel.options.length) asel.options[0].textContent=T.rtAllAg; }
  document.querySelectorAll('.lang').forEach(b=>b.classList.toggle('active',b.dataset.lang===lang));
  document.getElementById('asof').innerHTML=`${T.win}: <b>${D.window.min}</b> → <b>${D.window.max}</b><br>${T.updated}: ${D.generated_at}`;
}

function updSelNote(){
  const T=t();
  let n;
  if(sel.mode==='day') n=T.selDay(sel.day);
  else { const days=D.dates.filter(d=>d>=sel.from&&d<=sel.to).length; n=T.selRange(sel.from,sel.to,days); }
  document.getElementById('selnote').innerHTML=n;
}

function drawCards(){
  const T=t(), K=KPI, combined=K.private.turnover+K.public.turnover;
  const lp=K.private.listed!=null?('/'+K.private.listed):'';
  const lpub=K.public.listed!=null?('/'+K.public.listed):'';
  const share=ROWS.slice(0,20).reduce((s,x)=>s+x.pct,0);
  document.getElementById('cards').innerHTML=[
    {lbl:T.cTotal, val:fmtB(combined)+' '+T.unit, foot:(K.private.traded+K.public.traded)+' '+T.traded, c:'var(--good)'},
    {lbl:T.private, val:fmtB(K.private.turnover)+' '+T.unit, foot:K.private.traded+lp+' '+T.bondsVol+' '+fmtN(K.private.volume), c:'var(--priv)'},
    {lbl:T.public, val:fmtB(K.public.turnover)+' '+T.unit, foot:K.public.traded+lpub+' '+T.bondsVol+' '+fmtN(K.public.volume), c:'var(--pub)'},
    {lbl:T.cShare, val:share.toFixed(1)+'%', foot:T.ofSession, c:'var(--accent)'},
  ].map(k=>`<div class="card"><div class="lbl"><span class="dot" style="background:${k.c}"></span>${k.lbl}</div><div class="val">${k.val}</div><div class="foot">${k.foot}</div></div>`).join('');
}

function drawBars(){
  const data=ROWS.filter(x=>barMk==='all'||x.market===barMk).slice(0,20);
  const svg=document.getElementById('bars');
  const W=svg.clientWidth||1100, rowH=26, padL=150, padR=90, padT=8;
  const H=padT+Math.max(data.length,1)*rowH+8;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('height',H);
  if(!data.length){ svg.innerHTML=`<text x="${padL}" y="30" style="fill:var(--muted)">${t().noData}</text>`; return; }
  const max=Math.max(...data.map(x=>x.value),1), bw=W-padL-padR;
  let s='';
  data.forEach((x,i)=>{ const y=padT+i*rowH, w=Math.max(2,x.value/max*bw);
    s+=`<text x="${padL-8}" y="${y+rowH/2+4}" text-anchor="end" font-size="12" style="fill:var(--ink)">${esc(x.code)}</text>`;
    s+=`<rect x="${padL}" y="${y+3}" width="${w}" height="${rowH-8}" rx="3" fill="${mkColor(x.market)}"><title>${esc(x.code)} · ${mkLabel(x.market)} · ${fmtB(x.value)} ${t().unit}</title></rect>`;
    s+=`<text x="${padL+w+6}" y="${y+rowH/2+4}" font-size="12" style="fill:var(--ink)">${fmtB(x.value)}</text>`; });
  svg.innerHTML=s;
}

function drawTrend(){
  const svg=document.getElementById('trend');
  const dates=D.dates;
  const map={}; D.recs.forEach(r=>{const m=mkOf(r.c); (map[r.d]=map[r.d]||{private:0,public:0})[m]+=r.val;});
  const tr=dates.map(d=>({date:d,private:(map[d]||{}).private||0,public:(map[d]||{}).public||0}));
  const W=svg.clientWidth||1100, H=260, padL=54,padR=14,padT=12,padB=42;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('height',H);
  if(!tr.length){svg.innerHTML='';return;}
  const max=Math.max(...tr.map(d=>Math.max(d.private,d.public)),1);
  const iw=W-padL-padR, ih=H-padT-padB;
  const X=i=>padL+(tr.length<=1?0:i/(tr.length-1)*iw);
  const Y=v=>padT+ih-(v/max*ih);
  let s='';
  // selection band
  const inSel=d=> sel.mode==='day' ? d===sel.day : (d>=sel.from&&d<=sel.to);
  let idx=tr.map((p,i)=>inSel(p.date)?i:-1).filter(i=>i>=0);
  if(idx.length){ const x1=X(Math.min(...idx))-3, x2=X(Math.max(...idx))+3;
    s+=`<rect x="${x1}" y="${padT}" width="${Math.max(2,x2-x1)}" height="${ih}" fill="var(--accent)" opacity="0.12"/>`; }
  for(let g=0;g<=4;g++){const yv=max*g/4,y=Y(yv);
    s+=`<line x1="${padL}" y1="${y}" x2="${W-padR}" y2="${y}" stroke="var(--line)"/>`;
    s+=`<text x="${padL-6}" y="${y+4}" text-anchor="end" font-size="11">${(yv/1e9).toFixed(0)}</text>`;}
  const line=(key,col)=>{
    let d=tr.map((p,i)=>`${i?'L':'M'}${X(i).toFixed(1)},${Y(p[key]).toFixed(1)}`).join(' ');
    const area=`M${X(0)},${Y(0)} `+tr.map((p,i)=>`L${X(i).toFixed(1)},${Y(p[key]).toFixed(1)}`).join(' ')+` L${X(tr.length-1)},${Y(0)} Z`;
    return `<path d="${area}" fill="${col}" opacity="0.10"/><path d="${d}" fill="none" stroke="${col}" stroke-width="2"/>`;};
  s+=line('private','var(--priv)'); s+=line('public','var(--pub)');
  const step=Math.ceil(tr.length/8);
  tr.forEach((p,i)=>{ if(i%step===0||i===tr.length-1){
    s+=`<text x="${X(i)}" y="${H-padB+16}" text-anchor="middle" font-size="10">${p.date.slice(5)}</text>`;}});
  s+=`<text x="14" y="${padT+8}" font-size="10">${t().axis}</text>`;
  svg.innerHTML=s;
}

function renderTable(){
  const q=(document.getElementById('q').value||'').toLowerCase();
  let r=ROWS.filter(x=>(mk2==='all'||x.market===mk2) &&
     (!q||x.code.toLowerCase().includes(q)||(x.issuer||'').toLowerCase().includes(q)));
  r=r.slice().sort((a,b)=>{let va=a[sortK],vb=b[sortK];
    if(typeof va==='string')return sortDir*va.localeCompare(vb); return sortDir*(va-vb);});
  document.getElementById('count').textContent=r.length+' '+t().bonds;
  document.getElementById('tbody').innerHTML=r.map(x=>`<tr>
    <td class="rk">${x.rank}</td>
    <td class="l" style="font-weight:600">${esc(x.code)}</td>
    <td class="l"><span class="badge ${x.market==='private'?'b-priv':'b-pub'}">${mkLabel(x.market)}</span></td>
    <td class="l issuer">${esc(x.issuer)||'—'}</td>
    <td class="mono">${x.coupon?esc(x.coupon)+'%':'—'}</td>
    <td class="mono">${esc(x.maturity)||'—'}</td>
    <td class="mono">${x.price?fmtN(x.price):'—'}</td>
    <td class="mono">${fmtN(x.volume)}</td>
    <td class="mono" style="font-weight:600">${fmtB(x.value)}</td>
    <td class="mono">${x.pct}%</td></tr>`).join('');
}

// ---- detail table (Top 20 + tra cuu) : Dirty, thay doi, Clean, YTM, duration, rating ----
function detailAnalytics(x){
  const M=D.meta[x.code]||{};
  const asof = sel.mode==='day'?sel.day:sel.to;
  const rem = M.mt? yearsBetween(asof,M.mt): null;
  const settleISO = M.mt? bizAdd(asof, SETTLE[x.market]||0).toISOString().slice(0,10) : null;
  const par = (+M.par>0)? +M.par : inferFace(x.price);
  const cr = parsePct(M.cp);
  const a = (M.mt && settleISO)? priceAnalytics(x.price, par, cr, M.frq, settleISO, M.mt) : null;
  // Δ CLEAN %: gia sach phien nay vs phien truoc (fallback dirty% neu chua tinh duoc clean)
  const prev = priorRec(x.code);
  let chg = null;
  if(prev && prev.p>0 && x.price){
    if(a && M.mt){
      const ps = bizAdd(prev.d, SETTLE[x.market]||0).toISOString().slice(0,10);
      const pa = priceAnalytics(prev.p, par, cr, M.frq, ps, M.mt);
      chg = (pa && pa.clean>0) ? (a.clean-pa.clean)/pa.clean*100 : (x.price-prev.p)/prev.p*100;
    } else { chg = (x.price-prev.p)/prev.p*100; }
  }
  return {rem,a,chg,
          rtB:M.rt_b||'', rtBag:M.rt_b_ag||'', rtBout:M.rt_b_out||'',
          rtI:M.rt_i||'', rtIag:M.rt_i_ag||'', rtIout:M.rt_i_out||'',
          sector:(M.sector||'').replace(/^\d+\s*-\s*/,'')};
}
function rateCell(r,ag,out){
  if(!r) return '<td class="mono">—</td>';
  return `<td class="mono" title="${esc(ag)}${out?' · '+esc(out):''}"><b>${esc(r)}</b>${ag?' <span style="color:var(--muted);font-size:11px">'+esc(ag)+'</span>':''}</td>`;
}
function renderDetail(){
  const T=t();
  const q=(document.getElementById('lookup').value||'').trim().toLowerCase();
  const base = q? ROWS.filter(x=>x.code.toLowerCase().includes(q)||(x.issuer||'').toLowerCase().includes(q)) : ROWS.slice(0,20);
  const rl=document.getElementById('lookup-res');
  if(q){ const hit=Object.keys(D.meta).filter(c=>c.toLowerCase().includes(q));
    rl.innerHTML = hit.length? hit.slice(0,4).map(c=>`${esc(c)}: <b>${T.dloai(D.meta[c].m)}</b>`).join(' · ') : '—'; }
  else rl.textContent='Top 20';
  document.getElementById('dtbody').innerHTML=base.map(x=>{
    const dd=detailAnalytics(x), a=dd.a;
    const chgTxt=dd.chg==null?'—':(dd.chg>0?'+':'')+dd.chg.toFixed(2)+'%';
    const chgCol=dd.chg==null?'':(dd.chg>0?'color:var(--good)':(dd.chg<0?'color:#c0392b':''));
    return `<tr>
      <td class="rk">${x.rank}</td>
      <td class="l" style="font-weight:600">${esc(x.code)}</td>
      <td class="l"><span class="badge ${x.market==='private'?'b-priv':'b-pub'}">${mkLabel(x.market)}</span></td>
      <td class="l issuer">${esc(x.issuer)||'—'}</td>
      <td class="l issuer">${esc(dd.sector)||'—'}</td>
      <td class="mono">${x.coupon?esc(String(x.coupon))+'%':'—'}</td>
      <td class="mono">${esc(x.maturity)||'—'}</td>
      <td class="mono">${dd.rem!=null?dd.rem.toFixed(2):'—'}</td>
      <td class="mono">${x.price?fmtN(x.price):'—'}</td>
      <td class="mono" style="${chgCol}">${chgTxt}</td>
      <td class="mono">${a?fmtN(a.clean):'—'}</td>
      <td class="mono">${a?(a.cy*100).toFixed(2)+'%':'—'}</td>
      <td class="mono">${a?(a.ytm*100).toFixed(2)+'%':'—'}</td>
      <td class="mono">${a&&a.mod!=null?a.mod.toFixed(2):'—'}</td>
      <td class="mono">${a&&a.mac!=null?a.mac.toFixed(2):'—'}</td>
      ${rateCell(dd.rtB,dd.rtBag,dd.rtBout)}
      ${rateCell(dd.rtI,dd.rtIag,dd.rtIout)}
      <td class="mono" style="font-weight:600">${fmtB(x.value)}</td></tr>`;
  }).join('');
}

// ---- standalone bond calculator (auto-fill theo ma neu co) ----
function calcFill(){
  const code=(document.getElementById('c-code').value||'').trim().toUpperCase();
  const M=D.meta[code]; const res=document.getElementById('c-code-res');
  if(!M){ res.textContent = code? '—' : ''; return; }
  res.innerHTML=`<b>${t().dloai(M.m)}</b>`;
  const s=SERIES[code]; const last=s&&s.length?s[s.length-1]:null;
  const face=(+M.par>0)?+M.par:(last?inferFace(last.p):0);
  if(face) document.getElementById('c-face').value=face;
  if(M.cp) document.getElementById('c-coupon').value=parseFloat(String(M.cp).replace('%',''));
  if(M.frq) document.getElementById('c-freq').value=String(Math.round(M.frq));
  if(M.mt) document.getElementById('c-mat').value=M.mt;
  if(last){ document.getElementById('c-price').value=last.p;
    document.getElementById('c-settle').value=bizAdd(last.d, SETTLE[M.m]||0).toISOString().slice(0,10); }
  runCalc();
}
function runCalc(){
  const T=t();
  const price=parseFloat(document.getElementById('c-price').value);
  const face=parseFloat(document.getElementById('c-face').value);
  const cr=parsePct(document.getElementById('c-coupon').value);
  const freq=parseInt(document.getElementById('c-freq').value)||2;
  const settle=document.getElementById('c-settle').value, mat=document.getElementById('c-mat').value;
  const out=document.getElementById('c-out'), a=priceAnalytics(price,face,cr,freq,settle,mat);
  if(!a){ out.innerHTML=`<div class="kpi" style="min-width:auto"><div class="k">${T.outNeed}</div></div>`; return; }
  const yrs=yearsBetween(settle,mat);
  const kpi=(k,v)=>`<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  out.innerHTML=kpi(T.outRem,yrs.toFixed(2))+kpi(T.outAI,fmtN(a.ai))+kpi(T.outClean,fmtN(a.clean))
    +kpi(T.outCY,(a.cy*100).toFixed(2)+'%')+kpi(T.outYTM,(a.ytm*100).toFixed(2)+'%')
    +kpi(T.outMac,a.mac.toFixed(2))+kpi(T.outMod,a.mod.toFixed(2));
}

// ---- xuat Excel (CSV UTF-8) tu 1 bang ----
function exportTable(tableId, fname){
  const tb=document.getElementById(tableId);
  const rows=[...tb.querySelectorAll('tr')].map(tr=>[...tr.querySelectorAll('th,td')]
    .map(td=>{let s=td.textContent.trim().replace(/ /g,' '); return /[",\n;]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}).join(','));
  const csv='﻿'+rows.join('\r\n');
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download=fname+'_'+(sel.mode==='day'?sel.day:(sel.from+'_'+sel.to))+'.csv';
  document.body.appendChild(a); a.click(); a.remove();
}

// ---- request-update button (khong backend: dua tren do moi cua data + cooldown may) ----
const REQ_COOL=(D.cooldown_min||30)*60*1000;
function reqState(){
  const age=Date.now()-(D.generated_ms||0);
  const myLast=+(localStorage.getItem('bond_req')||0);
  const dataFresh=age<REQ_COOL, iReq=(Date.now()-myLast)<REQ_COOL;
  return {dataFresh,iReq,enabled:!dataFresh&&!iReq};
}
function drawReq(){
  const T=t(), st=reqState();
  const btn=document.getElementById('req-btn'), msg=document.getElementById('req-msg');
  document.getElementById('req-contact').innerHTML=`📩 ${T.reqNeed}`;
  btn.textContent=T.reqBtn; btn.disabled=!st.enabled; btn.classList.toggle('on',st.enabled);
  if(st.dataFresh){ const tm=new Date(D.generated_ms).toLocaleTimeString(T.loc,{hour:'2-digit',minute:'2-digit'});
    msg.innerHTML=T.reqFresh(tm); }
  else if(st.iReq){
    const failed=localStorage.getItem('bond_req_failed')==='1';
    msg.innerHTML=failed?`${T.reqFail} <b>${esc(D.contact||'')}</b>`:T.reqSent;
  }
  else { msg.innerHTML=''; }
}
const TRIGGER_URL='https://bond-dashboard-trigger.trigger-worker.workers.dev';
document.getElementById('req-btn').onclick=()=>{
  if(!reqState().enabled) return;
  try{ localStorage.setItem('bond_req', String(Date.now())); localStorage.removeItem('bond_req_failed'); }catch(e){}
  drawReq();
  fetch(TRIGGER_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({site:'bond-dashboard'})})
    .then(r=>r.json()).then(r=>{ if(!r.ok){ try{localStorage.setItem('bond_req_failed','1');}catch(e){} drawReq(); } })
    .catch(()=>{ try{localStorage.setItem('bond_req_failed','1');}catch(e){} drawReq(); });
};
setInterval(drawReq, 30000);

// ================= CREDIT RATING TAB =================
const RR = D.rating_records||[];
let rtView=false, rtFilter='all', rtAgency='', rtQ='', rtSortK='date', rtSortDir=-1;
// Thang diem: 'all' | 'nat' (trong nuoc) | 'intl' (Fitch/Moody). Loc CA TAB.
let rtScale='all';
function scOK(x){ return rtScale==='all' || (x.sc||'nat')===rtScale; }
function RRS(){ return RR.filter(scOK); }
function EVS(){ return EV.filter(scOK); }

const SP_TOKENS=['AAA','AA+','AA-','AA','A+','A-','A','BBB+','BBB-','BBB','BB+','BB-','BB','B+','B-','B','CCC+','CCC-','CCC','CC','SD','D','C'];
const MOODY={AAA:'AAA',AA1:'AA+',AA2:'AA',AA3:'AA-',A1:'A+',A2:'A',A3:'A-',
  BAA1:'BBB+',BAA2:'BBB',BAA3:'BBB-',BA1:'BB+',BA2:'BB',BA3:'BB-',
  B1:'B+',B2:'B',B3:'B-',CAA1:'CCC+',CAA2:'CCC',CAA3:'CCC-',CA:'CC',C:'C'};
const SP_SET=new Set(SP_TOKENS);
function gradeBase(g){                        // bo tien to 'vn' + map Moody -> chuan S&P (KHOP CHINH XAC)
  let s=String(g||'').trim().replace(/^vn/i,'').replace(/[\s()]/g,'').toUpperCase();
  if(!s) return '';
  if(MOODY[s]) return MOODY[s];               // Aaa/Baa3/Ba2... -> S&P
  return SP_SET.has(s) ? s : '';              // chi nhan hang hop le; loai "Bảo mật","WR","N/A"...
}
const GRANK={'AAA':1,'AA+':2,'AA':3,'AA-':4,'A+':5,'A':6,'A-':7,'BBB+':8,'BBB':9,'BBB-':10,
  'BB+':11,'BB':12,'BB-':13,'B+':14,'B':15,'B-':16,'CCC+':17,'CCC':18,'CCC-':19,'CC':20,'C':21,'D':22};
function gradeRank(g){ return GRANK[gradeBase(g)]||99; }
function isIG(g){ return gradeRank(g)<=10; }   // >= BBB- = dau tu
function gradeColor(g){ const r=gradeRank(g); return r<=10?'var(--good)':(r<=16?'#f59e0b':'#e11d48'); }
function parseRD(d){                          // dd/mm/yyyy | yyyy-mm-dd -> 'yyyymmdd' sortable
  const s=String(d||'').trim();
  let m=s.match(/^(\d{2})\/(\d{2})\/(\d{4})/); if(m) return m[3]+m[2]+m[1];
  m=s.match(/^(\d{4})-(\d{2})-(\d{2})/); if(m) return m[1]+m[2]+m[3];
  return '';
}
function gchip(g){ if(!g) return ''; const c=gradeColor(g);
  return `<span class="gradechip" style="background:${c}1a;color:${c};border:1px solid ${c}66">${esc(g)}</span>`; }
// dich data VN -> EN (chi khi lang=en; ten cong ty giu nguyen vi la danh tu rieng)
const TR_OUT={'Ổn định':'Stable','Bảo mật':'Confidential','Không thuận lợi':'Negative','Thuận lợi':'Positive',
  'Tích cực':'Positive','Tiêu cực':'Negative','Theo dõi ngắn hạn XHTN: Không thuận lợi':'Short-term watch: Negative'};
const TR_STATUS={'Xếp hạng lần đầu':'Initial rating','XHTN Lần đầu':'Initial rating','Lần đầu':'Initial rating',
  'Cập nhật kết quả XHTN':'Rating update','Cập nhật':'Update','Giám sát Xếp hạng':'Surveillance','Giám sát theo dõi':'Surveillance',
  'Giám sát Xếp hạng (hết hạn)':'Surveillance (expired)','Giám sát Xếp hạng (Hết hạn)':'Surveillance (expired)',
  'Xếp hạng lần đầu (hết hạn)':'Initial rating (expired)','Xếp hạng lần đầu (Hết hạn)':'Initial rating (expired)',
  'Kết thúc':'Ended','Gia hạn Giám sát Xếp hạng':'Surveillance renewed','Nâng bậc':'Upgrade','Thay đổi triển vọng':'Outlook change',
  'Xếp hạng lần đầu (Cập nhật thông tin)':'Initial rating (info update)','Không tiếp tục thực hiện Xếp hạng lần đầu':'Initial rating discontinued'};
const TR_SEC={'Phát triển Bất động sản':'Real estate development','Bất động sản':'Real estate','Dịch vụ tài chính':'Financial services',
  'Tổ chức tín dụng':'Credit institution','Ngân hàng':'Banking','Kinh doanh chứng khoán':'Securities','Cho thuê Bất động sản':'Real estate leasing',
  'Điện':'Power','Nước và tiện ích':'Water & utilities','Sản xuất':'Manufacturing','Hạ tầng vận tải':'Transport infrastructure',
  'Chứng khoán':'Securities','Bao bì':'Packaging','Đa ngành':'Conglomerate','Xây dựng':'Construction','Lĩnh vực khác':'Other',
  'Hạ tầng kỹ thuật':'Technical infrastructure','Bảo hiểm nhân thọ':'Life insurance','Thương mại, dịch vụ':'Trade & services',
  'Dịch vụ - Phát triển Bất động sản':'Services - Real estate dev.','Tổng công ty đầu tư':'Investment corporation',
  'Bảo hiểm phi nhân thọ':'Non-life insurance','Sản xuất đường':'Sugar production','Hàng không':'Aviation','Nhựa':'Plastics',
  'Sản xuất ôtô':'Automobile','Dịch vụ lưu trú':'Hospitality','Dịch vụ mua bán nợ':'Debt trading','Sản xuất điện':'Power generation',
  'Công nghệ thông tin':'Information technology','Tổ chức tài chính':'Financial institution'};
function trv(map,v){ return (lang==='en'&&map[v])?map[v]:(v||''); }

function rtCell(r,k){ const T=t();
  if(k==='grade') return `<td>${gchip(r.grade)}</td>`;
  if(k==='typ')   return `<td>${r.typ==='bond'?T.typBond:T.typIssuer}</td>`;
  const l=(k==='iss'||k==='code'||k==='sec')?' class="l"':'';
  let v=r[k]||''; if(k==='out') v=trv(TR_OUT,v); if(k==='sec') v=trv(TR_SEC,v);
  return `<td${l}>${esc(v)}</td>`;
}
function rtRow(r,keys){ return '<tr>'+keys.map(k=>rtCell(r,k)).join('')+'</tr>'; }

function drawRatingCards(){ const T=t();
  const issRated=new Set(), bondRated=new Set(), ags=new Set();
  RRS().forEach(r=>{ if(r.ag) ags.add(r.ag);
    if(r.typ==='bond'&&r.code) bondRated.add(r.code);
    else if(r.typ==='issuer'&&r.iss) issRated.add(r.iss.toLowerCase()); });
  let traded=0, tradedRated=0;
  for(const c in D.meta){ traded++; const M=D.meta[c]; if(M.rt_b||M.rt_i) tradedRated++; }
  const cov=traded?Math.round(tradedRated/traded*100):0;
  document.getElementById('rt-cards').innerHTML=[
    {lbl:T.rtKtcph,val:fmtN(issRated.size),foot:'',c:'var(--accent)'},
    {lbl:T.rtKtp,val:fmtN(bondRated.size),foot:'',c:'var(--priv)'},
    {lbl:T.rtKag,val:fmtN(ags.size),foot:[...ags].filter(Boolean).sort().join(' · '),c:'var(--good)'},
    (rtScale==='all'
       ? {lbl:T.rtKcov,val:cov+'%',foot:tradedRated+'/'+traded,c:'var(--pub)'}
       : {lbl:T.rtKcov,val:'—',foot:T.rtKcovNA,c:'var(--pub)'}),
  ].map(k=>`<div class="card"><div class="lbl"><span class="dot" style="background:${k.c}"></span>${k.lbl}</div><div class="val">${k.val}</div><div class="foot">${esc(k.foot)}</div></div>`).join('');
}

function drawRatingDist(){ const T=t(), nat={}, intl={};
  // Tach 2 thang: khong duoc cong gop 'BB+' quoc te vao 'BB+' trong nuoc.
  RRS().forEach(r=>{ const b=gradeBase(r.grade); if(!b) return;
    const bag=(r.sc==='intl')?intl:nat; bag[b]=(bag[b]||0)+1; });
  const grades=[...new Set([...Object.keys(nat),...Object.keys(intl)])]
    .sort((a,b)=>(GRANK[a]||99)-(GRANK[b]||99));
  const coIntl=Object.keys(intl).length>0;
  const lgw=document.getElementById('rt-lg-intl-wrap'); if(lgw) lgw.hidden=!coIntl;
  const svg=document.getElementById('rt-dist');
  const W=svg.clientWidth||1080, rowH=26, padL=72, padR=64, padT=8;
  const H=padT+Math.max(grades.length,1)*rowH+8;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('height',H);
  if(!grades.length){ svg.innerHTML=`<text x="${padL}" y="30" style="fill:var(--muted)">${T.noData}</text>`; return; }
  const tot=g=>(nat[g]||0)+(intl[g]||0);
  const max=Math.max(...grades.map(tot),1), bw=W-padL-padR; let s='';
  grades.forEach((g,i)=>{ const y=padT+i*rowH, nv=nat[g]||0, xv=intl[g]||0;
    const wn=nv/max*bw, wx=xv/max*bw, col=isIG(g)?'var(--good)':'#f59e0b';
    s+=`<text x="${padL-10}" y="${y+17}" text-anchor="end" style="fill:var(--ink);font-weight:600;font-size:12px">${g}</text>`;
    if(nv) s+=`<rect x="${padL}" y="${y+4}" width="${Math.max(2,wn)}" height="${rowH-10}" rx="3" fill="${col}"><title>${g} - ${T.rtScNat}: ${nv}</title></rect>`;
    if(xv) s+=`<rect x="${padL+wn+(nv?2:0)}" y="${y+4}" width="${Math.max(2,wx)}" height="${rowH-10}" rx="3" fill="#8b5cf6"><title>${g} - ${T.rtScIntl}: ${xv}</title></rect>`;
    const lbl=(nv&&xv)?`${nv}+${xv}`:String(nv||xv);
    s+=`<text x="${padL+wn+wx+8}" y="${y+17}" style="fill:var(--muted);font-size:12px">${lbl}</text>`; });
  svg.innerHTML=s;
}

function renderRecent(){ const T=t();
  const rows=RRS().filter(r=>parseRD(r.date)).sort((a,b)=>parseRD(a.date)<parseRD(b.date)?1:-1).slice(0,20);
  document.getElementById('rt-recent-body').innerHTML=
    rows.map(r=>rtRow(r,['date','iss','code','typ','grade','out','ag'])).join('')
    || `<tr><td colspan="7" style="text-align:center;color:var(--muted)">${T.noData}</td></tr>`;
}

function fillAgencySel(){ const sel=document.getElementById('rt-ag-sel');
  const ags=[...new Set(RRS().map(r=>r.ag).filter(Boolean))].sort();
  sel.innerHTML=`<option value="">${t().rtAllAg}</option>`+ags.map(a=>`<option value="${esc(a)}"${a===rtAgency?' selected':''}>${esc(a)}</option>`).join('');
}

function renderDir(){ const T=t(), q=rtQ.trim().toLowerCase();
  let rows=RRS().filter(r=>{
    if(rtFilter!=='all'&&r.typ!==rtFilter) return false;
    if(rtAgency&&r.ag!==rtAgency) return false;
    if(q&&!((r.iss||'').toLowerCase().includes(q)||(r.code||'').toLowerCase().includes(q))) return false;
    return true; });
  rows.sort((a,b)=>{ let av,bv;
    if(rtSortK==='grade'){av=gradeRank(a.grade);bv=gradeRank(b.grade);}
    else if(rtSortK==='date'){av=parseRD(a.date);bv=parseRD(b.date);}
    else {av=(a[rtSortK]||'').toLowerCase();bv=(b[rtSortK]||'').toLowerCase();}
    return (av<bv?-1:av>bv?1:0)*rtSortDir; });
  document.getElementById('rt-count').textContent=rows.length+' '+T.rtCount;
  document.getElementById('rt-dir-body').innerHTML=
    rows.slice(0,600).map(r=>rtRow(r,['iss','code','sec','typ','grade','out','ag','date'])).join('')
    || `<tr><td colspan="8" style="text-align:center;color:var(--muted)">${T.noData}</td></tr>`;
}

// ---- rating events -> timelines + nang/ha detection ----
const EV = D.rating_events||[];
let rtDistMode='sector', rtPer='d';   // main = theo nganh; phu = theo hang
EV.forEach(e=>{ e.dord=parseRD(e.date); e.gb=gradeBase(e.grade); });
function isEnd(st){ return /h[ếe]t h[ạa]n|k[ếe]t th[úu]c|kh[ôo]ng ti[ếe]p t[ụu]c/i.test(st||''); }
function isOutlook(st){ return /tri[ểe]n v[ọo]ng/i.test(st||''); }
const TL={};
EV.forEach(e=>{ const k=e.ag+'|'+e.typ+'|'+(e.code||(e.iss||'').toLowerCase().trim()); (TL[k]=TL[k]||[]).push(e); });
for(const k in TL){ const a=TL[k]; a.sort((x,y)=>x.dord<y.dord?-1:x.dord>y.dord?1:0); let prev=null;
  a.forEach(e=>{
    if(isEnd(e.status)){ e.move='wd'; e.from=prev?prev.gb:''; }
    else if(!prev || !prev.gb){ e.move=e.gb?'new':'same'; e.from=''; }   // truoc do chua co hang hop le -> "moi"
    else if(!e.gb){ e.move='same'; e.from=prev.gb; }                     // ky nay hang khong hop le (vd Bảo mật)
    else { const pr=gradeRank(prev.gb), cr=gradeRank(e.gb);
      e.move = cr<pr?'up' : cr>pr?'down' : (isOutlook(e.status)?'out':'same'); e.from=prev.gb; }
    if(!isEnd(e.status)) prev=e;
  });
}
const EV_MAX=EV.reduce((m,e)=>e.dord>m?e.dord:m,'');
function shiftDays(ymd,days){ if(!ymd) return '';
  const dt=new Date(Date.UTC(+ymd.slice(0,4),+ymd.slice(4,6)-1,+ymd.slice(6,8))); dt.setUTCDate(dt.getUTCDate()-days);
  return dt.toISOString().slice(0,10).replace(/-/g,''); }
function fmtRD(d){ return d?d.slice(6,8)+'/'+d.slice(4,6)+'/'+d.slice(0,4):''; }
function moveBadge(mv){ const T=t();
  const M=({up:['var(--good)',T.mvUp+' ▲'],down:['#e11d48',T.mvDown+' ▼'],new:['var(--accent)',T.mvNew],
    same:['var(--muted)',T.mvSame],out:['#f59e0b',T.mvOut],wd:['var(--muted)',T.mvWd+' ✕']})[mv]||['var(--muted)',''];
  return `<span class="gradechip" style="background:${M[0]}1a;color:${M[0]};border:1px solid ${M[0]}66">${M[1]}</span>`; }

function drawChanges(){ const T=t();
  const start=shiftDays(EV_MAX,{d:0,w:6,m:29,y:364}[rtPer]);
  const rows=EVS().filter(e=>e.dord && e.dord>=start && e.dord<=EV_MAX);
  const c={up:0,down:0,new:0,out:0,same:0,wd:0}; rows.forEach(e=>{ c[e.move]=(c[e.move]||0)+1; });
  document.getElementById('rt-chg-win').innerHTML=`${T.chgWin}: <b>${fmtRD(start)}</b> → <b>${fmtRD(EV_MAX)}</b>`;
  document.getElementById('rt-chg-cards').innerHTML=[
    {lbl:T.chgUp,val:c.up,c:'var(--good)'},{lbl:T.chgDown,val:c.down,c:'#e11d48'},
    {lbl:T.chgNew,val:c.new,c:'var(--accent)'},{lbl:T.chgOut,val:c.out,c:'#f59e0b'},
    {lbl:T.chgAff,val:c.same,c:'var(--muted)'},{lbl:T.chgWd,val:c.wd,c:'var(--muted)'},
  ].map(k=>`<div class="card"><div class="lbl"><span class="dot" style="background:${k.c}"></span>${k.lbl}</div><div class="val">${k.val}</div></div>`).join('');
  const order={up:0,down:0,out:0,new:1};   // nang/ha/doi trien vong len tren, xep hang moi ben duoi
  const list=rows.filter(e=>e.move in order)
    .sort((a,b)=>(order[a.move]-order[b.move]) || (a.dord<b.dord?1:a.dord>b.dord?-1:0)).slice(0,80);
  document.getElementById('rt-chg-body').innerHTML=list.map(e=>
    `<tr><td>${fmtRD(e.dord)}</td><td class="l">${esc(e.iss)}</td><td class="l">${esc(e.code)}</td>`
    +`<td>${moveBadge(e.move)}</td><td>${e.from?gchip(e.from):'—'}</td><td>${gchip(e.gb||e.grade)}</td><td>${esc(e.ag)}</td></tr>`).join('')
    || `<tr><td colspan="7" style="text-align:center;color:var(--muted)">${T.noData}</td></tr>`;
}

function drawSectorDist(){ const T=t(), IG={}, HY={};
  RRS().forEach(r=>{ const b=gradeBase(r.grade); if(!b) return; const sec=(r.sec||'').trim()||T.secOther;
    const bag=isIG(b)?IG:HY; bag[sec]=(bag[sec]||0)+1; });
  const secs=[...new Set([...Object.keys(IG),...Object.keys(HY)])]
    .sort((a,b)=>((IG[b]||0)+(HY[b]||0))-((IG[a]||0)+(HY[a]||0))).slice(0,16);
  const svg=document.getElementById('rt-sec');
  const W=svg.clientWidth||1080, rowH=28, padL=200, padR=46, padT=8, H=padT+Math.max(secs.length,1)*rowH+8;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('height',H);
  if(!secs.length){ svg.innerHTML=`<text x="${padL}" y="30" style="fill:var(--muted)">${T.noData}</text>`; return; }
  const max=Math.max(...secs.map(s=>(IG[s]||0)+(HY[s]||0)),1), bw=W-padL-padR; let out='';
  secs.forEach((s,i)=>{ const y=padT+i*rowH, ig=IG[s]||0, hy=HY[s]||0, wig=ig/max*bw, why=hy/max*bw;
    const sn=trv(TR_SEC,s), lbl=sn.length>28?sn.slice(0,27)+'…':sn;
    out+=`<text x="${padL-10}" y="${y+18}" text-anchor="end" style="fill:var(--ink);font-size:12px">${esc(lbl)}</text>`;
    out+=`<rect x="${padL}" y="${y+5}" width="${Math.max(0,wig)}" height="${rowH-12}" rx="3" fill="var(--good)"></rect>`;
    out+=`<rect x="${padL+wig}" y="${y+5}" width="${Math.max(0,why)}" height="${rowH-12}" rx="3" fill="#f59e0b"></rect>`;
    out+=`<text x="${padL+wig+why+6}" y="${y+18}" style="fill:var(--muted);font-size:12px">${ig+hy}</text>`; });
  svg.innerHTML=out;
}
function setDistMode(){ const g=rtDistMode==='grade';
  document.getElementById('rt-dist').hidden=!g; document.getElementById('rt-sec').hidden=g;
  document.querySelectorAll('[data-rd]').forEach(b=>b.classList.toggle('active',b.dataset.rd===rtDistMode));
  const Tm=t(); const coIntl=RRS().some(r=>r.sc==='intl');
  document.getElementById('rt-dist-hint').textContent =
    (g?Tm.rtDistHint:Tm.rtDistHintSec) + ((g&&coIntl)?Tm.rtDistMix:'');
  g?drawRatingDist():drawSectorDist();
}

function renderHist(){ const T=t(), q=document.getElementById('rt-hist-q').value.trim().toLowerCase();
  const out=document.getElementById('rt-hist-out'), res=document.getElementById('rt-hist-res');
  if(!q){ out.innerHTML=`<div class="hint">${T.rtHistTip}</div>`; res.textContent=''; return; }
  const hit=EVS().filter(e=>(e.iss||'').toLowerCase().includes(q)||(e.code||'').toLowerCase().includes(q));
  if(!hit.length){ out.innerHTML=`<div class="hint">${T.rtHistNone}</div>`; res.textContent='0'; return; }
  const byEnt={}; hit.forEach(e=>{ const k=e.ag+'|'+e.typ+'|'+(e.code||(e.iss||'').toLowerCase().trim()); (byEnt[k]=byEnt[k]||[]).push(e); });
  const keys=Object.keys(byEnt).sort((a,b)=>byEnt[b].length-byEnt[a].length).slice(0,12);
  res.textContent=Object.keys(byEnt).length+' '+T.rtHistEnt+' · '+hit.length+' '+T.rtHistEv;
  const statusH=lang==='vi'?'Trạng thái':'Status';
  out.innerHTML=keys.map(k=>{ const arr=byEnt[k].slice().sort((a,b)=>a.dord<b.dord?1:-1), h=arr[0];
    const title=(h.code?esc(h.code)+' · ':'')+esc(h.iss)+' <span style="color:var(--muted);font-weight:400">('+esc(h.ag)+' · '+(h.typ==='bond'?T.typBond:T.typIssuer)+(h.sec?' · '+esc(h.sec):'')+')</span>';
    const rows=arr.map(e=>`<tr><td>${fmtRD(e.dord)}</td><td>${moveBadge(e.move)}</td><td>${e.from?gchip(e.from):'—'}</td><td>${gchip(e.gb||e.grade)}</td><td class="l">${esc(trv(TR_OUT,e.out))}</td><td class="l" style="color:var(--muted)">${esc(trv(TR_STATUS,e.status))}</td></tr>`).join('');
    return `<div style="margin:14px 0 4px;font-weight:700">${title}</div><div class="tablescroll"><table><thead><tr>`
      +`<th>${T.cth.date}</th><th>${T.cth.move}</th><th>${T.cth.from}</th><th>${T.cth.to}</th><th class="l">${T.rth.out}</th><th class="l">${statusH}</th>`
      +`</tr></thead><tbody>${rows}</tbody></table></div>`;
  }).join('');
}

function drawRating(){ drawRatingCards(); setDistMode(); drawChanges(); renderRecent(); fillAgencySel(); renderDir(); renderHist(); }

function refresh(){ computeSelection(); updSelNote(); drawCards(); drawBars(); drawTrend(); renderTable(); renderDetail(); }
function drawAll(){ applyStatic(); drawReq(); refresh(); runCalc(); drawRating(); }

// ---- wire date controls ----
function setModeUI(){
  document.getElementById('m-day').classList.toggle('active',sel.mode==='day');
  document.getElementById('m-range').classList.toggle('active',sel.mode==='range');
  document.getElementById('single-wrap').classList.toggle('hidden',sel.mode!=='day');
  document.getElementById('range-wrap').classList.toggle('hidden',sel.mode!=='range');
}
(function initDates(){
  const dd=document.getElementById('d-day'), df=document.getElementById('d-from'), dt=document.getElementById('d-to');
  [dd,df,dt].forEach(el=>{el.min=D.window.min; el.max=D.window.max;});
  dd.value=sel.day; df.value=sel.from; dt.value=sel.to;
})();
document.getElementById('m-day').onclick=()=>{sel.mode='day';setModeUI();refresh();};
document.getElementById('m-range').onclick=()=>{sel.mode='range';setModeUI();refresh();};
document.getElementById('d-day').onchange=e=>{sel.day=e.target.value;refresh();};
document.getElementById('d-from').onchange=e=>{sel.from=e.target.value; if(sel.to<sel.from){sel.to=sel.from;document.getElementById('d-to').value=sel.to;} refresh();};
document.getElementById('d-to').onchange=e=>{sel.to=e.target.value; if(sel.to<sel.from){sel.from=sel.to;document.getElementById('d-from').value=sel.from;} refresh();};
document.getElementById('btn-latest').onclick=()=>{sel.mode='day';sel.day=D.default_date;document.getElementById('d-day').value=sel.day;setModeUI();refresh();};

document.querySelectorAll('[data-mk]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-mk]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); barMk=b.dataset.mk; drawBars();});
document.querySelectorAll('[data-mk2]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-mk2]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); mk2=b.dataset.mk2; renderTable();});
document.getElementById('q').oninput=renderTable;
document.getElementById('lookup').oninput=renderDetail;
document.getElementById('c-code').oninput=calcFill;
['c-price','c-face','c-coupon','c-freq','c-settle','c-mat'].forEach(id=>{
  const el=document.getElementById(id); el.oninput=runCalc; el.onchange=runCalc;});
document.getElementById('set-priv').onchange=e=>{SETTLE.private=parseInt(e.target.value)||0; renderDetail();};
document.getElementById('set-pub').onchange=e=>{SETTLE.public=parseInt(e.target.value)||0; renderDetail();};
document.getElementById('exp-detail').onclick=()=>exportTable('dtbl','bond_top20');
document.getElementById('exp-full').onclick=()=>exportTable('tbl','bond_all');
document.querySelectorAll('#tbl th').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; if(k===sortK)sortDir*=-1; else{sortK=k;sortDir=-1;} renderTable();});
document.querySelectorAll('.lang').forEach(b=>b.onclick=()=>{
  lang=b.dataset.lang; try{localStorage.setItem('bond_lang',lang);}catch(e){} drawAll();});

// ---- tab switching (Thi truong / Xep hang) ----
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{
  const v=b.dataset.view; rtView=(v==='rating');
  document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x===b));
  document.getElementById('view-market').hidden=(v!=='market');
  document.getElementById('view-rating').hidden=(v!=='rating');
  if(rtView) setDistMode();   // ve lai sau khi hien (co width dung)
});
// ---- rating change frame + sector toggle + history ----
document.querySelectorAll('[data-per]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-per]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); rtPer=b.dataset.per; drawChanges();});
document.querySelectorAll('[data-rd]').forEach(b=>b.onclick=()=>{ rtDistMode=b.dataset.rd; setDistMode(); });
document.querySelectorAll('[data-rs]').forEach(b=>b.onclick=()=>{
  rtScale=b.dataset.rs;
  document.querySelectorAll('[data-rs]').forEach(x=>x.classList.toggle('active',x.dataset.rs===rtScale));
  rtAgency=''; drawRating();
});
document.getElementById('rt-hist-q').oninput=renderHist;
// ---- rating directory controls ----
document.querySelectorAll('[data-rf]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-rf]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); rtFilter=b.dataset.rf; renderDir();});
document.getElementById('rt-ag-sel').onchange=e=>{rtAgency=e.target.value; renderDir();};
document.getElementById('rt-q').oninput=e=>{rtQ=e.target.value; renderDir();};
document.getElementById('rt-exp').onclick=()=>exportTable('rt-dir-tbl','credit_ratings');
document.querySelectorAll('#rt-dir-tbl th').forEach(th=>th.onclick=()=>{
  const k=th.dataset.rk; if(k===rtSortK)rtSortDir*=-1; else{rtSortK=k;rtSortDir=(k==='date'||k==='grade')?-1:1;} renderDir();});

setModeUI(); drawAll();
window.addEventListener('resize',()=>{drawBars();drawTrend();if(rtView)setDistMode();});
</script>
</body>
</html>
"""


def build_html(payload):
    html = (HTML_TEMPLATE
            .replace("%%DATA%%", json.dumps(payload, ensure_ascii=False))
            .replace("%%TREND_DAYS%%", str(TREND_DAYS)))
    OUT_HTML.write_text(html, encoding="utf-8")
    return OUT_HTML


# ------------------------------------------------------------------ credit rating
def run_rating(timeout=420):
    """Chay pipeline credit rating (5 CRA -> merge -> equity) trong CUNG lan build.
    Idempotent (append+dedup). Loi/timeout thi bo qua, dung data rating cu (khong chan build)."""
    import subprocess
    if not RATING_RUNNER.exists():
        print(f"  [!] Khong thay {RATING_RUNNER} -> bo qua rating.")
        return
    print("==> Cap nhat Credit Rating (5 CRA: Fiin/VIS/Saigon/TMR/SNI)...")
    try:
        r = subprocess.run(["cmd", "/c", str(RATING_RUNNER)],
                           capture_output=True, text=True, timeout=timeout)
        print("    Rating: xong." if r.returncode == 0
              else f"    Rating: ret={r.returncode} (xem logs\\rating_*.txt).")
    except subprocess.TimeoutExpired:
        print(f"  [!] Rating chay qua {timeout}s -> bo qua (dung data cu).")
    except Exception as e:
        print(f"  [!] Bo qua rating: {e}")


# ------------------------------------------------------------------ build + open
def build_and_open(update=True, open_it=True, rating=True):
    if update and rating:
        run_rating()                 # scrape rating TRUOC khi doc/dung (gop chung 1 lan chay)
    if update:
        try:
            import tpdn_scraper
            print("==> Cap nhat du lieu Cong chung (TPDN)...")
            tpdn_scraper.run()  # incremental
        except Exception as e:
            print(f"  [!] Bo qua cap nhat public: {e}")

    print("==> Doc du lieu 2 thi truong...")
    priv, priv_day = load_private()
    pub, pub_day = load_public()
    print(f"    Rieng le : {priv_day}  ({priv['scrape_day'].nunique() if len(priv) else 0} ngay)")
    print(f"    Cong chung: {pub_day}  ({pub['trading_date'].nunique() if len(pub) else 0} ngay)")

    # cap nhat coupon schedule (cbonds) cho cac ma private MOI chua co (de tinh clean price)
    if update and len(priv):
        try:
            import bondinfo_scraper as bi
            have = set(bi.load_cache())
            # PRIVATE (cbonds): uu tien ma co GD gan day; gioi han 80/lan (cache lon dan)
            recent = list(priv[priv["tv"] > 0]["Symbol"].unique())
            codes = [c for c in recent if c not in have][:80]
            if codes:
                print(f"==> Coupon schedule {len(codes)} ma private moi (cbonds)...")
                bi.run(codes)
                have = set(bi.load_cache())
            # PUBLIC (hnx.vn): coupon/issue/maturity/sector cho bond cong chung
            if len(pub):
                pubc = [c for c in pub[pub["tv"] > 0]["bond_code"].unique() if c not in have][:80]
                if pubc:
                    print(f"==> Thong tin {len(pubc)} ma public moi (hnx.vn)...")
                    bi.run(pubc, public=True)
        except Exception as e:
            print(f"  [!] Bo qua bondinfo: {e}")

    payload = build_payload(priv, priv_day, pub, pub_day)
    out = build_html(payload)
    print(f"\n==> Dashboard: {out}")
    print(f"    Cua so du lieu: {payload['window']['min']} -> {payload['window']['max']} "
          f"| ngay mac dinh (du ca 2 TT): {payload['default_date']} "
          f"| {len(payload['recs'])} dong, {len(payload['meta'])} ma")

    if open_it:
        try:
            os.startfile(str(out))  # Windows: mo bang trinh duyet mac dinh
        except Exception as e:
            print(f"  [!] Khong tu mo duoc: {e}")
    return out


def publish_site():
    """Copy dashboard -> repo GitHub Pages, commit + push. Chi day dung index.html."""
    import shutil, subprocess
    if not (PUBLISH_DIR / ".git").exists():
        print(f"  [!] Chua co repo publish tai {PUBLISH_DIR} -> bo qua publish.")
        return
    shutil.copyfile(OUT_HTML, PUBLISH_DIR / "index.html")

    def git(*args):
        return subprocess.run(["git", *args], cwd=str(PUBLISH_DIR),
                              capture_output=True, text=True)

    git("add", "index.html")
    status = git("status", "--porcelain")
    if not status.stdout.strip():
        print("  [=] Dashboard khong doi -> khong can push.")
        return
    msg = "Update dashboard " + datetime.now().strftime("%Y-%m-%d %H:%M")
    git("commit", "-m", msg)
    push = git("push", "origin", "main")
    if push.returncode == 0:
        print("  [OK] Da push len GitHub Pages.")
    else:
        print(f"  [!] Push loi: {push.stderr.strip()[:200]}")


# ------------------------------------------------------------------ daily poll
def _read_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(d):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _public_file_ready_today():
    """Kiem tra NHE (1 request): tin 'Ket qua giao dich TPDN' co bai dang HOM NAY chua."""
    try:
        import tpdn_scraper as tp
        s = tp.ttcb.make_session()
        rows = tp.ttcb.parse_rows(tp.ttcb.fetch(s, "HNX_PUBLIC", 1), "HNX_PUBLIC")
        today = date.today().strftime("%Y-%m-%d")
        for r in rows:
            if r["post_date"] == today and tp.TITLE_MATCH in r["title"]:
                return True
    except Exception as e:
        print(f"  [!] Kiem tra file public loi: {e}")
    return False


def daily_poll():
    """Chay moi 10 phut tu 8h sang: cho den khi co file public HOM NAY -> dung + pop 1 lan."""
    today = date.today().strftime("%Y-%m-%d")
    st = _read_state()
    if st.get("built_date") == today:
        print(f"[daily] Da dung dashboard hom nay ({today}) roi -> bo qua.")
        return
    if not _public_file_ready_today():
        print(f"[daily] Chua co file public hom nay ({today}). Se thu lai sau 10 phut.")
        return
    print(f"[daily] Da co file public hom nay ({today}) -> dung dashboard, mo len, publish.")
    build_and_open(update=True, open_it=True)
    try:
        publish_site()
    except Exception as e:
        print(f"  [!] Publish loi: {e}")
    _write_state({"built_date": today, "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true", help="Khong scrape lai public")
    ap.add_argument("--no-rating", action="store_true", help="Khong scrape lai credit rating trong lan build nay")
    ap.add_argument("--no-open", action="store_true", help="Khong tu mo dashboard")
    ap.add_argument("--daily", action="store_true",
                    help="Che do lich: cho file public hom nay roi moi dung + pop (chong pop trung)")
    ap.add_argument("--publish", action="store_true", help="Push dashboard len GitHub Pages")
    args = ap.parse_args()

    if args.daily:
        daily_poll()
    else:
        build_and_open(update=not args.no_update, open_it=not args.no_open, rating=not args.no_rating)
        if args.publish:
            publish_site()


if __name__ == "__main__":
    main()

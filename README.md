# Bond Dashboard (public + private placement, credit ratings)

**Live:** https://thanhnhaxinhdep.github.io/bond-dashboard/

Gop 2 thi truong trai phieu doanh nghiep (rieng le tu bang gia cbonds.hnx.vn,
cong chung tu ket qua giao dich TPDN tren HNX) + xep hang tin nhiem tu 6 to
chuc (FiinRatings, VIS Rating, Saigon Ratings, TMR, S&I Ratings, Fitch) thanh
1 dashboard tuong tac, tu chua du lieu (khong goi API luc xem).

## Cach cap nhat du lieu (hybrid: may local + GitHub Actions)

1. **May local (chinh)**: `D:\Nha\public bond trade\bond_app.py` chay qua cac
   Task Scheduler task co san ("HNX Bond Dashboard Daily", "HNX Bond Dashboard
   PM Refresh"...) - khong doi gi ca, van hoat dong nhu truoc, tu commit +
   push thang vao repo nay.
2. **GitHub Actions (song song, doc lap)**: `.github/workflows/update.yml`
   chay 1 ban sao day du cua toan bo pipeline (scrape 2 thi truong + 6 nguon
   xep hang, gop lai, dung dashboard) tu thu muc `pipeline/` trong CHINH repo
   nay - khong dung file/duong dan tren may local. Chay theo lich (2 lan/ngay)
   hoac bam **Run workflow** thu cong trong tab Actions tren GitHub (repo
   public, Actions mien phi khong gioi han).

Ca hai duong deu push vao cung nhanh `main`; khong gioi han so lan (repo
public = Actions free), khac voi fund-nav-dashboard (repo private, phai gioi
han 4 tieng/lan de khong vuot quota).

## Cau truc `pipeline/`

- `bond_app.py` - doc 2 nguon (`data/HNX/hnx_bonds.db` rieng le,
  `tpdn_data/TPDN_trading.csv` cong chung), goi `ratings` module, dung
  `bond_dashboard.html`.
- `scrape_hnx_outright.py` - scrape bang gia rieng le qua WebSocket cong khai
  `wss://cbonds.hnx.vn/board/ws`, ghi/append vao `data/HNX/hnx_bonds.db`.
- `tpdn_scraper.py` (+ `ttcb_scraper.py`) - scrape ket qua GD TPDN cong chung
  tu HNX, ghi/append vao `tpdn_data/TPDN_trading.csv`.
- `bondinfo_scraper.py` - coupon/issuer/dao han tung ma (cbonds.hnx.vn +
  hnx.vn), cache vao `bondinfo.csv`.
- `ratings.py` - doc du lieu xep hang tu `credit_rating/output/`.
- `credit_rating/` - ban sao doc lap cua pipeline xep hang tin nhiem
  (`D:\Nha\Credit rating` tren may local): 5 script Python scrape truc tiep
  (FiinRatings/VIS/Saigon/TMR/S&I) + `fitch-daily/` (Node + Playwright,
  scrape Fitch) + `fitch_to_csv.py` -> `merge_xhtn.py` -> `build_equity_ratings.py`
  -> `cleanup_dated_outputs.py` (xoa file dated cu, giu file `*_history.csv`).

**File accumulator PHAI duoc giu lai giua cac lan chay CI** (da seed san,
workflow tu commit lai moi lan co thay doi): `data/HNX/hnx_bonds.db`,
`tpdn_data/TPDN_trading.csv`, `bondinfo.csv`,
`credit_rating/output/*_xhtn_history.csv`,
`credit_rating/fitch-daily/data/snapshot-*.json`,
`credit_rating/toan_bo_doanh_nghiep_cbonds*.csv` (danh ba issuer, tinh, can
tu lam moi thu cong khi co danh sach moi),
`credit_rating/mapping_ticker_tay.csv` + `equity_list.tsv` (tinh, chi sua tay).

## Chay tay pipeline nay (vd de debug)

```bash
cd pipeline
pip install -r requirements.txt
python scrape_hnx_outright.py --out data/HNX --wait 15

cd credit_rating
python scrape_fiinratings.py --append
python scrape_visrating.py --append
python scrape_saigonratings.py --append
python scrape_tmr.py --append
python scrape_sniratings.py --append
cd fitch-daily && npm ci && npx playwright install --with-deps chromium && node fitch-daily.js && cd ..
python fitch_to_csv.py
python merge_xhtn.py
python build_equity_ratings.py
python cleanup_dated_outputs.py

cd ..
python bond_app.py --no-open --no-rating
cp bond_dashboard.html ../index.html
```

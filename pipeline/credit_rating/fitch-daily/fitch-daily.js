/* =========================================================================
 * FITCH RATINGS — THEO DÕI HẰNG NGÀY
 * -------------------------------------------------------------------------
 * Mỗi lần chạy:
 *   1. Quét danh sách entity (ép DATA VIEW để lấy đủ rating con)
 *   2. Vào từng trang entity lấy TRÁI PHIẾU (Securities and Obligations)
 *   3. Đối chiếu số lượng với con số trang Fitch tự báo
 *   4. Lưu  data/snapshot-YYYY-MM-DD.json
 *   5. So sánh với lần chạy trước
 *      Có thay đổi -> bao-cao/THAY-DOI-YYYY-MM-DD.txt   (thoát mã 10)
 *      Không       -> im lặng                            (thoát mã 0)
 *      Thiếu/lỗi   -> KHÔNG ghi đè dữ liệu cũ            (thoát mã 2)
 *
 * ⚠ BA CÁI BẪY ĐÃ KIỂM CHỨNG TRÊN TRANG THẬT (đừng sửa lại):
 *   1. Bấm nút số trang KHÔNG làm danh sách render lại → phải lật trang
 *      bằng cách vào thẳng URL &page=N.
 *   2. GRID VIEW chỉ có 1 rating/entity. Phải dùng &viewType=data.
 *   3. Mục trái phiếu ở trang entity chỉ load khi CUỘN TỚI, và mặc định
 *      chỉ hiện 10 dòng → phải đổi sang 100 dòng/trang.
 * ========================================================================= */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { BOC_TACH_TRANG } = require('./lib-boc-tach');
const { CHUAN_BI_TRAI_PHIEU, BOC_TACH_TRAI_PHIEU } = require('./lib-trai-phieu');

/* ======================= PHẦN CẦN CHỈNH SỬA ============================== */

const DANH_SACH_URL = [
  {
    ten: 'Vietnam - All Entities',
    url: 'https://www.fitchratings.com/search?expanded=entity&filter.country=Vietnam&isIdentifier=true&item=IDENTIFIERS',
  },
];

// Lấy cả rating từng lô trái phiếu? (chậm hơn ~3-5 phút vì phải mở từng trang entity)
const LAY_TRAI_PHIEU = false;

const HIEN_TRINH_DUYET = process.env.HEADLESS === 'false';
const NGHI_GIUA_CAC_TRANG_MS = 2000;

/* ======================= HẾT PHẦN CẦN CHỈNH SỬA ========================== */

const DIR_DATA = path.join(__dirname, 'data');
const DIR_BAOCAO = path.join(__dirname, 'bao-cao');
for (const d of [DIR_DATA, DIR_BAOCAO]) if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });

const homNay = new Date().toISOString().slice(0, 10);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const THANG = ['AAA','AA+','AA','AA-','A+','A','A-','BBB+','BBB','BBB-','BB+','BB','BB-',
               'B+','B','B-','CCC+','CCC','CCC-','CC','C','RD','D'];
const bac = (r) => THANG.indexOf(String(r || '').replace(/\s|\(.*?\)/g, '').toUpperCase());
function soSanhBac(cu, moi) {
  const a = bac(cu), b = bac(moi);
  if (a < 0 || b < 0 || a === b) return '';
  return b < a ? 'NÂNG BẬC ⬆' : 'HẠ BẬC ⬇';
}

function urlTrang(goc, p) {
  const u = new URL(goc);
  u.searchParams.set('viewType', 'data');
  if (p <= 1) u.searchParams.delete('page');
  else u.searchParams.set('page', String(p));
  return u.href;
}

async function moTrang(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('h3.frw-heading--5', { timeout: 60000 });
  await page.waitForTimeout(2000);
}

/* =================== QUÉT DANH SÁCH ENTITY =================== */
async function quetEntity(page, urlGoc, ten) {
  console.log(`\n→ [${ten}] danh sách entity`);
  await moTrang(page, urlTrang(urlGoc, 1));

  for (const t of ['Accept All', 'Accept all', 'I Accept', 'Agree']) {
    const b = page.getByRole('button', { name: t });
    if (await b.count().catch(() => 0)) { await b.first().click().catch(() => {}); break; }
  }

  const tongBaoCao = await page.evaluate(() => {
    const el = document.querySelector('.frw-search__results-count');
    const m = el && el.textContent.match(/([\d,]+)\s*Results?/i);
    return m ? Number(m[1].replace(/,/g, '')) : null;
  });
  const tongTrang = await page.evaluate(() => {
    const n = [...document.querySelectorAll('.frw-pager__items .frw-pager__item')]
      .map((li) => Number(li.textContent.trim())).filter(Number.isFinite);
    return n.length ? Math.max(...n) : 1;
  });
  console.log(`   Trang báo: ${tongBaoCao ?? '?'} entity / ${tongTrang} trang`);

  const daThay = new Set(), ketQua = [];
  let kieuView = '';
  for (let p = 1; p <= tongTrang; p++) {
    if (p > 1) await moTrang(page, urlTrang(urlGoc, p));
    const kq = await page.evaluate(BOC_TACH_TRANG);
    kieuView = kq.view;
    let them = 0;
    for (const r of kq.ds) {
      const k = r.entity_id || r.entity_name;
      if (!daThay.has(k)) { daThay.add(k); ketQua.push(r); them++; }
    }
    const sr = kq.ds.reduce((s, e) => s + e.ratings.length, 0);
    console.log(`   ✔ trang ${p}/${tongTrang} [${kq.view}]: ${kq.ds.length} entity, ${sr} rating (+${them} mới) — cộng dồn ${ketQua.length}`);
    if (p < tongTrang) await sleep(NGHI_GIUA_CAC_TRANG_MS);
  }

  if (tongBaoCao != null && ketQua.length !== tongBaoCao) {
    throw new Error(`[${ten}] THIẾU DỮ LIỆU: trang báo ${tongBaoCao} entity nhưng chỉ lấy được ${ketQua.length}. KHÔNG ghi đè dữ liệu cũ.`);
  }
  const rong = ketQua.filter((e) => !e.ratings.length);
  if (rong.length > ketQua.length * 0.5) {
    throw new Error(`[${ten}] BẤT THƯỜNG: ${rong.length}/${ketQua.length} entity không có rating nào. KHÔNG ghi đè dữ liệu cũ.`);
  }

  const tongRating = ketQua.reduce((s, e) => s + e.ratings.length, 0);
  console.log(`   ✅ Khớp ${ketQua.length}/${tongBaoCao ?? ketQua.length} entity — ${tongRating} dòng rating (chế độ ${kieuView})`);
  if (kieuView !== 'data') console.log('   ⚠ Đang ở GRID VIEW — chỉ có rating chính. Kiểm tra lại URL.');
  return ketQua;
}

/* =================== SỐ TRÁI PHIẾU TRANG FITCH TỰ BÁO =================== */
async function demTraiPhieuTrenSearch(page, urlGoc) {
  const u = new URL(urlGoc);
  u.searchParams.delete('expanded');
  u.searchParams.delete('page');
  u.searchParams.delete('viewType');
  try {
    await page.goto(u.href, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('.frw-search__results-count', { timeout: 40000 });
    await page.waitForTimeout(2500);
    return await page.evaluate(() => {
      const secs = [...document.querySelectorAll('.frw-section')];
      for (const s of secs) {
        const h = s.querySelector('.frw-search__section-heading');
        if (h && /Securities and Obligations/i.test(h.textContent)) {
          const m = (s.querySelector('.frw-search__results-count') || {}).textContent || '';
          const n = m.match(/([\d,]+)\s*Results?/i);
          return n ? Number(n[1].replace(/,/g, '')) : null;
        }
      }
      return null;
    });
  } catch { return null; }
}

/* =================== QUÉT TRÁI PHIẾU TỪNG ENTITY =================== */
async function quetTraiPhieu(page, dsEntity, ten) {
  console.log(`\n→ [${ten}] trái phiếu — mở ${dsEntity.length} trang entity`);
  const tatCa = [];
  for (let i = 0; i < dsEntity.length; i++) {
    const e = dsEntity[i];
    if (!e.entity_url) continue;
    const nhan = `   [${String(i + 1).padStart(2)}/${dsEntity.length}] ${e.entity_name.slice(0, 44)}`;
    try {
      await page.goto(e.entity_url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(2500);
      const co = await page.evaluate(CHUAN_BI_TRAI_PHIEU);
      if (!co) { console.log(`${nhan} — không có lô nào`); continue; }
      const ds = await page.evaluate(BOC_TACH_TRAI_PHIEU);
      ds.forEach((x) => {
        x.entity_name = e.entity_name;
        x.entity_id = e.entity_id;
        x.entity_url = e.entity_url;
      });
      tatCa.push(...ds);
      console.log(`${nhan} — ${ds.length} lô`);
    } catch (err) {
      console.log(`${nhan} — LỖI: ${String(err.message).slice(0, 60)}`);
    }
    await sleep(800);
  }
  return tatCa;
}

/* =================== SO SÁNH =================== */
function lamPhangEntity(entities) {
  const map = new Map();
  for (const [nguon, ds] of Object.entries(entities || {})) {
    for (const e of ds) for (const r of e.ratings) {
      map.set(`${nguon}||E||${e.entity_id || e.entity_name}||${r.rating_type}`, {
        loaiMuc: 'entity', ten: e.entity_name, url: e.entity_url, loai: r.rating_type,
        rating: r.rating, action: r.action, ngay: r.date, outlook: r.outlook_watch,
      });
    }
  }
  return map;
}
function lamPhangTraiPhieu(securities) {
  const map = new Map();
  for (const [nguon, ds] of Object.entries(securities || {})) {
    for (const t of ds) {
      const dinhDanh = t.isin || t.issue;
      map.set(`${nguon}||S||${dinhDanh}||${t.rating_type}`, {
        loaiMuc: 'trái phiếu', ten: `${t.entity_name} — ${t.issue}`, url: t.entity_url,
        loai: t.rating_type, rating: t.rating, action: t.action, ngay: t.date,
        outlook: t.outlook_watch, isin: t.isin, dao_han: t.maturity,
      });
    }
  }
  return map;
}

function soSanh(cu, moi) {
  const A = new Map([...lamPhangEntity(cu.entities), ...lamPhangTraiPhieu(cu.securities)]);
  const B = new Map([...lamPhangEntity(moi.entities), ...lamPhangTraiPhieu(moi.securities)]);
  const tds = [];
  for (const [k, m] of B) {
    const c = A.get(k);
    if (!c) { tds.push({ kieu: 'MỚI', m }); continue; }
    const dc = [];
    if (c.rating !== m.rating) {
      const h = soSanhBac(c.rating, m.rating);
      dc.push(`Rating: ${c.rating} → ${m.rating}${h ? '  [' + h + ']' : ''}`);
    }
    if (c.outlook !== m.outlook) dc.push(`Outlook/Watch: ${c.outlook || '(không)'} → ${m.outlook || '(không)'}`);
    if (c.action !== m.action || c.ngay !== m.ngay)
      dc.push(`Rating action: ${c.action} (${c.ngay}) → ${m.action} (${m.ngay})`);
    if (dc.length) tds.push({ kieu: 'THAY ĐỔI', m, c, dc });
  }
  for (const [k, c] of A) if (!B.has(k)) tds.push({ kieu: 'BIẾN MẤT', c });

  const uu = (t) => {
    const s = (t.dc || []).join(' ');
    if (s.includes('HẠ BẬC')) return 0;
    if (s.includes('NÂNG BẬC')) return 1;
    if (t.kieu === 'MỚI') return 2;
    if (t.kieu === 'THAY ĐỔI') return 3;
    return 4;
  };
  const chinh = (t) => (/^Long Term Issuer Default Rating$/i.test((t.m || t.c).loai) ? 0 : 1);
  return tds.sort((a, b) => uu(a) - uu(b) || chinh(a) - chinh(b));
}

function vietBaoCao(tds, ngayCu) {
  const L = [];
  const soTP = tds.filter((t) => (t.m || t.c).loaiMuc === 'trái phiếu').length;
  L.push('='.repeat(74));
  L.push(`BÁO CÁO THAY ĐỔI XẾP HẠNG FITCH — ${homNay}`);
  L.push(`So với lần quét ngày: ${ngayCu}`);
  L.push(`Số thay đổi: ${tds.length}  (entity: ${tds.length - soTP}, trái phiếu: ${soTP})`);
  L.push('='.repeat(74));
  for (const t of tds) {
    const x = t.m || t.c;
    const nhan = x.loaiMuc === 'trái phiếu' ? 'TRÁI PHIẾU' : 'ENTITY';
    L.push('');
    if (t.kieu === 'THAY ĐỔI') {
      L.push(`[THAY ĐỔI · ${nhan}] ${x.ten}`);
      L.push(`  Loại rating : ${x.loai}`);
      if (x.isin) L.push(`  ISIN        : ${x.isin}   Đáo hạn: ${x.dao_han || '-'}`);
      t.dc.forEach((d) => L.push(`  ${d}`));
      L.push(`  Link        : ${x.url}`);
    } else if (t.kieu === 'MỚI') {
      L.push(`[MỚI · ${nhan}] ${x.ten}`);
      L.push(`  ${x.loai}: ${x.rating} ${x.outlook ? '(' + x.outlook + ')' : ''}`);
      if (x.isin) L.push(`  ISIN        : ${x.isin}   Đáo hạn: ${x.dao_han || '-'}`);
      L.push(`  ${x.action} — ${x.ngay}`);
      L.push(`  Link        : ${x.url}`);
    } else {
      L.push(`[BIẾN MẤT · ${nhan}] ${x.ten}`);
      L.push(`  ${x.loai}: ${x.rating}`);
      if (x.isin) L.push(`  ISIN        : ${x.isin}`);
      L.push(`  Link        : ${x.url}`);
    }
  }
  L.push('');
  L.push('-'.repeat(74));
  L.push('Nguồn: fitchratings.com — dữ liệu công khai trên trang search và trang entity.');
  return L.join('\n');
}

/* ---------- Đọc snapshot cũ, chấp nhận cả định dạng đời đầu ---------- */
function docSnapshot(p) {
  const j = JSON.parse(fs.readFileSync(p, 'utf8'));
  if (j && j._v >= 2) return j;
  return { _v: 1, entities: j, securities: {} };   // định dạng cũ: chỉ có entity
}

/* Cho phép file test nạp các hàm so sánh mà không chạy trình duyệt */
module.exports = { soSanh, vietBaoCao, docSnapshot, soSanhBac };
if (require.main !== module) return;

/* ============================== CHẠY ==================================== */
(async () => {
  const browser = await chromium.launch({ headless: !HIEN_TRINH_DUYET });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
    viewport: { width: 1440, height: 1000 },
  });
  const page = await ctx.newPage();

  const snapshot = { _v: 2, ngay: homNay, entities: {}, securities: {} };
  try {
    for (const m of DANH_SACH_URL) {
      snapshot.entities[m.ten] = await quetEntity(page, m.url, m.ten);

      if (LAY_TRAI_PHIEU) {
        const mongDoi = await demTraiPhieuTrenSearch(page, m.url);
        const tp = await quetTraiPhieu(page, snapshot.entities[m.ten], m.ten);
        snapshot.securities[m.ten] = tp;
        console.log(`   ✅ Trái phiếu: ${tp.length} lô` + (mongDoi != null ? ` (trang Fitch báo ${mongDoi})` : ''));
        if (mongDoi != null && tp.length < mongDoi) {
          console.log(`   ⚠ Ít hơn con số Fitch báo — có thể vài lô thuộc entity ngoài bộ lọc. Vẫn lưu bình thường.`);
        }
      }
    }
  } catch (err) {
    console.error('\n❌ ' + err.message);
    await browser.close();
    process.exit(2);
  }
  await browser.close();

  const tongEntity = Object.values(snapshot.entities).reduce((s, a) => s + a.length, 0);
  const tongRating = Object.values(snapshot.entities).reduce((s, a) => s + a.reduce((m, e) => m + e.ratings.length, 0), 0);
  const tongTP = Object.values(snapshot.securities).reduce((s, a) => s + a.length, 0);
  if (tongEntity === 0) { console.error('❌ Không lấy được entity nào. KHÔNG ghi đè.'); process.exit(2); }

  fs.writeFileSync(path.join(DIR_DATA, `snapshot-${homNay}.json`), JSON.stringify(snapshot, null, 2), 'utf8');
  console.log(`\n💾 Đã lưu: ${tongEntity} entity / ${tongRating} rating / ${tongTP} lô trái phiếu → data/snapshot-${homNay}.json`);

  const cacFile = fs.readdirSync(DIR_DATA)
    .filter((f) => /^snapshot-\d{4}-\d{2}-\d{2}\.json$/.test(f)).sort();
  const fileCu = cacFile.filter((f) => f < `snapshot-${homNay}.json`).pop();
  if (!fileCu) { console.log('ℹ️  Lần chạy đầu tiên — đã lưu làm mốc gốc. Từ mai bắt đầu so sánh.'); process.exit(0); }

  const ngayCu = fileCu.slice(9, 19);
  const cu = docSnapshot(path.join(DIR_DATA, fileCu));
  if (cu._v === 1) {
    console.log('⚠ Snapshot cũ là định dạng đời đầu (chưa có trái phiếu) — lần này sẽ báo toàn bộ trái phiếu là "MỚI". Bình thường, chỉ xảy ra 1 lần.');
  }

  const tds = soSanh(cu, snapshot);
  if (!tds.length) { console.log(`✅ Không có thay đổi nào so với ngày ${ngayCu}.`); process.exit(0); }

  const noiDung = vietBaoCao(tds, ngayCu);
  fs.writeFileSync(path.join(DIR_BAOCAO, `THAY-DOI-${homNay}.txt`), '﻿' + noiDung, 'utf8');
  console.log('\n' + noiDung);
  console.log(`\n🔔 CÓ ${tds.length} THAY ĐỔI → bao-cao/THAY-DOI-${homNay}.txt`);
  process.exit(10);
})();

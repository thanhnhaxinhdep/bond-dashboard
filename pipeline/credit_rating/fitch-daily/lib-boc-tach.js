/* =========================================================================
 * HÀM BÓC TÁCH DÙNG CHUNG — chạy BÊN TRONG trang web
 * -------------------------------------------------------------------------
 * Trang Fitch có 2 kiểu hiển thị:
 *   • GRID VIEW (mặc định)  → mỗi entity chỉ 1 rating chính, kèm chữ
 *                             "Additional Ratings Available"
 *   • DATA VIEW (&viewType=data) → ĐẦY ĐỦ mọi rating con: Short Term IDR,
 *                             Viability, Government Support, Country Ceiling,
 *                             Local Currency, các bản (xgs)...
 *
 * Script luôn ép dùng DATA VIEW. Hàm dưới vẫn đọc được cả 2 kiểu để phòng
 * trường hợp Fitch đổi mặc định.
 * ========================================================================= */

const BOC_TACH_TRANG = () => {
  const OUTLOOK = {
    ROPos: 'Outlook Positive', RONeg: 'Outlook Negative',
    ROSta: 'Outlook Stable',   ROEvo: 'Outlook Evolving',
    RWPos: 'Watch Positive',   RWNeg: 'Watch Negative', RWEvo: 'Watch Evolving',
  };
  const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

  // Nhận cả "13 Jul 2026" (grid) lẫn "06-Nov-2025" (data)
  const toISO = (s) => {
    const m = /^(\d{1,2})[-\s]([A-Za-z]{3})[a-z]*[-\s](\d{4})$/.exec((s || '').trim());
    if (!m) return '';
    const mm = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']
      .indexOf(m[2].toLowerCase()) + 1;
    return mm ? `${m[3]}-${String(mm).padStart(2,'0')}-${String(m[1]).padStart(2,'0')}` : '';
  };

  const olCua = (cell) => {
    const s = cell.querySelector('span[class*="frw-RO"], span[class*="frw-RW"]');
    return s ? (OUTLOOK[(s.className.match(/frw-(R[OW]\w+)/) || [])[1]] || '') : '';
  };

  const base = (c) => {
    const a = c.querySelector('h3.frw-heading--5 a');
    const href = a ? a.getAttribute('href') : '';
    return {
      entity_name: txt(a),
      entity_type: txt(c.querySelector('.frw-heading--tag')),
      entity_id: (href.match(/-(\d+)$/) || [])[1] || '',
      entity_url: href ? new URL(href, location.origin).href : '',
      ratings: [],
    };
  };

  /* ---------- DATA VIEW: đầy đủ rating con ---------- */
  const dataConts = [...document.querySelectorAll('.frw-column__four.frw-entity-data')]
    .filter((c) => c.querySelector('h3.frw-heading--5 a'));   // bỏ container rỗng

  if (dataConts.length) {
    return {
      view: 'data',
      ds: dataConts.map((c) => {
        const e = base(c);
        e.view = 'data';
        e.has_more_ratings = false;
        [...c.querySelectorAll('table.frw-entity-data tr')].forEach((tr) => {
          const td = [...tr.children];
          if (td.length < 4) return;                       // cột: rating|action|date|loại
          e.ratings.push({
            rating: txt(td[0]), rating_type: txt(td[3]), action: txt(td[1]),
            date: txt(td[2]), date_iso: toISO(txt(td[2])), outlook_watch: olCua(td[0]),
          });
        });
        return e;
      }),
    };
  }

  /* ---------- GRID VIEW: chỉ rating chính (dự phòng) ---------- */
  const ds = [...document.querySelectorAll('h3.frw-heading--5')].map((h3) => {
    const card = h3.closest('.frw-column');
    const e = base(card);
    e.view = 'grid';
    e.has_more_ratings = /Additional Ratings Available/i.test(txt(card));
    const rows = [...card.querySelectorAll('table.frw-table__wrapper tr')];
    for (let i = 0; i < rows.length; i++) {
      const td = [...rows[i].children];
      if (td.length < 3) continue;
      let loai = '';
      if (rows[i + 1] && rows[i + 1].children.length === 1) loai = txt(rows[++i]);
      e.ratings.push({
        rating: txt(td[0]), rating_type: loai, action: txt(td[1]),
        date: txt(td[2]), date_iso: toISO(txt(td[2])), outlook_watch: olCua(td[0]),
      });
    }
    return e;
  });
  return { view: 'grid', ds };
};

module.exports = { BOC_TACH_TRANG };

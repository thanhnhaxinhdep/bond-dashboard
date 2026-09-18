/* =========================================================================
 * BÓC TÁCH TRÁI PHIẾU (Securities and Obligations) — chạy BÊN TRONG trang
 * -------------------------------------------------------------------------
 * Mục "05 Securities and Obligations" nằm ở cuối trang entity, dùng
 * component ReactTable (.ReactTable > .rt-tbody > .rt-tr > .rt-td) và
 * CHỈ LOAD KHI CUỘN TỚI. Mặc định hiện 10 dòng/trang — phải đổi ô chọn
 * số dòng sang 100 mới lấy đủ.
 *
 * Cấu trúc mỗi ô là <strong>Nhãn:</strong> + text node giá trị, nên phải
 * duyệt childNodes chứ không dùng textContent.
 * ========================================================================= */

/* Bước 1: cuộn xuống cho mục trái phiếu load ra, rồi đặt 100 dòng/trang.
   Trả về true nếu có bảng trái phiếu, false nếu entity này không có lô nào. */
const CHUAN_BI_TRAI_PHIEU = async () => {
  const cho = (ms) => new Promise((r) => setTimeout(r, ms));

  const heading = [...document.querySelectorAll('h2,h3,h4')]
    .find((e) => /Securities and Obligations/i.test(e.textContent));
  const anchor = document.querySelector('#securities-and-obligations');
  if (!heading && !anchor) return false;
  (anchor || heading).scrollIntoView({ block: 'center' });

  // chờ ReactTable xuất hiện (tối đa 15 giây)
  for (let i = 0; i < 30; i++) {
    if (document.querySelector('.ReactTable .rt-tbody .rt-tr')) break;
    window.scrollBy(0, 120);
    await cho(500);
  }
  const rt = document.querySelector('.ReactTable');
  if (!rt) return false;

  // đổi 10 dòng/trang -> 100 dòng/trang (React nên phải dùng native setter)
  const sel = rt.querySelector('.-pageSizeOptions select');
  if (sel && sel.value !== '100') {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
    setter.call(sel, '100');
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    await cho(3000);
  }
  return true;
};

/* Bước 2: đọc bảng */
const BOC_TACH_TRAI_PHIEU = () => {
  const OL = {
    ROPos: 'Outlook Positive', RONeg: 'Outlook Negative', ROSta: 'Outlook Stable',
    ROEvo: 'Outlook Evolving',  RWPos: 'Watch Positive',   RWNeg: 'Watch Negative',
    RWEvo: 'Watch Evolving',
  };
  const cl = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const toISO = (s) => {
    const m = /^(\d{1,2})[-\s]([A-Za-z]{3})[a-z]*[-\s](\d{4})$/.exec(cl(s));
    if (!m) return '';
    const mm = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']
      .indexOf(m[2].toLowerCase()) + 1;
    return mm ? `${m[3]}-${String(mm).padStart(2,'0')}-${String(m[1]).padStart(2,'0')}` : '';
  };
  // gom các cặp <strong>Nhãn:</strong> giá-trị trong 1 ô
  const cap = (td) => {
    const o = {}; let k = null;
    if (!td) return o;
    for (const n of td.childNodes) {
      if (n.nodeType === 1 && n.tagName === 'STRONG') k = cl(n.textContent).replace(/:$/, '');
      else if (n.nodeType === 3 && k) { const v = cl(n.textContent); if (v) { o[k] = v; k = null; } }
    }
    return o;
  };

  const rt = document.querySelector('.ReactTable');
  if (!rt) return [];

  return [...rt.querySelectorAll('.rt-tbody .rt-tr')]
    .filter((tr) => cl(tr.textContent))
    .map((tr) => {
      const td = [...tr.querySelectorAll('.rt-td')];
      const A = cap(td[0]), D = cap(td[3]), E = cap(td[4]);

      // ô Ratings: [ngày, rating, action, loại rating]
      const rc = td[1];
      const parts = rc ? [...rc.childNodes]
        .filter((n) => n.nodeType === 3 || (n.nodeType === 1 && n.tagName === 'SPAN' && !/rating-history/.test(n.className || '')))
        .map((n) => cl(n.textContent)).filter(Boolean) : [];
      const olSpan = rc ? rc.querySelector('span[class*="frw-RO"], span[class*="frw-RW"]') : null;

      // dòng text đầu tiên của ô "Debt Type & Identifiers"
      const dtypeNode = td[3] ? [...td[3].childNodes].find((n) => n.nodeType === 3) : null;

      return {
        issuer: A['Issuer'] || '',
        debt_level: A['Debt Level'] || '',
        issue: A['Issue'] || '',
        rating: parts[1] || '',
        action: parts[2] || '',
        rating_type: parts[3] || '',
        date: parts[0] || '',
        date_iso: toISO(parts[0]),
        outlook_watch: olSpan ? (OL[(olSpan.className.match(/frw-(R[OW]\w+)/) || [])[1]] || '') : '',
        debt_type: cl(dtypeNode ? dtypeNode.textContent : ''),
        isin: (D['ISIN'] || '').replace(/\s*\(.*\)\s*$/, ''),
        maturity: E['Maturity Date'] || '',
        maturity_iso: toISO(E['Maturity Date']),
        currency: E['Currency'] || '',
        amount: E['Amount'] || '',
        coupon: E['Coupon Rate'] || '',
        placement: E['Placement'] || '',
      };
    });
};

module.exports = { CHUAN_BI_TRAI_PHIEU, BOC_TACH_TRAI_PHIEU };

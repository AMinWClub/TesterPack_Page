/**
 * SORRAD / Tester Selection — FRONT-END DESIGN PROTOTYPE
 * -----------------------------------------------------
 * This file uses only local mock data. There is NO API, database, login,
 * payment, form submission or localStorage. Reloading resets selections.
 * Replace PERFUMES and ORDER in the API integration phase.
 */

const MAX_TESTERS = 10;

// Names, order data and visuals are fictional samples for design review.
const PERFUMES = [
  { id: 1, name: 'رز مخملی', brand: 'MAISON ÉLLE', en: 'VELVET ROSE', gender: 'زنانه', season: 'فصول سرد', scent: 'گرم و شیرین', tone: 'rose', shape: 'classic', tag: 'پرطرفدار' },
  { id: 2, name: 'آبی بی‌انتها', brand: 'NOVA PARFUMS', en: 'BLUE INFINITY', gender: 'مردانه', season: 'چهار فصل', scent: 'خنک و تلخ', tone: 'blue', shape: 'tall', tag: 'انتخاب خاص' },
  { id: 3, name: 'طلای کهربا', brand: 'ATELIER AMBRE', en: 'AMBER GOLD', gender: 'یونیسکس', season: 'فصول سرد', scent: 'گرم و شرقی', tone: 'amber', shape: 'square', tag: 'لوکس' },
  { id: 4, name: 'شکوفه سپید', brand: 'MAISON ÉLLE', en: 'WHITE BLOOM', gender: 'زنانه', season: 'بهار و تابستان', scent: 'ملایم و گلی', tone: 'ivory', shape: 'round', tag: 'لطیف' },
  { id: 5, name: 'ساحل نقره‌ای', brand: 'NOVA PARFUMS', en: 'SILVER COAST', gender: 'مردانه', season: 'فصول گرم', scent: 'خنک و مرکباتی', tone: 'sage', shape: 'square', tag: 'با‌طراوت' },
  { id: 6, name: 'عود سلطنتی', brand: 'NOIR COLLECTION', en: 'ROYAL OUD', gender: 'یونیسکس', season: 'پاییز و زمستان', scent: 'تلخ و چوبی', tone: 'noir', shape: 'classic', tag: 'خاص' },
  { id: 7, name: 'رویای یاس', brand: 'FLEUR D’OR', en: 'JASMINE DREAM', gender: 'زنانه', season: 'بهار', scent: 'شیرین و گلی', tone: 'lilac', shape: 'tall', tag: 'دلنشین' },
  { id: 8, name: 'ماجراجوی شب', brand: 'NOIR COLLECTION', en: 'NIGHT VOYAGE', gender: 'مردانه', season: 'چهار فصل', scent: 'تلخ و ادویه‌ای', tone: 'slate', shape: 'round', tag: 'جذاب' },
  { id: 9, name: 'طلوع مرکبات', brand: 'ATELIER AMBRE', en: 'CITRUS SUN', gender: 'یونیسکس', season: 'فصول گرم', scent: 'خنک و ترش', tone: 'citrus', shape: 'classic', tag: 'انرژی‌بخش' },
  { id: 10, name: 'ابریشم صورتی', brand: 'FLEUR D’OR', en: 'PINK SILK', gender: 'زنانه', season: 'چهار فصل', scent: 'شیرین و پودری', tone: 'peach', shape: 'square', tag: 'محبوب' },
  { id: 11, name: 'سدر آبی', brand: 'NOVA PARFUMS', en: 'CEDAR BLUE', gender: 'مردانه', season: 'پاییز و بهار', scent: 'چوبی و خنک', tone: 'teal', shape: 'tall', tag: 'مدرن' },
  { id: 12, name: 'شب طلایی', brand: 'NOIR COLLECTION', en: 'GOLDEN NIGHT', gender: 'یونیسکس', season: 'فصول سرد', scent: 'گرم و ادویه‌ای', tone: 'plum', shape: 'round', tag: 'متمایز' },
];

const PALETTES = {
  rose: { bg: '#f2e9e8', glass: '#ae596b', liquid: '#d9a0a2', cap: '#b99458', light: '#fff8f1', accent: '#853f55' },
  blue: { bg: '#e8eef5', glass: '#1a4483', liquid: '#477eb9', cap: '#233653', light: '#dde9ff', accent: '#122f60' },
  amber: { bg: '#f4ecdf', glass: '#b47a36', liquid: '#ebc076', cap: '#b5965a', light: '#fff8dd', accent: '#734b25' },
  ivory: { bg: '#f2f1e9', glass: '#d5c5a5', liquid: '#ece5d0', cap: '#d1b56f', light: '#fffefa', accent: '#a38a65' },
  sage: { bg: '#e7efec', glass: '#58877f', liquid: '#a6ccc4', cap: '#acacb1', light: '#f4fffc', accent: '#356c64' },
  noir: { bg: '#e7e5e5', glass: '#262a32', liquid: '#4c463f', cap: '#c2a16e', light: '#acaba5', accent: '#c7a56a' },
  lilac: { bg: '#f0e9f1', glass: '#af91c2', liquid: '#dbbedf', cap: '#c9a961', light: '#fff4ff', accent: '#86618e' },
  slate: { bg: '#e6eaf0', glass: '#35465b', liquid: '#72879d', cap: '#282b34', light: '#d3dce5', accent: '#2d384f' },
  citrus: { bg: '#f2f0df', glass: '#a0ab60', liquid: '#d6d98b', cap: '#b9a463', light: '#fffce4', accent: '#808a3d' },
  peach: { bg: '#f5ece7', glass: '#d69d8e', liquid: '#eecabb', cap: '#c6ad83', light: '#fff7f2', accent: '#b16f62' },
  teal: { bg: '#e4eff0', glass: '#277d89', liquid: '#89b7bb', cap: '#c8b9a3', light: '#ecffff', accent: '#17606c' },
  plum: { bg: '#efe8ec', glass: '#6c415d', liquid: '#ab7e9c', cap: '#c9a961', light: '#fbe8f5', accent: '#673253' },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const farsiNumber = n => Number(n).toLocaleString('fa-IR');
const selected = [];
let activeFilter = 'all';
let query = '';
let toastTimer;
let lastFocus = null;
const bottleImages = new Map();

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

// Local vector product placeholders. No image server or external URLs needed.
// Real API-provided perfume images will replace these during integration.
function bottleArt(item) {
  if (bottleImages.has(item.id)) return bottleImages.get(item.id);
  const p = PALETTES[item.tone];
  const coords = {
    classic: { x: 103, y: 134, w: 194, h: 197, rx: 15, cx: 164, cw: 73, cy: 90, ch: 48, neck: 20 },
    square: { x: 86, y: 145, w: 228, h: 188, rx: 6, cx: 154, cw: 93, cy: 95, ch: 46, neck: 17 },
    tall: { x: 124, y: 109, w: 154, h: 232, rx: 12, cx: 162, cw: 77, cy: 68, ch: 43, neck: 17 },
    round: { x: 101, y: 130, w: 198, h: 202, rx: 51, cx: 160, cw: 79, cy: 86, ch: 45, neck: 18 },
  }[item.shape];
  const { x, y, w, h, rx, cx, cw, cy, ch, neck } = coords;
  const labelW = Math.min(w - 40, 148);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" role="img" aria-label="تصویر نمایشی شیشه عطر ${escapeHtml(item.name)}">
    <defs>
      <linearGradient id="glass" x1="0" y1="0" x2="1" y2=".15"><stop offset="0" stop-color="${p.light}" stop-opacity=".72"/><stop offset=".18" stop-color="${p.glass}" stop-opacity=".85"/><stop offset=".54" stop-color="${p.liquid}" stop-opacity=".98"/><stop offset=".88" stop-color="${p.glass}" stop-opacity=".94"/><stop offset="1" stop-color="${p.light}" stop-opacity=".79"/></linearGradient>
      <linearGradient id="cap" x1="0" y1="0" x2="1" y2=".2"><stop stop-color="${p.cap}"/><stop offset=".2" stop-color="${p.light}"/><stop offset=".46" stop-color="${p.cap}"/><stop offset=".87" stop-color="${p.accent}"/><stop offset="1" stop-color="${p.cap}"/></linearGradient>
      <linearGradient id="reflection" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#fff" stop-opacity=".65"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
      <filter id="sh" x="-60%" y="-60%" width="220%" height="250%"><feGaussianBlur stdDeviation="10"/></filter>
    </defs>
    <ellipse cx="200" cy="356" rx="100" ry="11" fill="${p.accent}" opacity=".16" filter="url(#sh)"/>
    <path d="M${cx+15},${cy+ch-3}h${cw-30}v${neck+2}h-${cw-30}Z" fill="url(#cap)" stroke="${p.accent}" stroke-opacity=".4"/>
    <rect x="${cx}" y="${cy}" width="${cw}" height="${ch}" rx="4" fill="url(#cap)" stroke="${p.accent}" stroke-opacity=".24" stroke-width="2"/>
    <path d="M${cx+5} ${cy+3}v${ch-7}" stroke="white" stroke-opacity=".45" stroke-width="4"/>
    <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="url(#glass)" stroke="${p.light}" stroke-width="2.5"/>
    <rect x="${x+7}" y="${y+7}" width="${w-14}" height="${h-15}" rx="${Math.max(rx-5,3)}" fill="none" stroke="#fff" stroke-opacity=".35" stroke-width="1.8"/>
    <path d="M${x+17} ${y+19}q-7 55-1 ${h-45}" fill="none" stroke="url(#reflection)" stroke-width="11" stroke-linecap="round" opacity=".68"/>
    <path d="M${x+w-18} ${y+24}q4 80-1 ${h-55}" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".17"/>
    <rect x="${200-labelW/2}" y="${y+h*.37}" width="${labelW}" height="${h*.38}" rx="2" fill="${item.tone==='noir'||item.tone==='slate'||item.tone==='blue'?'#152039':'#fffaf1'}" fill-opacity=".88" stroke="${p.accent}" stroke-opacity=".3"/>
    <line x1="${200-labelW/2+14}" y1="${y+h*.435}" x2="${200+labelW/2-14}" y2="${y+h*.435}" stroke="${p.cap}" stroke-opacity=".8" stroke-width="1.2"/>
    <text x="200" y="${y+h*.545}" text-anchor="middle" font-family="Georgia,Times New Roman,serif" letter-spacing="1.2" font-weight="bold" font-size="${item.en.length>12?12:15}" fill="${item.tone==='noir'||item.tone==='slate'||item.tone==='blue'?'#f4e6ce':p.accent}">${escapeHtml(item.en)}</text>
    <text x="200" y="${y+h*.65}" text-anchor="middle" font-family="Georgia,serif" letter-spacing="2" font-size="8" fill="${item.tone==='noir'||item.tone==='slate'||item.tone==='blue'?'#c9b891':p.accent}">EAU DE PARFUM</text>
    <line x1="${200-labelW/2+25}" y1="${y+h*.695}" x2="${200+labelW/2-25}" y2="${y+h*.695}" stroke="${p.cap}" stroke-width=".8"/>
    <text x="200" y="${y+h-17}" text-anchor="middle" font-family="Georgia,serif" letter-spacing="1.5" font-size="8" fill="${p.light}" opacity=".85">SORRAD DISCOVERY EDIT</text>
  </svg>`;
  const src = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
  bottleImages.set(item.id, src);
  return src;
}

const selectedHas = id => selected.includes(id);
function getProduct(id) { return PERFUMES.find(item => item.id === id); }

function cardTemplate(item) {
  const isSelected = selectedHas(item.id);
  const isFull = selected.length >= MAX_TESTERS && !isSelected;
  const p = PALETTES[item.tone];
  return `<article class="product-card ${isSelected ? 'is-selected' : ''}" data-id="${item.id}">
    <div class="product-visual" style="--artbg:${p.bg}">
      <span class="product-tag">${escapeHtml(item.tag)}</span>
      <span class="product-selected-mark" aria-label="انتخاب شده"><svg class="ico"><use href="#i-check"/></svg></span>
      <img src="${bottleArt(item)}" alt="تصویر نمونه و غیرواقعی شیشه عطر ${escapeHtml(item.name)}" loading="eager" />
    </div>
    <div class="product-body">
      <div class="product-brand" dir="ltr">${escapeHtml(item.brand)}</div>
      <h3 class="product-name">${escapeHtml(item.name)}</h3>
      <div class="product-details">
        <div class="product-detail"><span class="product-detail-label">جنسیت</span><span class="product-detail-value">${item.gender}</span></div>
        <div class="product-detail"><span class="product-detail-label">مناسب فصل</span><span class="product-detail-value">${item.season}</span></div>
        <div class="product-detail"><span class="product-detail-label">رایحه</span><span class="product-detail-value">${item.scent}</span></div>
      </div>
      <button class="product-button" data-toggle="${item.id}" type="button" aria-label="${isSelected ? 'حذف' : 'افزودن'} ${item.name} ${isSelected ? 'از' : 'به'} انتخاب‌ها" aria-pressed="${isSelected}" ${isFull?'disabled':''}>
        <svg class="ico"><use href="#${isSelected ? 'i-check' : 'i-plus'}"/></svg>
        ${isSelected?'انتخاب شده · حذف':isFull?'ظرفیت تکمیل شده':'افزودن به تسترها'}
      </button>
    </div>
  </article>`;
}

function renderCatalog() {
  const normalized = query.trim().toLocaleLowerCase();
  const filtered = PERFUMES.filter(p => {
    const genderOk = activeFilter === 'all' || p.gender === activeFilter;
    const searchable = [p.name, p.brand, p.en, p.gender, p.season, p.scent].join(' ').toLocaleLowerCase();
    return genderOk && (!normalized || searchable.includes(normalized));
  });
  $('#productGrid').innerHTML = filtered.map(cardTemplate).join('');
  $('#emptyResults').hidden = filtered.length > 0;
  $('#resultCount').textContent = `${farsiNumber(filtered.length)} رایحه`;
  $('#clearSearch').hidden = query.length === 0;
  $$('.pill').forEach(btn => {
    const active = btn.dataset.filter === activeFilter;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

function selectedRowTemplate(item) {
  return `<div class="selected-item" style="--artbg:${PALETTES[item.tone].bg}">
    <div class="selected-thumb"><img src="${bottleArt(item)}" alt="" /></div>
    <div class="selected-info"><strong>${escapeHtml(item.name)}</strong><span dir="ltr">${escapeHtml(item.brand)}</span></div>
    <button type="button" class="remove-selected" data-remove="${item.id}" aria-label="حذف ${item.name} از انتخاب‌ها"><svg class="ico"><use href="#i-x"/></svg></button>
  </div>`;
}

function selectedListTemplate() {
  if (!selected.length) return `<div class="selection-empty"><div class="selection-empty-icon"><svg class="ico"><use href="#i-bag"/></svg></div><strong>هنوز عطری انتخاب نکردی!</strong><p>عطرهایی که دوست داری رو از گرید به جعبه‌ات اضافه کن.</p></div>`;
  return selected.map(id => selectedRowTemplate(getProduct(id))).join('');
}

function renderSelection() {
  const n = selected.length;
  const remaining = MAX_TESTERS - n;
  $('#headerSelected').textContent = farsiNumber(n);
  $('#asideCount').textContent = farsiNumber(n);
  $('#mobileCount').textContent = farsiNumber(n);
  $('#remainingLabel').textContent = n === MAX_TESTERS ? 'جعبه تکمیل شد ✦' : `${farsiNumber(remaining)} جای خالی`;
  $('#mobileRemaining').textContent = n === MAX_TESTERS ? 'آماده مرور انتخاب‌ها' : `${farsiNumber(remaining)} انتخاب باقی‌مانده`;
  $('#finalizeHint').textContent = n === MAX_TESTERS ? 'عالی! هر ۱۰ رایحه رو انتخاب کردی.' : `برای ادامه، ${farsiNumber(remaining)} عطر دیگه انتخاب کن.`;
  $('#finalizeButton').disabled = n !== MAX_TESTERS;
  $('#mobileFinalize').disabled = n !== MAX_TESTERS;
  $('#selectedList').innerHTML = selectedListTemplate();
  $('#drawerContent').innerHTML = `<div class="selection-subheading"><strong>رایحه‌های انتخاب‌شده</strong><span>${farsiNumber(remaining)} جای خالی</span></div><div class="selected-list">${selectedListTemplate()}</div>`;
  $('#progressTrack').innerHTML = Array.from({ length: MAX_TESTERS }, (_, i) => `<span class="${i < n?'active':''}"></span>`).join('');
  $('#progressTrack').setAttribute('aria-valuenow', String(n));
  $('#mobileProgress').style.setProperty('--mobile-fill', `${n / MAX_TESTERS * 100}%`);
}

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove('show'), 2100);
}

function toggleProduct(id) {
  const item = getProduct(id);
  if (!item) return;
  const i = selected.indexOf(id);
  if (i >= 0) {
    selected.splice(i, 1);
    showToast(`«${item.name}» از جعبه انتخاب حذف شد`);
  } else if (selected.length < MAX_TESTERS) {
    selected.push(id);
    showToast(`«${item.name}» به جعبه انتخاب اضافه شد`);
  } else {
    showToast('هر سفارش فقط شامل ۱۰ تستر است');
  }
  renderCatalog();
  renderSelection();
  if (!$('#mobileDrawer').classList.contains('is-open')) {
    const button = $(`[data-toggle="${id}"]`);
    button?.focus({preventScroll:true});
  }
}

function openDrawer() {
  lastFocus = document.activeElement;
  $('#drawerBackdrop').hidden = false;
  const drawer = $('#mobileDrawer');
  drawer.inert = false;
  drawer.setAttribute('aria-hidden','false');
  drawer.classList.add('is-open');
  document.body.style.overflow = 'hidden';
  $('#closeDrawer').focus();
}
function closeDrawer() {
  const drawer = $('#mobileDrawer');
  drawer.classList.remove('is-open');
  drawer.setAttribute('aria-hidden','true');
  drawer.inert = true;
  $('#drawerBackdrop').hidden = true;
  document.body.style.overflow = '';
  lastFocus?.focus?.();
}
function openModal() {
  if (selected.length !== MAX_TESTERS) return;
  if ($('#mobileDrawer').classList.contains('is-open')) closeDrawer();
  lastFocus = document.activeElement;
  $('#modalSelected').innerHTML = selected.map(id => `<span>${escapeHtml(getProduct(id).name)}</span>`).join('');
  $('#modalBackdrop').hidden = false;
  const modal = $('#demoModal');
  modal.inert = false;
  modal.setAttribute('aria-hidden','false');
  document.body.style.overflow = 'hidden';
  $('#closeModal').focus();
}
function closeModal() {
  $('#modalBackdrop').hidden = true;
  const modal = $('#demoModal');
  modal.inert = true;
  modal.setAttribute('aria-hidden','true');
  document.body.style.overflow = '';
  lastFocus?.focus?.();
}

$('#productGrid').addEventListener('click', e => {
  const button = e.target.closest('[data-toggle]');
  if (button) toggleProduct(Number(button.dataset.toggle));
});
$('#selectedList').addEventListener('click', e => {
  const button = e.target.closest('[data-remove]');
  if (button) toggleProduct(Number(button.dataset.remove));
});
$('#drawerContent').addEventListener('click', e => {
  const button = e.target.closest('[data-remove]');
  if (button) toggleProduct(Number(button.dataset.remove));
});
$('#searchInput').addEventListener('input', e => { query = e.target.value; renderCatalog(); });
$('#clearSearch').addEventListener('click', () => { query = ''; $('#searchInput').value = ''; renderCatalog(); $('#searchInput').focus(); });
$('#filters').addEventListener('click', e => {
  const button = e.target.closest('[data-filter]');
  if (!button) return;
  activeFilter = button.dataset.filter;
  renderCatalog();
});
$('#resetFilters').addEventListener('click', () => {
  activeFilter = 'all'; query = ''; $('#searchInput').value = ''; renderCatalog();
});
$('#headerCount').addEventListener('click', () => {
  if (window.matchMedia('(max-width: 940px)').matches) openDrawer();
  else $('#selectionPanel').scrollIntoView({behavior:'smooth',block:'center'});
});
$('#openDrawer').addEventListener('click', openDrawer);
$('#closeDrawer').addEventListener('click', closeDrawer);
$('#drawerBackdrop').addEventListener('click', closeDrawer);
$('#finalizeButton').addEventListener('click', openModal);
$('#mobileFinalize').addEventListener('click', openModal);
$('#closeModal').addEventListener('click', closeModal);
$('#modalDone').addEventListener('click', closeModal);
$('#modalBackdrop').addEventListener('click', closeModal);
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  if ($('#demoModal').getAttribute('aria-hidden') === 'false') closeModal();
  else if ($('#mobileDrawer').getAttribute('aria-hidden') === 'false') closeDrawer();
});

renderCatalog();
renderSelection();

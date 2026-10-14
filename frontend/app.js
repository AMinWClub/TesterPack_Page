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
  {
    id: 1, name: 'رز مخملی', brand: 'MAISON ÉLLE', en: 'VELVET ROSE', gender: 'زنانه', season: 'فصول سرد', scent: 'گرم و شیرین', tone: 'rose', shape: 'classic', tag: 'پرطرفدار', year: 2021,
    descriptionText: 'رزهای نرم و مخملی با شیرینی لطیف وانیل؛ رایحه‌ای گرم و آرام برای عصرهای خنک و لحظه‌های صمیمی.',
    mainAccords: [{ name: 'floral', strength: 95, color: '#c88799' }, { name: 'sweet', strength: 82, color: '#d7b781' }, { name: 'powdery', strength: 65, color: '#b5a1c6' }, { name: 'woody', strength: 42, color: '#a88a6d' }],
    notesTop: ['bergamot', 'rose'], notesMiddle: ['rose', 'jasmine'], notesBase: ['vanilla', 'musk'], longevity: { average: 4.1 }, sillage: { average: 2.8 },
  },
  {
    id: 2, name: 'آبی بی‌انتها', brand: 'NOVA PARFUMS', en: 'BLUE INFINITY', gender: 'مردانه', season: 'چهار فصل', scent: 'خنک و تلخ', tone: 'blue', shape: 'tall', tag: 'انتخاب خاص', year: 2022,
    descriptionText: 'شروعی شفاف و مرکباتی که آرام‌آرام به چوب سدر می‌رسد؛ انتخابی تمیز و خنک برای همراهی روزمره.',
    mainAccords: [{ name: 'fresh', strength: 96, color: '#89b8c8' }, { name: 'citrus', strength: 80, color: '#d2bf76' }, { name: 'woody', strength: 68, color: '#a88a6d' }, { name: 'aromatic', strength: 50, color: '#98b19d' }],
    notesTop: ['bergamot', 'lemon'], notesMiddle: ['lavender', 'pepper'], notesBase: ['cedar', 'musk'], longevity: { average: 3.5 }, sillage: { average: 2.2 },
  },
  {
    id: 3, name: 'طلای کهربا', brand: 'ATELIER AMBRE', en: 'AMBER GOLD', gender: 'یونیسکس', season: 'فصول سرد', scent: 'گرم و شرقی', tone: 'amber', shape: 'square', tag: 'لوکس', year: 2020,
    descriptionText: 'ترکیبی طلایی از کهربا و وانیل با سایه‌ای چوبی؛ گرم، عمیق و مناسب شب‌های پاییزی.',
    mainAccords: [{ name: 'amber', strength: 98, color: '#cf9b57' }, { name: 'warm spicy', strength: 84, color: '#bf8b6d' }, { name: 'sweet', strength: 70, color: '#d7b781' }, { name: 'woody', strength: 55, color: '#a88a6d' }],
    notesTop: ['bergamot', 'pepper'], notesMiddle: ['amber', 'rose'], notesBase: ['vanilla', 'cedar'], longevity: { average: 4.6 }, sillage: { average: 3.4 },
  },
  {
    id: 4, name: 'شکوفه سپید', brand: 'MAISON ÉLLE', en: 'WHITE BLOOM', gender: 'زنانه', season: 'بهار و تابستان', scent: 'ملایم و گلی', tone: 'ivory', shape: 'round', tag: 'لطیف', year: 2023,
    descriptionText: 'شکوفه‌های روشن و لطیف در کنار مشک نرم؛ رایحه‌ای سبک برای روزهای آفتابی و شروع‌های تازه.',
    mainAccords: [{ name: 'white floral', strength: 94, color: '#b1bfa0' }, { name: 'fresh', strength: 76, color: '#89b8c8' }, { name: 'musky', strength: 54, color: '#b5b1b0' }],
    notesTop: ['bergamot'], notesMiddle: ['jasmine', 'rose'], notesBase: ['musk', 'vanilla'], longevity: { average: 2.8 }, sillage: { average: 1.7 },
  },
  {
    id: 5, name: 'ساحل نقره‌ای', brand: 'NOVA PARFUMS', en: 'SILVER COAST', gender: 'مردانه', season: 'فصول گرم', scent: 'خنک و مرکباتی', tone: 'sage', shape: 'square', tag: 'با‌طراوت', year: 2021,
    descriptionText: 'حس نسیم ساحلی با پوست مرکبات و چوب‌های روشن؛ شفاف و سرزنده برای روزهای گرم.',
    mainAccords: [{ name: 'citrus', strength: 97, color: '#d2bf76' }, { name: 'aquatic', strength: 85, color: '#8bbac1' }, { name: 'fresh', strength: 75, color: '#89b8c8' }, { name: 'woody', strength: 40, color: '#a88a6d' }],
    notesTop: ['lemon', 'bergamot'], notesMiddle: ['lavender'], notesBase: ['cedar', 'musk'], longevity: { average: 3.1 }, sillage: { average: 2.1 },
  },
  {
    id: 6, name: 'عود سلطنتی', brand: 'NOIR COLLECTION', en: 'ROYAL OUD', gender: 'یونیسکس', season: 'پاییز و زمستان', scent: 'تلخ و چوبی', tone: 'noir', shape: 'classic', tag: 'خاص', year: 2019,
    descriptionText: 'عود و چوب سدر با گرمای کهربا؛ رایحه‌ای عمیق و رسمی با شخصیتی آرام و قدرتمند.',
    mainAccords: [{ name: 'woody', strength: 97, color: '#a88a6d' }, { name: 'oud', strength: 88, color: '#95775d' }, { name: 'amber', strength: 65, color: '#cf9b57' }, { name: 'warm spicy', strength: 52, color: '#bf8b6d' }],
    notesTop: ['pepper', 'bergamot'], notesMiddle: ['oud', 'rose'], notesBase: ['cedar', 'amber'], longevity: { average: 4.7 }, sillage: { average: 3.6 },
  },
  {
    id: 7, name: 'رویای یاس', brand: 'FLEUR D’OR', en: 'JASMINE DREAM', gender: 'زنانه', season: 'بهار', scent: 'شیرین و گلی', tone: 'lilac', shape: 'tall', tag: 'دلنشین', year: 2022,
    descriptionText: 'یاس سفید در فضایی لطیف و شیرین؛ سبک و دلنشین، شبیه قدم‌زدن در باغی بهاری.',
    mainAccords: [{ name: 'white floral', strength: 96, color: '#b1bfa0' }, { name: 'sweet', strength: 73, color: '#d7b781' }, { name: 'powdery', strength: 56, color: '#b5a1c6' }],
    notesTop: ['bergamot'], notesMiddle: ['jasmine', 'iris'], notesBase: ['vanilla', 'musk'], longevity: { average: 3.3 }, sillage: { average: 2.3 },
  },
  {
    id: 8, name: 'ماجراجوی شب', brand: 'NOIR COLLECTION', en: 'NIGHT VOYAGE', gender: 'مردانه', season: 'چهار فصل', scent: 'تلخ و ادویه‌ای', tone: 'slate', shape: 'round', tag: 'جذاب', year: 2020,
    descriptionText: 'ادویه‌های تیره در کنار چوب و کهربا؛ رایحه‌ای پرشخصیت برای شب و قرارهای خاص.',
    mainAccords: [{ name: 'warm spicy', strength: 96, color: '#bf8b6d' }, { name: 'woody', strength: 85, color: '#a88a6d' }, { name: 'amber', strength: 66, color: '#cf9b57' }],
    notesTop: ['pepper', 'bergamot'], notesMiddle: ['lavender', 'cedar'], notesBase: ['amber', 'musk'], longevity: { average: 4 }, sillage: { average: 3.2 },
  },
  {
    id: 9, name: 'طلوع مرکبات', brand: 'ATELIER AMBRE', en: 'CITRUS SUN', gender: 'یونیسکس', season: 'فصول گرم', scent: 'خنک و ترش', tone: 'citrus', shape: 'classic', tag: 'انرژی‌بخش', year: 2024,
    descriptionText: 'درخشش لیمو و ترنج با پایانی تمیز و سبک؛ همراهی پرانرژی برای صبح‌های تابستانی.',
    mainAccords: [{ name: 'citrus', strength: 99, color: '#d2bf76' }, { name: 'fresh', strength: 83, color: '#89b8c8' }, { name: 'green', strength: 63, color: '#99b18b' }],
    notesTop: ['lemon', 'bergamot'], notesMiddle: ['jasmine'], notesBase: ['musk', 'cedar'], longevity: { average: 2.5 }, sillage: { average: 1.8 },
  },
  {
    id: 10, name: 'ابریشم صورتی', brand: 'FLEUR D’OR', en: 'PINK SILK', gender: 'زنانه', season: 'چهار فصل', scent: 'شیرین و پودری', tone: 'peach', shape: 'square', tag: 'محبوب', year: 2021,
    descriptionText: 'لطافت زنبق و رز با شیرینی ملایم وانیل؛ رایحه‌ای نزدیک به پوست، نرم و صمیمی.',
    mainAccords: [{ name: 'powdery', strength: 96, color: '#b5a1c6' }, { name: 'sweet', strength: 84, color: '#d7b781' }, { name: 'floral', strength: 70, color: '#c88799' }, { name: 'musky', strength: 55, color: '#b5b1b0' }],
    notesTop: ['bergamot', 'rose'], notesMiddle: ['iris', 'jasmine'], notesBase: ['vanilla', 'musk'], longevity: { average: 3.5 }, sillage: { average: 2.2 },
  },
  {
    id: 11, name: 'سدر آبی', brand: 'NOVA PARFUMS', en: 'CEDAR BLUE', gender: 'مردانه', season: 'پاییز و بهار', scent: 'چوبی و خنک', tone: 'teal', shape: 'tall', tag: 'مدرن', year: 2023,
    descriptionText: 'سدر روشن با طراوت مرکبات و هوایی سبز؛ ترکیبی متعادل برای استفاده روزمره و موقعیت‌های رسمی.',
    mainAccords: [{ name: 'woody', strength: 95, color: '#a88a6d' }, { name: 'fresh', strength: 82, color: '#89b8c8' }, { name: 'aromatic', strength: 67, color: '#98b19d' }],
    notesTop: ['bergamot', 'lemon'], notesMiddle: ['lavender', 'pepper'], notesBase: ['cedar', 'musk'], longevity: { average: 3.8 }, sillage: { average: 2.6 },
  },
  {
    id: 12, name: 'شب طلایی', brand: 'NOIR COLLECTION', en: 'GOLDEN NIGHT', gender: 'یونیسکس', season: 'فصول سرد', scent: 'گرم و ادویه‌ای', tone: 'plum', shape: 'round', tag: 'متمایز', year: 2022,
    descriptionText: 'گرمای ادویه و کهربا با پایانی نرم از وانیل؛ رایحه‌ای درخشان و دلپذیر برای شب‌های سرد.',
    mainAccords: [{ name: 'amber', strength: 97, color: '#cf9b57' }, { name: 'warm spicy', strength: 85, color: '#bf8b6d' }, { name: 'sweet', strength: 74, color: '#d7b781' }, { name: 'woody', strength: 53, color: '#a88a6d' }],
    notesTop: ['pepper', 'bergamot'], notesMiddle: ['amber', 'rose'], notesBase: ['vanilla', 'cedar'], longevity: { average: 4.4 }, sillage: { average: 3.3 },
  },
];

// Mock averages use 1–5 for longevity and 1–4 for sillage.
const SCENT_LABELS = {
  fruity: 'میوه‌ای', sweet: 'شیرین', powdery: 'پودری', woody: 'چوبی', floral: 'گلی',
  'white floral': 'گل‌های سفید', fresh: 'تازه', 'warm spicy': 'ادویه‌ای گرم',
  citrus: 'مرکباتی', amber: 'کهربایی', aromatic: 'معطر و گیاهی', musky: 'مشکی',
  aquatic: 'آبی', green: 'سبز', oud: 'عود', bergamot: 'ترنج', lemon: 'لیمو',
  rose: 'رز', jasmine: 'یاس', cedar: 'چوب سدر', vanilla: 'وانیل', musk: 'مشک',
  pepper: 'فلفل', iris: 'زنبق', lavender: 'اسطوخودوس',
  female: 'زنانه', male: 'مردانه', unisex: 'یونیسکس', spring: 'بهار', summer: 'تابستان',
  autumn: 'پاییز', winter: 'زمستان', 'all seasons': 'چهار فصل',
};
const NOTE_IMAGES = {
  rose: 'assets/notes/rose.svg', jasmine: 'assets/notes/jasmine.svg',
  bergamot: 'assets/notes/citrus.svg', lemon: 'assets/notes/citrus.svg',
  cedar: 'assets/notes/wood.svg', oud: 'assets/notes/wood.svg',
  vanilla: 'assets/notes/vanilla.svg', pepper: 'assets/notes/pepper.svg',
  iris: 'assets/notes/iris.svg', amber: 'assets/notes/amber.svg',
};

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
let perfumeId = null;
let perfumeReturnFocus = null;
let perfumePreviousOverflow = '';
let perfumeCloseTimer;
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
  return `<article class="product-card ${isSelected ? 'is-selected' : ''}" data-id="${item.id}" tabindex="0" aria-haspopup="dialog" aria-controls="perfumeDialog" aria-label="مشاهده جزئیات ${escapeHtml(item.name)}">
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
  syncPerfumeSelection();
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
  if ($('#perfumeDialog').open) {
    $('#perfumeSelect').focus({preventScroll:true});
  } else if (!$('#mobileDrawer').classList.contains('is-open')) {
    const button = $(`[data-toggle="${id}"]`);
    button?.focus({preventScroll:true});
  }
}

function scentLabel(value) {
  const text = String(value ?? '').trim();
  return SCENT_LABELS[text.toLowerCase()] || text;
}

function accordsTemplate(accords) {
  const valid = (Array.isArray(accords) ? accords : [])
    .filter(accord => accord?.name && Number.isFinite(Number(accord.strength)) && Number(accord.strength) > 0)
    .slice().sort((a, b) => Number(b.strength) - Number(a.strength));
  if (!valid.length) return '<p class="pd-empty">اطلاعات آکوردها هنوز موجود نیست.</p>';
  return `<ul class="pd-accords">${valid.map(accord => {
    const strength = Math.min(100, Number(accord.strength));
    const color = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(accord.color) ? accord.color : '#c9a961';
    return `<li class="pd-accord" style="--strength:${strength}%;--accord-color:${color}">
      <span>${escapeHtml(scentLabel(accord.name))}</span><small>${farsiNumber(Math.round(strength))}٪</small>
    </li>`;
  }).join('')}</ul>`;
}

function notesTemplate(item) {
  const groups = [['notesTop', 'نت‌های ابتدایی'], ['notesMiddle', 'نت‌های میانی'], ['notesBase', 'نت‌های پایه']];
  const content = groups.map(([field, label]) => {
    const notes = (Array.isArray(item[field]) ? item[field] : [])
      .map(note => typeof note === 'string' ? { name: note, image: NOTE_IMAGES[note] } : note)
      .filter(note => note?.name);
    if (!notes.length) return '';
    return `<section class="pd-note-group"><h4>${label}</h4><ul class="pd-note-list">${notes.map(note =>
      `<li class="pd-note">${note.image ? `<img src="${escapeHtml(note.image)}" alt="" width="36" height="36" />` : ''}<span>${escapeHtml(scentLabel(note.name))}</span></li>`
    ).join('')}</ul></section>`;
  }).join('');
  return content ? `<section><h3 class="pd-section-title">سفر نت‌ها</h3><div class="pd-notes">${content}</div></section>` : '';
}

function meterTemplate(title, summary, labels) {
  const average = Number(summary?.average);
  const known = Number.isFinite(average) && average >= 1 && average <= labels.length;
  const level = known ? Math.round(average) : 0;
  const label = known ? labels[level - 1] : 'هنوز مشخص نیست';
  return `<section class="pd-meter">
    <div class="pd-meter-head"><strong>${title}</strong><span>${label}</span></div>
    ${known ? `<div class="pd-meter-track" role="meter" aria-label="${title}" aria-valuemin="1" aria-valuemax="${labels.length}" aria-valuenow="${average}" aria-valuetext="${label}">
      ${labels.map((_, index) => `<i aria-hidden="true" class="${index < level ? 'is-active' : ''} ${index === level - 1 ? 'is-current' : ''}"></i>`).join('')}
    </div><div class="pd-meter-range" aria-hidden="true"><span>${labels[0]}</span><span>${labels.at(-1)}</span></div>` : ''}
  </section>`;
}

function renderPerfumeDetails(item) {
  const meta = [['جنسیت', item.gender], ['فصل', item.season], ['طبع', item.scent]]
    .filter(([, value]) => value).map(([label, value]) => `<span><small>${label}</small>${escapeHtml(scentLabel(value))}</span>`).join('');
  $('#perfumeContent').innerHTML = `
    <section class="pd-intro">
      <div class="pd-identity">
        <div class="pd-photo" style="--artbg:${PALETTES[item.tone].bg}"><img src="${bottleArt(item)}" alt="تصویر نمایشی ${escapeHtml(item.name)}" /></div>
        <div class="pd-heading"><div class="pd-brand" dir="ltr">${escapeHtml(item.brand)}</div>
          <h2 class="pd-title" id="perfumeTitle">${escapeHtml(item.name)}</h2>
          ${item.en ? `<div class="pd-english" dir="ltr">${escapeHtml(item.en)}</div>` : ''}
          ${item.year ? `<span class="pd-year">سال معرفی · ${escapeHtml(Number(item.year).toLocaleString('fa-IR', {useGrouping:false}))}</span>` : ''}
        </div>
      </div>
      ${meta ? `<div class="pd-meta">${meta}</div>` : ''}
      ${item.descriptionText ? `<p class="pd-description">${escapeHtml(item.descriptionText)}</p>` : ''}
      <div class="pd-meters">
        ${meterTemplate('ماندگاری', item.longevity, ['خیلی ضعیف', 'ضعیف', 'متوسط', 'ماندگار', 'بسیار ماندگار'])}
        ${meterTemplate('پخش بو', item.sillage, ['ملایم', 'متوسط', 'محسوس', 'قوی'])}
      </div>
    </section>
    <div class="pd-analysis">
      <section><h3 class="pd-section-title">آکوردهای اصلی</h3><p class="pd-section-hint">هویت رایحه، از پررنگ‌ترین تا لطیف‌ترین</p>${accordsTemplate(item.mainAccords)}</section>
      ${notesTemplate(item)}
    </div>`;
}

function syncPerfumeSelection() {
  if (!$('#perfumeDialog').open || perfumeId === null) return;
  const item = getProduct(perfumeId);
  const isSelected = selectedHas(perfumeId);
  const full = selected.length >= MAX_TESTERS && !isSelected;
  const button = $('#perfumeSelect');
  button.disabled = full;
  button.classList.toggle('is-selected', isSelected);
  button.setAttribute('aria-pressed', String(isSelected));
  button.setAttribute('aria-label', `${isSelected ? 'حذف' : 'افزودن'} ${item.name} ${isSelected ? 'از انتخاب‌ها' : 'به تسترهای من'}`);
  button.innerHTML = `<svg class="ico" aria-hidden="true"><use href="#${isSelected ? 'i-check' : 'i-plus'}"/></svg>${isSelected ? 'حذف از انتخاب‌ها' : full ? 'ظرفیت ۱۰ تستر تکمیل است' : 'افزودن به تسترهای من'}`;
  $('#perfumeSelectionStatus').textContent = `${isSelected ? 'در انتخاب‌های شما · ' : ''}${farsiNumber(selected.length)} از ${farsiNumber(MAX_TESTERS)} تستر انتخاب شده`;
}

function openPerfume(id) {
  const item = getProduct(id);
  const dialog = $('#perfumeDialog');
  if (!item || dialog.open) return;
  perfumeId = id;
  perfumeReturnFocus = document.activeElement;
  perfumePreviousOverflow = document.body.style.overflow;
  renderPerfumeDetails(item);
  dialog.showModal();
  document.body.style.overflow = 'hidden';
  syncPerfumeSelection();
}

function closePerfume() {
  const dialog = $('#perfumeDialog');
  if (!dialog.open || dialog.classList.contains('is-closing')) return;
  dialog.classList.add('is-closing');
  const delay = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 160;
  perfumeCloseTimer = setTimeout(() => dialog.close(), delay);
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
  if (button) {
    toggleProduct(Number(button.dataset.toggle));
    return;
  }
  const card = e.target.closest('.product-card');
  if (card) {
    card.focus({preventScroll:true});
    openPerfume(Number(card.dataset.id));
  }
});
$('#productGrid').addEventListener('keydown', event => {
  if (!event.target.matches('.product-card') || !['Enter', ' '].includes(event.key)) return;
  event.preventDefault();
  openPerfume(Number(event.target.dataset.id));
});
$('#perfumeSelect').addEventListener('click', () => {
  if (perfumeId !== null) toggleProduct(perfumeId);
});
$('#closePerfume').addEventListener('click', closePerfume);
$('#perfumeDialog').addEventListener('keydown', event => {
  if (event.key !== 'Tab') return;
  const controls = $$('button:not(:disabled), [tabindex="0"]', $('#perfumeDialog'));
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});
$('#perfumeDialog').addEventListener('cancel', event => {
  event.preventDefault();
  closePerfume();
});
$('#perfumeDialog').addEventListener('click', event => {
  const dialog = $('#perfumeDialog');
  const rect = dialog.getBoundingClientRect();
  const outside = event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
  if (event.target === dialog && outside) closePerfume();
});
$('#perfumeDialog').addEventListener('error', event => {
  if (event.target.matches('.pd-note img')) event.target.hidden = true;
}, true);
$('#perfumeDialog').addEventListener('close', () => {
  clearTimeout(perfumeCloseTimer);
  $('#perfumeDialog').classList.remove('is-closing');
  document.body.style.overflow = perfumePreviousOverflow;
  const target = perfumeReturnFocus?.isConnected ? perfumeReturnFocus : $(`.product-card[data-id="${perfumeId}"]`) || $('#searchInput');
  perfumeId = null;
  target?.focus({preventScroll:true});
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
  if ($('#perfumeDialog').open) return;
  if ($('#demoModal').getAttribute('aria-hidden') === 'false') closeModal();
  else if ($('#mobileDrawer').getAttribute('aria-hidden') === 'false') closeDrawer();
});

renderCatalog();
renderSelection();

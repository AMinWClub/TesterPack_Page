/** Real catalog and token-linked order selection; tokens stay out of API URLs. */
const MAX_TESTERS = 10;
const PAGE_SIZE = 24;
let PERFUMES = [];
const productsByPid = new Map();
const detailCache = new Map();
const detailRequests = new Map();
const unavailablePids = new Set();
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const farsiNumber = n => Number(n).toLocaleString('fa-IR');
const selected = [];
let activeFilter = 'all';
let activeSeason = 'all';
const activeAccords = new Set();
let accordOptions = [];
const SEASON_LABELS = { spring: 'بهار', summer: 'تابستان', autumn: 'پاییز', winter: 'زمستان' };
let query = '';
let toastTimer;
let lastFocus = null;
let perfumeId = null;
let perfumeReturnFocus = null;
let perfumePreviousOverflow = '';
let perfumeCloseTimer;
let perfumeRequestVersion = 0;
let catalogState = 'loading';
let catalogLoading = false;
let visibleCount = PAGE_SIZE;
let filteredProducts = [];
const PLACEHOLDER = '/frontend/assets/perfume-placeholder.svg';
const selectionToken = (() => {
  try { return /^\/([A-Za-z0-9_-]{18})\/?$/.exec(decodeURIComponent(location.pathname))?.[1] || ''; }
  catch { return ''; }
})();
const isSelectionPage = Boolean(selectionToken);
let selectionState = isSelectionPage ? 'loading' : 'preview';
let submitBusy = false;
let contextBusy = false;
let submissionPids = null;
let submittedNames = [];
let selectionDeadline = 0;
let selectionExpiryTimer;
const deadlinePassed = () => isSelectionPage && performance.now() >= selectionDeadline;
const canEditSelection = () => ['pending', 'preview'].includes(selectionState) && !submitBusy && !deadlinePassed();

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function normalizeSearch(value) {
  return String(value ?? '').normalize('NFKC').toLowerCase()
    .replace(/[يى]/g, 'ی').replace(/ك/g, 'ک')
    .replace(/[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]/g, '')
    .replace(/[\s\u200c\u200d]+/g, ' ').trim();
}

function safeImageUrl(value) {
  if (typeof value !== 'string' || /[\s\\\u0000-\u001f]/.test(value)) return '';
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || !url.hostname.includes('.') || /^[\d.]+$/.test(url.hostname) || url.hostname.startsWith('[') || /(^|\.)(localhost|local|internal)$/.test(url.hostname)) return '';
    return url.href;
  } catch { return ''; }
}

function productImage(item, alt = '') {
  return `<img src="${escapeHtml(safeImageUrl(item.imageUrl) || PLACEHOLDER)}" alt="${escapeHtml(alt)}" data-product-image loading="lazy" decoding="async" referrerpolicy="no-referrer" />`;
}

const selectedHas = pid => selected.includes(String(pid));
function getProduct(pid) { return productsByPid.get(String(pid)); }
function displayName(item) { return String(item.name || '').split('|')[0].trim(); }

async function apiJson(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal, cache: 'no-store', headers: { Accept: 'application/json', ...options.headers } });
    if (!response.ok) {
      const error = new Error('API request failed');
      error.status = response.status;
      try {
        const body = await response.json();
        error.code = body.detail?.code;
        if (typeof body.detail?.message === 'string') error.message = body.detail.message;
      } catch { /* Keep the generic error if the response is not JSON. */ }
      throw error;
    }
    return await response.json();
  } finally { clearTimeout(timer); }
}

function showCatalogState(state) {
  catalogState = state;
  const status = $('#catalogStatus');
  status.hidden = state === 'ready';
  $('#productGrid').hidden = state !== 'ready';
  $('#catalogMore').hidden = true;
  $('#emptyResults').hidden = true;
  $('#productGrid').setAttribute('aria-busy', String(state === 'loading'));
  const messages = {
    loading: ['در حال دریافت رایحه‌ها', 'کاتالوگ عطرهای قابل انتخاب در حال بارگذاری است.'],
    error: ['دریافت کاتالوگ ممکن نشد', 'اتصال را بررسی کنید و دوباره تلاش کنید.'],
    empty: ['فعلاً عطری برای انتخاب موجود نیست', 'پس از به‌روزرسانی موجودی دوباره سر بزنید.'],
  };
  if (messages[state]) {
    const [title, message] = messages[state];
    status.innerHTML = `<strong>${title}</strong><p>${message}</p>${state === 'error' ? '<button type="button" id="retryCatalog">تلاش دوباره</button>' : ''}`;
    $('#resultCount').textContent = state === 'loading' ? 'در حال دریافت…' : state === 'empty' ? '۰ رایحه' : '—';
  }
}

async function loadCatalog() {
  if (catalogLoading) return;
  catalogLoading = true;
  showCatalogState('loading');
  try {
    const data = await apiJson('/api/tester/catalog');
    if (!Array.isArray(data.products) || data.total !== data.products.length) throw new Error('Invalid catalog');
    const products = data.products.map(raw => {
      if ((typeof raw.pid !== 'string' && !Number.isSafeInteger(raw.pid)) || String(raw.pid).trim() === '') throw new Error('Invalid pid');
      const searchText = normalizeSearch([raw.name, raw.label, raw.fragranticaName, raw.englishName, raw.brand, raw.sourceBrand, raw.season, raw.scent].join(' '));
      return { ...raw, pid: String(raw.pid), searchText, searchCompact: searchText.replaceAll(' ', ''),
        seasonKeys: Array.isArray(raw.seasonKeys) ? raw.seasonKeys.filter(key => Object.hasOwn(SEASON_LABELS, key)) : [],
        accordKeys: Array.isArray(raw.accordKeys) ? raw.accordKeys.filter(key => typeof key === 'string' && key.trim()) : [] };
    });
    const previousSelections = selected.map(getProduct).filter(Boolean);
    productsByPid.clear();
    products.forEach(product => productsByPid.set(product.pid, product));
    previousSelections.forEach(product => {
      if (!productsByPid.has(product.pid)) { productsByPid.set(product.pid, product); unavailablePids.add(product.pid); }
    });
    PERFUMES = products;
    const keys = new Set(products.flatMap(product => product.accordKeys));
    accordOptions = [...keys].map(key => ({ key, label: typeof data.accordLabels?.[key] === 'string' ? data.accordLabels[key] : key }))
      .sort((a, b) => a.label.localeCompare(b.label, 'fa'));
    renderAccordOptions();
    showCatalogState(products.length ? 'ready' : 'empty');
    renderCatalog();
    renderSelection();
  } catch {
    showCatalogState('error');
  } finally { catalogLoading = false; }
}

function cardTemplate(item) {
  const isSelected = selectedHas(item.pid);
  const isFull = !canEditSelection() || ((selected.length >= MAX_TESTERS || unavailablePids.has(item.pid)) && !isSelected);
  return `<article class="product-card ${isSelected ? 'is-selected' : ''}" data-id="${escapeHtml(item.pid)}" tabindex="0" aria-haspopup="dialog" aria-controls="perfumeDialog" aria-label="مشاهده جزئیات ${escapeHtml(item.name)}">
    <div class="product-visual">
      ${unavailablePids.has(item.pid) ? '<span class="product-tag">ناموجود</span>' : ''}
      <span class="product-selected-mark" aria-label="انتخاب شده"><svg class="ico"><use href="#i-check"/></svg></span>
      ${productImage(item, item.imageUrl ? item.name : 'تصویر عطر موجود نیست')}
    </div>
    <div class="product-body">
      <div class="product-brand" dir="auto">${escapeHtml(item.brand || 'برند نامشخص')}</div>
      <h3 class="product-name" title="${escapeHtml(item.name)}">${escapeHtml(displayName(item))}</h3>
      <div class="product-details">
        <div class="product-detail"><span class="product-detail-label">جنسیت</span><span class="product-detail-value">${escapeHtml(item.gender || 'نامشخص')}</span></div>
        <div class="product-detail"><span class="product-detail-label">مناسب فصل</span><span class="product-detail-value">${escapeHtml(item.season || 'نامشخص')}</span></div>
        <div class="product-detail"><span class="product-detail-label">رایحه</span><span class="product-detail-value">${escapeHtml(item.scent || 'نامشخص')}</span></div>
      </div>
      <button class="product-button" data-toggle="${escapeHtml(item.pid)}" type="button" aria-label="${isSelected ? 'حذف' : 'افزودن'} ${escapeHtml(item.name)} ${isSelected ? 'از' : 'به'} انتخاب‌ها" aria-pressed="${isSelected}" ${isFull ? 'disabled' : ''}>
        <svg class="ico"><use href="#${isSelected ? 'i-check' : 'i-plus'}"/></svg>
        ${isSelected ? 'انتخاب شده · حذف' : unavailablePids.has(item.pid) ? 'فعلاً ناموجود' : isFull ? 'ظرفیت تکمیل شده' : 'افزودن به تسترها'}
      </button>
    </div>
  </article>`;
}

function appendProducts() {
  const grid = $('#productGrid');
  const start = grid.children.length;
  grid.insertAdjacentHTML('beforeend', filteredProducts.slice(start, visibleCount).map(cardTemplate).join(''));
  $('#catalogMore').hidden = visibleCount >= filteredProducts.length;
}

function renderCatalog(reset = true) {
  if (catalogState !== 'ready') return;
  if (reset) visibleCount = PAGE_SIZE;
  const normalized = normalizeSearch(query);
  const compact = normalized.replaceAll(' ', '');
  const tokens = normalized.split(' ').filter(Boolean);
  filteredProducts = PERFUMES.filter(p => (activeFilter === 'all' || p.genderGroup === activeFilter)
    && (activeSeason === 'all' || p.seasonKeys.includes(activeSeason))
    && (!activeAccords.size || p.accordKeys.some(key => activeAccords.has(key)))
    && (!normalized || tokens.every(token => p.searchText.includes(token)) || p.searchCompact.includes(compact)));
  $('#productGrid').innerHTML = '';
  appendProducts();
  $('#emptyResults').hidden = filteredProducts.length > 0;
  $('#resultCount').textContent = `${farsiNumber(filteredProducts.length)} رایحه`;
  $('#clearSearch').hidden = query.length === 0;
  $$('.pill').forEach(btn => {
    const active = btn.dataset.filter === activeFilter;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
  renderActiveFilters();
}

function renderAccordOptions() {
  const search = normalizeSearch($('#accordSearch').value);
  const options = accordOptions.filter(option => normalizeSearch(`${option.label} ${option.key}`).includes(search));
  $('#accordOptions').innerHTML = options.map(({ key, label }) => `<label class="accord-option"><input type="checkbox" value="${escapeHtml(key)}" ${activeAccords.has(key) ? 'checked' : ''} /><span>${escapeHtml(label)}</span></label>`).join('');
  $('#accordEmpty').hidden = options.length > 0;
}

function renderActiveFilters() {
  const tags = [];
  if (query) tags.push(['query', '', `جست‌وجو: ${query}`]);
  if (activeFilter !== 'all') tags.push(['gender', '', activeFilter]);
  if (activeSeason !== 'all') tags.push(['season', '', SEASON_LABELS[activeSeason]]);
  activeAccords.forEach(key => tags.push(['accord', key, accordOptions.find(option => option.key === key)?.label || key]));
  $('#activeCatalogFilters').hidden = tags.length === 0;
  $('#activeFilterTags').innerHTML = tags.map(([group, key, label]) => `<button type="button" class="active-filter-tag" data-clear-group="${group}" data-clear-key="${escapeHtml(key)}" aria-label="حذف فیلتر ${escapeHtml(label)}">${escapeHtml(label)} <span aria-hidden="true">×</span></button>`).join('');
  $('#seasonFilter').value = activeSeason;
  $('#accordFilterCount').textContent = activeAccords.size ? farsiNumber(activeAccords.size) : '';
  $$('#accordOptions input').forEach(input => { input.checked = activeAccords.has(input.value); });
}

function resetCatalogFilters() {
  activeFilter = 'all'; activeSeason = 'all'; activeAccords.clear(); query = '';
  $('#searchInput').value = ''; $('#accordSearch').value = '';
  $('#accordFilter').open = false;
  renderAccordOptions(); renderActiveFilters(); renderCatalog();
}

function selectedRowTemplate(item) {
  return `<div class="selected-item">
    <div class="selected-thumb">${productImage(item)}</div>
    <div class="selected-info"><strong>${escapeHtml(displayName(item))}</strong><span dir="auto">${escapeHtml(item.brand || 'برند نامشخص')}</span></div>
    <button type="button" class="remove-selected" data-remove="${escapeHtml(item.pid)}" ${canEditSelection() ? '' : 'disabled'} aria-label="حذف ${escapeHtml(item.name)} از انتخاب‌ها"><svg class="ico"><use href="#i-x"/></svg></button>
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
  $('#finalizeButton').disabled = n !== MAX_TESTERS || selectionState !== 'pending' || submitBusy || deadlinePassed();
  $('#mobileFinalize').disabled = $('#finalizeButton').disabled;
  if (selectionState === 'preview') $('#finalizeHint').textContent = 'برای ثبت واقعی، لینک اختصاصی پیامک را باز کنید.';
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
function cleanText(text) {
  return text.replace(/[^آ-یءؤئ۰-۹0-9 ]/g, '');
}
function toggleProduct(id) {
  if (!canEditSelection()) return;
  id = String(id);
  const item = getProduct(id);
  if (!item) return;
  const i = selected.indexOf(id);
  if (i >= 0) {
    selected.splice(i, 1);
    showToast(`«${cleanText(item.name).trimEnd()}» از جعبه انتخاب حذف شد`);
  } else if (selected.length < MAX_TESTERS && !unavailablePids.has(id)) {
    selected.push(id);
    showToast(`«${cleanText(item.name).trimEnd()}» به جعبه انتخاب اضافه شد`);
  } else {
    showToast('هر سفارش فقط شامل ۱۰ تستر است');
  }
  renderCatalog(false);
  renderSelection();
  if ($('#perfumeDialog').open) {
    $('#perfumeSelect').focus({preventScroll:true});
  } else if (!$('#mobileDrawer').classList.contains('is-open')) {
    const button = $(`[data-toggle="${CSS.escape(id)}"]`);
    button?.focus({preventScroll:true});
  }
}

function accordsTemplate(accords) {
  const valid = (Array.isArray(accords) ? accords : [])
    .filter(accord => accord?.name && Number.isFinite(Number(accord.strength)) && Number(accord.strength) > 0)
    .slice().sort((a, b) => Number(b.strength) - Number(a.strength));
  if (!valid.length) return '<p class="pd-empty">اطلاعات رایحه‌ها هنوز موجود نیست.</p>';
  return `<ul class="pd-accords">${valid.map(accord => {
    const strength = Number(accord.strength);
    const color = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(accord.color) ? accord.color : '#c9a961';
    return `<li class="pd-accord" style="--strength:${Math.min(100, strength)}%;--accord-color:${color}">
      <span>${escapeHtml(accord.name)}</span><small>${farsiNumber(strength)}٪</small>
    </li>`;
  }).join('')}</ul>`;
}

function notesTemplate(item) {
  const groups = [['notesTop', 'نت‌های ابتدایی'], ['notesMiddle', 'نت‌های میانی'], ['notesBase', 'نت‌های پایه']];
  const content = groups.map(([field, label]) => {
    const notes = (Array.isArray(item[field]) ? item[field] : [])
      .filter(note => note?.name);
    if (!notes.length) return '';
    return `<section class="pd-note-group"><h4>${label}</h4><ul class="pd-note-list">${notes.map(note =>
      `<li class="pd-note">${safeImageUrl(note.image) ? `<img src="${escapeHtml(safeImageUrl(note.image))}" alt="" width="36" height="36" loading="lazy" decoding="async" referrerpolicy="no-referrer" />` : ''}<span>${escapeHtml(note.name)}</span></li>`
    ).join('')}</ul></section>`;
  }).join('');
  return content ? `<section><h3 class="pd-section-title">نت‌ها</h3><div class="pd-notes">${content}</div></section>` : '';
}

function meterTemplate(title, summary) {
  const labels = Array.isArray(summary?.scale) ? summary.scale : [];
  const level = summary?.level;
  const known = Number.isInteger(level) && level >= 1 && level <= labels.length;
  const label = known ? summary.label : 'نامشخص';
  return `<section class="pd-meter">
    <div class="pd-meter-head"><strong>${title}</strong><span>${escapeHtml(label)}</span></div>
    ${known ? `<div class="pd-meter-track" role="meter" aria-label="${title}" aria-valuemin="1" aria-valuemax="${labels.length}" aria-valuenow="${level}" aria-valuetext="${escapeHtml(label)}">
      ${labels.map((_, index) => `<i aria-hidden="true" class="${index < level ? 'is-active' : ''} ${index === level - 1 ? 'is-current' : ''}"></i>`).join('')}
    </div><div class="pd-meter-range" aria-hidden="true"><span>${escapeHtml(labels[0])}</span><span>${escapeHtml(labels.at(-1))}</span></div>` : ''}
  </section>`;
}

function descriptiveTagsTemplate(tags) {
  const valid = (Array.isArray(tags) ? tags : []).filter(tag => typeof tag?.label === 'string' && tag.label.trim());
  if (!valid.length) return '';
  return `<section class="pd-traits" aria-labelledby="perfumeTraitsTitle">
    <h3 class="pd-section-title" id="perfumeTraitsTitle">حال‌وهوای این عطر</h3>
    <ul class="pd-trait-list">${valid.map(tag => `<li class="pd-trait"><span dir="auto">${escapeHtml(tag.label)}</span></li>`).join('')}</ul>
  </section>`;
}

function renderPerfumeDetails(item) {
  const meta = [['جنسیت ', item.gender], ['فصل ', item.season], ['طبع ', item.scent]]
    .filter(([, value]) => value).map(([label, value]) => `<span><small>${label}</small>${escapeHtml(value)}</span>`).join('');
  $('#perfumeContent').dataset.state = 'ready';
  $('#perfumeContent').setAttribute('aria-busy', 'false');
  $('#perfumeContent').innerHTML = `
    <section class="pd-intro">
      <div class="pd-identity">
        <div class="pd-photo">${productImage(item, item.imageUrl ? item.name : 'تصویر عطر موجود نیست')}</div>
        <div class="pd-heading"><div class="pd-brand" dir="auto">${escapeHtml(item.brand || 'برند نامشخص')}</div>
          <h2 class="pd-title" id="perfumeTitle" title="${escapeHtml(item.name)}">${escapeHtml(displayName(item))}</h2>
          ${item.englishName ? `<div class="pd-english" dir="ltr">${escapeHtml(item.englishName)}</div>` : ''}
          ${item.year ? `<span class="pd-year">سال معرفی · ${escapeHtml(Number(item.year).toLocaleString('fa-IR', {useGrouping:false}))}</span>` : ''}
        </div>
      </div>
      ${meta ? `<div class="pd-meta">${meta}</div>` : ''}
      <div class="pd-meters">
        ${meterTemplate('ماندگاری', item.longevity)}
        ${meterTemplate('پخش بو', item.sillage)}
      </div>
      ${descriptiveTagsTemplate(item.descriptiveTags)}
    </section>
    <div class="pd-analysis">
      <section><h3 class="pd-section-title">رایحه‌های اصلی</h3><p class="pd-section-hint">هویت رایحه، از پررنگ‌ترین تا لطیف‌ترین</p>${accordsTemplate(item.mainAccords)}</section>
      ${notesTemplate(item)}
    </div>`;
}

function syncPerfumeSelection() {
  if (!$('#perfumeDialog').open || perfumeId === null) return;
  const item = getProduct(perfumeId);
  const isSelected = selectedHas(perfumeId);
  const full = !canEditSelection() || ((selected.length >= MAX_TESTERS || unavailablePids.has(perfumeId)) && !isSelected);
  const button = $('#perfumeSelect');
  button.disabled = full;
  button.classList.toggle('is-selected', isSelected);
  button.setAttribute('aria-pressed', String(isSelected));
  button.setAttribute('aria-label', `${isSelected ? 'حذف' : 'افزودن'} ${item.name} ${isSelected ? 'از انتخاب‌ها' : 'به تسترهای من'}`);
  button.innerHTML = `<svg class="ico" aria-hidden="true"><use href="#${isSelected ? 'i-check' : 'i-plus'}"/></svg>${isSelected ? 'حذف از انتخاب‌ها' : unavailablePids.has(perfumeId) ? 'فعلاً قابل انتخاب نیست' : full ? 'ظرفیت ۱۰ تستر تکمیل است' : 'افزودن به تسترهای من'}`;
  $('#perfumeSelectionStatus').textContent = `${isSelected ? 'در انتخاب‌های شما · ' : ''}${farsiNumber(selected.length)} از ${farsiNumber(MAX_TESTERS)} تستر انتخاب شده`;
}

function renderPerfumeState(item, state, unavailable = false) {
  const content = $('#perfumeContent');
  content.dataset.state = state;
  content.setAttribute('aria-busy', String(state === 'loading'));
  content.innerHTML = `<div class="catalog-status"><h2 class="pd-title" id="perfumeTitle">${escapeHtml(item.name)}</h2>
    <p>${state === 'loading' ? 'در حال دریافت جزئیات رایحه…' : unavailable ? 'این عطر دیگر در کاتالوگ قابل انتخاب نیست.' : 'دریافت جزئیات عطر ممکن نشد؛ دوباره تلاش کنید.'}</p>
    ${state === 'error' ? '<button type="button" id="retryPerfume">تلاش دوباره</button>' : ''}</div>`;
}

async function fetchPerfume(pid) {
  if (detailCache.has(pid)) return detailCache.get(pid);
  if (detailRequests.has(pid)) return detailRequests.get(pid);
  const request = apiJson(`/api/tester/perfumes/${encodeURIComponent(pid)}`).then(data => {
    if (String(data.pid) !== pid) throw new Error('Invalid perfume identity');
    detailCache.set(pid, data);
    return data;
  }).finally(() => detailRequests.delete(pid));
  detailRequests.set(pid, request);
  return request;
}

async function loadPerfumeDetails(pid) {
  const version = ++perfumeRequestVersion;
  const item = getProduct(pid);
  renderPerfumeState(item, 'loading');
  try {
    const detail = await fetchPerfume(pid);
    if (perfumeId !== pid || version !== perfumeRequestVersion || !$('#perfumeDialog').open || $('#perfumeDialog').classList.contains('is-closing')) return;
    const restoreFocus = $('#perfumeContent').contains(document.activeElement);
    if (unavailablePids.delete(pid)) renderCatalog(false);
    renderPerfumeDetails(detail);
    syncPerfumeSelection();
    if (restoreFocus) $('#perfumeContent').focus({preventScroll:true});
  } catch (error) {
    if (perfumeId !== pid || version !== perfumeRequestVersion || !$('#perfumeDialog').open || $('#perfumeDialog').classList.contains('is-closing')) return;
    if (error.status === 404) { unavailablePids.add(pid); renderCatalog(false); }
    renderPerfumeState(item, 'error', error.status === 404);
    syncPerfumeSelection();
  }
}

function openPerfume(id) {
  id = String(id);
  const item = getProduct(id);
  const dialog = $('#perfumeDialog');
  if (!item || dialog.open) return;
  perfumeId = id;
  perfumeReturnFocus = document.activeElement;
  perfumePreviousOverflow = document.body.style.overflow;
  renderPerfumeState(item, 'loading');
  dialog.showModal();
  document.body.style.overflow = 'hidden';
  syncPerfumeSelection();
  loadPerfumeDetails(id);
}

function closePerfume() {
  const dialog = $('#perfumeDialog');
  if (!dialog.open || dialog.classList.contains('is-closing')) return;
  dialog.classList.add('is-closing');
  const delay = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 160;
  perfumeCloseTimer = setTimeout(() => {
    dialog.close();
    finishPerfumeClose();
  }, delay);
}

function finishPerfumeClose() {
  if (perfumeId === null) return;
  clearTimeout(perfumeCloseTimer);
  $('#perfumeDialog').classList.remove('is-closing');
  document.body.style.overflow = perfumePreviousOverflow;
  const target = perfumeReturnFocus?.isConnected ? perfumeReturnFocus : $(`.product-card[data-id="${CSS.escape(perfumeId)}"]`) || $('#searchInput');
  perfumeId = null;
  target?.focus({preventScroll:true});
}

function openDrawer() {
  if (!['pending', 'preview', 'submitting', 'uncertain'].includes(selectionState)) return;
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
  if (selected.length !== MAX_TESTERS || selectionState !== 'pending' || submitBusy || deadlinePassed()) return;
  if ($('#mobileDrawer').classList.contains('is-open')) closeDrawer();
  lastFocus = document.activeElement;
  $('#modalSelected').innerHTML = selected.map(id => `<li>${escapeHtml(getProduct(id).name)}</li>`).join('');
  $('#modalTitle').textContent = 'مرور نهایی انتخاب‌ها';
  $('#modalDescription').textContent = 'این ۱۰ عطر برای پک تستر شما ثبت می‌شوند. پس از تأیید، امکان تغییر انتخاب‌ها وجود ندارد.';
  $('#modalDone').textContent = 'تأیید و ثبت نهایی';
  $('#modalDone').disabled = false;
  $('#modalBack').hidden = false;
  $('#submissionMessage').hidden = true;
  $('#modalBackdrop').hidden = false;
  const modal = $('#demoModal');
  modal.inert = false;
  modal.setAttribute('aria-hidden','false');
  document.body.style.overflow = 'hidden';
  $('#closeModal').focus();
}
function closeModal() {
  if (submitBusy) return;
  $('#modalBackdrop').hidden = true;
  const modal = $('#demoModal');
  modal.inert = true;
  modal.setAttribute('aria-hidden','true');
  document.body.style.overflow = '';
  if (selectionState === 'submitted') $('#submittedTitle').focus({preventScroll:true});
  else lastFocus?.focus?.();
}

$('#productGrid').addEventListener('click', e => {
  const button = e.target.closest('[data-toggle]');
  if (button) {
    toggleProduct(button.dataset.toggle);
    return;
  }
  const card = e.target.closest('.product-card');
  if (card) {
    card.focus({preventScroll:true});
    openPerfume(card.dataset.id);
  }
});
$('#productGrid').addEventListener('keydown', event => {
  if (!event.target.matches('.product-card') || !['Enter', ' '].includes(event.key)) return;
  event.preventDefault();
  openPerfume(event.target.dataset.id);
});
$('#perfumeSelect').addEventListener('click', () => {
  if (perfumeId !== null) toggleProduct(perfumeId);
});
$('#closePerfume').addEventListener('click', closePerfume);
$('#perfumeContent').addEventListener('click', event => {
  if (event.target.closest('#retryPerfume') && perfumeId !== null) {
    loadPerfumeDetails(perfumeId);
    $('#perfumeContent').focus({preventScroll:true});
  }
});
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
  // The native close event can arrive after another perfume was already opened.
  if (!$('#perfumeDialog').open) finishPerfumeClose();
});
$('#selectedList').addEventListener('click', e => {
  const button = e.target.closest('[data-remove]');
  if (button) toggleProduct(button.dataset.remove);
});
$('#drawerContent').addEventListener('click', e => {
  const button = e.target.closest('[data-remove]');
  if (button) toggleProduct(button.dataset.remove);
});
$('#searchInput').addEventListener('input', e => { query = e.target.value; renderCatalog(); });
$('#clearSearch').addEventListener('click', () => { query = ''; $('#searchInput').value = ''; renderCatalog(); $('#searchInput').focus(); });
$('#filters').addEventListener('click', e => {
  const button = e.target.closest('[data-filter]');
  if (!button) return;
  activeFilter = button.dataset.filter;
  renderCatalog();
});
$('#seasonFilter').addEventListener('change', e => { activeSeason = e.target.value; renderCatalog(); });
$('#accordSearch').addEventListener('input', renderAccordOptions);
$('#accordOptions').addEventListener('change', e => {
  const input = e.target.closest('input[type="checkbox"]');
  if (!input) return;
  input.checked ? activeAccords.add(input.value) : activeAccords.delete(input.value);
  renderCatalog();
});
$('#activeFilterTags').addEventListener('click', e => {
  const button = e.target.closest('[data-clear-group]');
  if (!button) return;
  if (button.dataset.clearGroup === 'query') { query = ''; $('#searchInput').value = ''; }
  if (button.dataset.clearGroup === 'gender') activeFilter = 'all';
  if (button.dataset.clearGroup === 'season') activeSeason = 'all';
  if (button.dataset.clearGroup === 'accord') activeAccords.delete(button.dataset.clearKey);
  renderCatalog();
});
$('#resetFilters').addEventListener('click', resetCatalogFilters);
$('#clearAllFilters').addEventListener('click', resetCatalogFilters);
document.addEventListener('click', e => { if (!$('#accordFilter').contains(e.target)) $('#accordFilter').open = false; });
$('#accordFilter').addEventListener('keydown', e => {
  if (e.key === 'Escape') { $('#accordFilter').open = false; $('#accordFilter summary').focus(); }
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
$('#modalDone').addEventListener('click', () => selectionState === 'submitted' ? closeModal() : submitSelection());
$('#modalBack').addEventListener('click', closeModal);
$('#modalBackdrop').addEventListener('click', closeModal);
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  if ($('#perfumeDialog').open) return;
  if ($('#demoModal').getAttribute('aria-hidden') === 'false') closeModal();
  else if ($('#mobileDrawer').getAttribute('aria-hidden') === 'false') closeDrawer();
});

$('#catalogStatus').addEventListener('click', event => {
  if (event.target.closest('#retryCatalog')) loadCatalog();
});
$('#catalogMore').addEventListener('click', () => { visibleCount += PAGE_SIZE; appendProducts(); });
if ('IntersectionObserver' in window) {
  new IntersectionObserver(entries => {
    if (catalogState === 'ready' && entries.some(entry => entry.isIntersecting) && visibleCount < filteredProducts.length) {
      visibleCount += PAGE_SIZE;
      appendProducts();
    }
  }, {rootMargin: '320px'}).observe($('#catalogMore'));
}
document.addEventListener('error', event => {
  const img = event.target;
  if (!img.matches?.('[data-product-image]')) return;
  if (img.dataset.fallback) { img.hidden = true; return; }
  img.dataset.fallback = 'true';
  img.src = PLACEHOLDER;
  img.alt = 'تصویر عطر موجود نیست';
}, true);
$('#orderState').addEventListener('click', event => {
  if (event.target.closest('#retrySelectionContext')) loadSelectionContext();
});
$('#demoModal').addEventListener('keydown', event => {
  if (event.key !== 'Tab') return;
  const controls = $$('button:not(:disabled):not([hidden])', $('#demoModal'));
  if (!controls.length) { event.preventDefault(); return; }
  const first = controls[0], last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});

async function selectionApi(action, extra = {}) {
  const started = performance.now();
  const data = await apiJson(`/api/tester/selection/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: selectionToken, ...extra }),
  });
  data.requestDurationMs = performance.now() - started;
  return data;
}

function closeSelectionOverlays() {
  if ($('#perfumeDialog').open) $('#perfumeDialog').close();
  if ($('#mobileDrawer').classList.contains('is-open')) closeDrawer();
  $('#demoModal').inert = true;
  $('#demoModal').setAttribute('aria-hidden', 'true');
  $('#modalBackdrop').hidden = true;
  document.body.style.overflow = '';
}

function checkSelectionDeadline() {
  clearTimeout(selectionExpiryTimer);
  if (!isSelectionPage || !['pending', 'submitting', 'uncertain'].includes(selectionState)) return;
  const remaining = selectionDeadline - performance.now();
  if (remaining > 0) {
    selectionExpiryTimer = setTimeout(checkSelectionDeadline, Math.min(remaining, 30000));
    return;
  }
  selectionState = 'expired';
  closeSelectionOverlays();
  updateSelectionPage();
  $('#selectionNoticeTitle').focus({preventScroll:true});
}

function renderSelectionNotice() {
  const expired = selectionState === 'expired';
  const waiting = selectionState === 'awaiting_sms';
  $('#selectionNotice').hidden = !['expired', 'awaiting_sms', 'review_required'].includes(selectionState);
  $('#selectionNoticeTitle').textContent = expired ? 'مهلت انتخاب تسترهای شما به پایان رسیده است.'
    : waiting ? 'در انتظار تأیید ارسال پیامک' : 'وضعیت انتخاب‌های شما نیاز به بررسی دارد.';
  $('#selectionNoticeDescription').textContent = expired
    ? 'مشتری عزیز، مهلت انتخاب عطرهای پک تستر شما به پایان رسیده است.'
    : waiting ? 'تأیید موفق ارسال پیامک هنوز ثبت نشده است. لطفاً کمی بعد وضعیت را دوباره بررسی کنید.'
    : 'ثبت انتخاب‌های شما نیمه‌تمام است. لطفاً برای بررسی وضعیت با پشتیبانی تماس بگیرید.';
  $('#selectionNoticeDefault').hidden = !expired;
  $('#selectionNoticeDefault').textContent = 'پک تستر پیش‌فرض متناسب با جنسیتی که هنگام ثبت سفارش انتخاب کرده‌اید، برای شما ارسال خواهد شد.';
  $('#refreshSelectionNotice').hidden = !waiting;
}

function updateSelectionPage() {
  const browsing = ['pending', 'submitting', 'uncertain', 'preview'].includes(selectionState);
  $('#collection').hidden = !browsing;
  $('#mobileSelectionBar').hidden = !browsing;
  $('#submittedSelection').hidden = selectionState !== 'submitted';
  renderSelectionNotice();
  $('.order-card').setAttribute('aria-busy', String(selectionState === 'loading'));
  $('#orderFields').hidden = ['loading', 'error', 'preview'].includes(selectionState);
  $('#orderState').hidden = ['pending', 'submitted', 'submitting', 'expired', 'awaiting_sms', 'review_required'].includes(selectionState);
  $('#headerCount').disabled = !browsing;
  renderSelection();
  renderCatalog(false);
}

function applySelectionContext(data) {
  if (!data.order || !['pending', 'submitted', 'submitting', 'review_required'].includes(data.selection?.status)
      || data.order.tester_count !== MAX_TESTERS || typeof data.order.customer_name !== 'string'
      || typeof data.order.phone !== 'string' || typeof data.order.id !== 'string') throw new Error('Invalid context');
  const status = data.selection.status;
  if (status === 'submitted' && (!Array.isArray(data.selection.selected_names) || data.selection.selected_names.length !== MAX_TESTERS
      || !data.selection.selected_names.every(name => typeof name === 'string' && name.trim())
      || !Array.isArray(data.selection.selected_pids) || data.selection.selected_pids.length !== MAX_TESTERS)) throw new Error('Invalid receipt');
  $('#orderCustomerName').textContent = data.order.customer_name;
  $('#orderNumber').textContent = data.order.id;
  $('#orderPhone').textContent = data.order.phone;
  const availability = data.selection.availability;
  if (!['active', 'expired', 'awaiting_sms', 'review_required', 'submitted'].includes(availability)) throw new Error('Invalid window');
  if (availability === 'active' && (!Number.isFinite(data.selection.remaining_seconds) || data.selection.remaining_seconds <= 0)) throw new Error('Invalid window');
  selectionDeadline = performance.now() + Math.max(0, (data.selection.remaining_seconds || 0) * 1000 - (data.requestDurationMs || 0));
  selectionState = status === 'submitted' ? 'submitted' : availability === 'active' ? 'pending' : availability;
  $('#selectionNoticeSupport').hidden = !data.selection.requires_support;
  submissionPids = null;
  if (status === 'submitted') {
    submittedNames = [...data.selection.selected_names];
    selected.splice(0, selected.length, ...data.selection.selected_pids);
    // The receipt uses immutable saved names, independent of later catalog stock.
    data.selection.selected_pids.forEach((pid, i) => productsByPid.set(pid, { pid, name: submittedNames[i] }));
    $('#submittedList').innerHTML = submittedNames.map(name => `<li>${escapeHtml(name)}</li>`).join('');
  }
  if (['expired', 'awaiting_sms', 'review_required'].includes(selectionState)) closeSelectionOverlays();
  updateSelectionPage();
  checkSelectionDeadline();
}

async function loadSelectionContext() {
  if (contextBusy || submitBusy) return;
  contextBusy = true;
  const recovering = selectionState === 'uncertain';
  if (!recovering) selectionState = 'loading';
  $('#orderState').textContent = 'در حال بررسی وضعیت سفارش…';
  updateSelectionPage();
  try {
    const data = await selectionApi('context');
    applySelectionContext(data);
    if (selectionState === 'pending' && catalogState !== 'ready') await loadCatalog();
  } catch (error) {
    selectionState = recovering ? 'uncertain' : 'error';
    const message = error.message === 'API request failed' || !error.status
      ? 'ارتباط با سرویس برقرار نشد. لطفاً دوباره وضعیت سفارش را بررسی کنید.' : error.message;
    $('#orderState').innerHTML = `<strong>${recovering ? 'وضعیت ثبت هنوز مشخص نیست' : 'دریافت سفارش ممکن نشد'}</strong><p>${escapeHtml(message)}</p>
      <button type="button" id="retrySelectionContext">بررسی دوباره</button>`;
    updateSelectionPage();
  } finally { contextBusy = false; }
}

function showSubmissionSuccess() {
  $('#modalTitle').textContent = 'انتخاب‌های شما با موفقیت ثبت شد.';
  $('#modalDescription').textContent = '۱۰ عطر انتخابی شما برای آماده‌سازی پک تستر ثبت شدند. از همراهی شما با سراد سپاسگزاریم.';
  $('#modalSelected').innerHTML = submittedNames.map(name => `<li>${escapeHtml(name)}</li>`).join('');
  $('#modalDone').textContent = 'مشاهده انتخاب‌های ثبت‌شده';
  $('#modalBack').hidden = true;
  $('#submissionMessage').hidden = true;
}

async function submitSelection() {
  if (submitBusy || !isSelectionPage || !['pending', 'uncertain'].includes(selectionState) || selected.length !== MAX_TESTERS) return;
  if (deadlinePassed()) { checkSelectionDeadline(); await loadSelectionContext(); return; }
  submissionPids = submissionPids || [...selected];
  submitBusy = true;
  selectionState = 'submitting';
  $('#modalDone').disabled = true;
  $('#modalDone').textContent = 'در حال ثبت انتخاب‌ها…';
  $('#closeModal').disabled = true;
  $('#modalBack').disabled = true;
  $('#submissionMessage').hidden = true;
  updateSelectionPage();
  try {
    const result = await selectionApi('submit', { pids: submissionPids });
    if (result.selection?.status !== 'submitted') throw new Error('Unknown submission result');
    applySelectionContext(result);
    showSubmissionSuccess();
  } catch (error) {
    // A lost response is never interpreted as success. Ask the server to resume
    // its saved intent or report pending, then allow changes only when confirmed.
    try {
      const result = await selectionApi('context');
      applySelectionContext(result);
      if (selectionState === 'submitted') showSubmissionSuccess();
      else if (selectionState === 'pending') {
        $('#submissionMessage').textContent = error.status ? error.message : 'ثبت انجام نشده است. اتصال را بررسی کنید و دوباره تلاش کنید.';
        $('#submissionMessage').hidden = false;
        $('#modalDone').textContent = 'تلاش دوباره برای ثبت';
        if (error.code === 'products_unavailable') {
          // Keep existing picks visible so unavailable products can be removed.
          await loadCatalog();
        }
      }
    } catch {
      selectionState = error.code === 'selection_expired' || deadlinePassed() ? 'expired' : 'uncertain';
      $('#submissionMessage').textContent = 'نتیجهٔ ثبت مشخص نیست. انتخاب‌ها تا بررسی وضعیت قفل هستند؛ دوباره تلاش کنید.';
      $('#submissionMessage').hidden = false;
      $('#modalDone').textContent = 'بررسی وضعیت و تلاش دوباره';
      $('#orderState').innerHTML = '<strong>در انتظار تأیید وضعیت ثبت</strong><p>انتخاب‌ها تا بررسی نتیجه قابل تغییر نیستند.</p><button type="button" id="retrySelectionContext">بررسی وضعیت ثبت</button>';
    }
  } finally {
    submitBusy = false;
    $('#modalDone').disabled = false;
    $('#closeModal').disabled = false;
    $('#modalBack').disabled = false;
    updateSelectionPage();
    checkSelectionDeadline();
    if (selectionState === 'expired') closeSelectionOverlays();
  }
}

$('#refreshSelectionNotice').addEventListener('click', loadSelectionContext);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && isSelectionPage && selectionState !== 'submitted') {
    checkSelectionDeadline();
    loadSelectionContext();
  }
});

renderSelection();
if (isSelectionPage) loadSelectionContext();
else {
  $('#orderState').innerHTML = '<strong>پیش‌نمایش کاتالوگ سراد</strong><p>برای مشاهده سفارش و ثبت ۱۰ تستر، لینک اختصاصی پیامک را باز کنید.</p>';
  updateSelectionPage();
  loadCatalog();
}

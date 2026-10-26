/* Browser checks using Node 22+ and an installed Chromium browser; no npm dependencies.
 * Start uvicorn on localhost:8787 first. Catalog reads are real in the first checks;
 * fault/identity scenarios intercept only catalog APIs with local fixture responses.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

async function main() {
  const artifacts = path.resolve('test-artifacts/catalog');
  fs.mkdirSync(artifacts, { recursive: true });
  const executable = process.env.CATALOG_BROWSER_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
  const base = process.env.CATALOG_TEST_BASE_URL || 'http://127.0.0.1:8787';
  const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=9228', `--user-data-dir=${fs.mkdtempSync(path.join(artifacts, 'edge-profile-'))}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  let socket;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  try {
    let tabs;
    for (let i = 0; i < 80; i++) {
      try { tabs = await (await fetch('http://127.0.0.1:9228/json/list')).json(); break; }
      catch { await wait(200); }
    }
    assert(tabs, 'Browser debugging endpoint unavailable; set CATALOG_BROWSER_PATH.');
    socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    let sequence = 0;
    const pending = new Map();
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence; pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
    const errors = [];
    const mode = { catalog: 'real', detail: 'real', catalogRequests: 0, detailRequests: 0 };
    const fixtures = Array.from({ length: 498 }, (_, i) => ({
      pid: ['0012', '12', 'quote"pid', '9007199254740993'][i] || String(1000 + i),
      name: i === 0 ? 'عطر یاس کشمیر' : `عطر آزمایشی ${i}`, label: `PERFUME ${i}`,
      englishName: i === 0 ? 'Jasmine Example' : `English Perfume ${i}`, fragranticaName: `Fragrance ${i}`,
      brand: 'Example Brand', sourceBrand: i === 0 ? 'برند فارسی ویژه' : null, imageUrl: null, gender: i % 2 ? 'متمایل به زنانه' : 'مردانه',
      genderGroup: i % 2 ? 'زنانه' : 'مردانه', season: 'پاییز و زمستان', scent: 'گرم و شیرین', vol_idx: i + 1,
      seasonKeys: i % 3 === 0 ? ['spring', 'summer'] : i % 3 === 1 ? ['autumn', 'winter'] : [],
      accordKeys: i % 4 === 0 ? ['woody', 'sweet'] : i % 4 === 1 ? ['sweet'] : i % 4 === 2 ? ['mystery accord'] : [],
    }));
    function fixtureDetail(pid) {
      return { ...fixtures.find(item => item.pid === pid), mainAccords: [{ name: 'چوبی', strength: 80.24, color: '#774414' }],
        notesTop: [{ name: 'رز', originalName: 'Rose', noteId: '105', image: null }], notesMiddle: [], notesBase: [],
        longevity: { label: 'ماندگار', level: 4, scale: ['خیلی ضعیف', 'ضعیف', 'متوسط', 'ماندگار', 'بسیار ماندگار'] },
        sillage: { label: 'قوی', level: 3, scale: ['ملایم', 'متوسط', 'قوی', 'بسیار قوی'] },
        descriptiveTags: [{ id: 'luxurious', label: 'لوکس' }, { id: 'bold', label: 'جسور' },
          { id: 'future_tag', label: '<img src=x onerror=alert(1)> توصیف ناشناخته با نام طولانی برای بررسی چیدمان' }] };
    }
    async function intercept(event) {
      const url = new URL(event.request.url);
      const catalog = url.pathname === '/api/tester/catalog';
      catalog ? mode.catalogRequests++ : mode.detailRequests++;
      const strategy = catalog ? mode.catalog : mode.detail;
      if (strategy === 'real') return send('Fetch.continueRequest', { requestId: event.requestId });
      if (strategy === 'slow') await wait(500);
      const pid = catalog ? null : decodeURIComponent(url.pathname.split('/').at(-1));
      let status = strategy === 'error' ? 503 : strategy === 'unavailable' ? 404 : 200;
      let body = catalog ? { products: strategy === 'empty' ? [] : fixtures, total: strategy === 'empty' ? 0 : fixtures.length } : fixtureDetail(pid);
      if (catalog) body.accordLabels = { woody: 'چوبی', sweet: 'شیرین', 'mystery accord': 'Mystery Accord', 'outside catalog': 'نباید دیده شود' };
      if (!catalog && strategy === 'missing') body = { ...fixtures.find(item => item.pid === pid), mainAccords: [], notesTop: null, notesMiddle: [], notesBase: [], longevity: null, sillage: null, descriptiveTags: [] };
      if (status !== 200) body = { detail: 'Test error' };
      return send('Fetch.fulfillRequest', { requestId: event.requestId, responseCode: status,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json; charset=utf-8' }], body: Buffer.from(JSON.stringify(body)).toString('base64') });
    }
    socket.onmessage = ({ data }) => {
      const message = JSON.parse(data);
      if (pending.has(message.id)) {
        const { resolve, reject } = pending.get(message.id); pending.delete(message.id);
        message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
      } else if (message.method === 'Fetch.requestPaused') intercept(message.params).catch(error => errors.push(String(error)));
      else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
    };
    const evaluate = async expression => {
      const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    const until = async expression => {
      for (let i = 0; i < 160; i++) {
        try { if (await evaluate(expression)) return; } catch { /* Navigation may still be initializing JS. */ }
        await wait(100);
      }
      throw new Error('Timed out: ' + expression);
    };
    const click = async selector => {
      const point = await evaluate(`(() => {const el=document.querySelector(${JSON.stringify(selector)});el.scrollIntoView({block:'center',behavior:'instant'});const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 });
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 });
      await wait(30);
    };
    const close = async () => { await click('#closePerfume'); await until('!document.querySelector("#perfumeDialog").open'); };
    const load = async (width = 1280, height = 900) => {
      await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width <= 680 });
      await send('Page.navigate', { url: base });
      await until('typeof catalogState !== "undefined" && ["ready","empty","error"].includes(catalogState)');
      await evaluate('document.fonts.ready.then(()=>true)');
    };
    const screenshot = async name => {
      const result = await send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(artifacts, name + '.png'), Buffer.from(result.data, 'base64'));
    };
    const settleVisibleImages = () => evaluate(`(async () => {
      for (let i=0;i<50;i++) {
        const images=[...document.images].filter(img=>{const r=img.getBoundingClientRect();return r.width&&r.height&&r.top<innerHeight&&r.bottom>0;});
        if(images.every(img=>img.complete)) return;
        await new Promise(resolve=>setTimeout(resolve,100));
      }
    })()`);
    await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
    await send('Network.setCacheDisabled', { cacheDisabled: true });
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
    await send('Fetch.enable', { patterns: [{ urlPattern: '*/api/tester/catalog' }, { urlPattern: '*/api/tester/perfumes/*' }] });

    // Read-only real catalog smoke test and responsive review.
    const real = [];
    for (const [width, height] of [[1440,1000], [768,1024], [390,844], [320,740]]) {
      await load(width, height);
      assert(await evaluate('catalogState === "ready" && PERFUMES.length > 0 && PERFUMES.length <= 498'));
      assert(await evaluate('document.querySelectorAll(".product-card").length === 24 && [...productsByPid.keys()].every(pid=>typeof pid==="string")'));
      await evaluate('document.querySelector(".finder").scrollIntoView({behavior:"instant",block:"start"})');
      await settleVisibleImages();
      await screenshot('real-catalog-' + width);
      await click('.product-card .product-name');
      await until('document.querySelector("#perfumeContent").dataset.state === "ready"');
      await wait(300);
      await settleVisibleImages();
      const layout = await evaluate(`(() => {const c=document.querySelector('#perfumeContent');const f=document.querySelector('.pd-footer').getBoundingClientRect();const photo=document.querySelector('.pd-photo img');return {total:PERFUMES.length,rendered:document.querySelectorAll('.product-card').length,overflow:document.documentElement.scrollWidth>innerWidth||c.scrollWidth>c.clientWidth,footerBottom:f.bottom,accords:document.querySelectorAll('.pd-accord').length,notes:document.querySelectorAll('.pd-note').length,images:[...document.querySelectorAll('.pd-note img')].filter(img=>img.complete&&img.naturalWidth>0).length,productsWithImage:PERFUMES.filter(p=>p.imageUrl).length,productPhotoLoaded:!!(photo?.complete&&photo.naturalWidth>0&&!photo.src.includes('perfume-placeholder'))};})()`);
      assert(!layout.overflow && layout.footerBottom <= height + 1);
      assert(await evaluate('document.querySelectorAll(".pd-description").length === 0'));
      assert(await evaluate('[...document.querySelectorAll(".pd-note img")].every(img=>img.loading==="lazy"&&img.referrerPolicy==="no-referrer")'));
      assert(await evaluate('[...document.querySelectorAll("[data-product-image]")].every(img=>img.loading==="lazy"&&img.referrerPolicy==="no-referrer")'));
      real.push({ width, ...layout });
      await screenshot('real-detail-' + width);
      if (await evaluate('!!document.querySelector(".pd-traits")')) {
        await evaluate('document.querySelector(".pd-traits").scrollIntoView({behavior:"instant",block:"center"})');
        await screenshot('real-traits-' + width);
        assert(await evaluate('document.querySelector(".pd-traits").previousElementSibling.matches(".pd-meters")'));
      }
      await evaluate('document.querySelector("#perfumeContent").scrollTop = 100000'); await wait(500);
      await screenshot('real-notes-' + width);
      await close();
    }

    // Fixture-only behavior tests: types, normalization, incremental rendering, caches and errors.
    mode.catalog = 'fixture'; mode.detail = 'fixture';
    const filterLayouts = [];
    for (const [width, height] of [[1440,1000], [768,1024], [390,844], [320,740]]) {
      await load(width, height);
      await evaluate(`document.querySelector('.finder').scrollIntoView({block:'start',behavior:'instant'});document.querySelector('#accordFilter').open=true`);
      const layout = await evaluate(`(() => {const picker=document.querySelector('.accord-picker').getBoundingClientRect();const select=document.querySelector('#seasonFilter').getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth,pickerLeft:picker.left,pickerRight:picker.right,selectHeight:select.height,optionHeight:document.querySelector('.accord-option').getBoundingClientRect().height};})()`);
      assert(!layout.overflow && layout.pickerLeft >= 0 && layout.pickerRight <= width + 1);
      assert(layout.selectHeight >= 42 && layout.optionHeight >= 44);
      filterLayouts.push({width, ...layout});
      await screenshot('filters-' + width);
      await evaluate(`document.querySelector('#accordSearch').focus();document.querySelector('#accordSearch').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
      assert(await evaluate('!document.querySelector("#accordFilter").open && document.activeElement===document.querySelector("#accordFilter summary")'));
    }
    fs.writeFileSync(path.join(artifacts, 'filter-responsive.json'), JSON.stringify(filterLayouts, null, 2));
    await load();
    assert(await evaluate('document.querySelector(".product-card [data-product-image]").src.includes("perfume-placeholder")'));
    await evaluate('document.querySelector(".product-card [data-product-image]").dispatchEvent(new Event("error"))');
    assert(await evaluate('document.querySelector(".product-card [data-product-image]").src.includes("perfume-placeholder")'));
    assert(await evaluate(`normalizeSearch('ياس كَشمير')===normalizeSearch('یاس کشمیر')&&normalizeSearch('بی‌انتها')===normalizeSearch('بی انتها')&&normalizeSearch('  ENGLISH   Brand ')==='english brand'`));
    assert(await evaluate(`!safeImageUrl('javascript:alert(1)')&&!safeImageUrl('https://127.0.0.1/a')&&!safeImageUrl('http://fimgs.net/a')`));
    await evaluate(`toggleProduct('0012');toggleProduct('12');toggleProduct('9007199254740993');toggleProduct('quote"pid')`);
    assert.deepEqual(await evaluate('selected'), ['0012','12','9007199254740993','quote"pid']);
    const catalogCalls = mode.catalogRequests;
    await evaluate(`(() => {const input=document.querySelector('#searchInput');input.value='ياس كَشمير';input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
    assert(await evaluate('filteredProducts.length===1&&filteredProducts[0].pid==="0012"&&selected.length===4'));
    await evaluate(`query='برند فارسی ویژه';renderCatalog()`);
    assert(await evaluate('filteredProducts.length===1&&filteredProducts[0].pid==="0012"'));
    await evaluate(`document.querySelector('#clearSearch').click();document.querySelectorAll('[data-filter]')[1].click()`);
    assert(await evaluate('filteredProducts.length===249&&selected.length===4'));
    await evaluate(`document.querySelector('[data-filter="all"]').click()`);
    assert.deepEqual(await evaluate('accordOptions.map(option=>option.key).sort()'), ['mystery accord','sweet','woody']);
    assert(await evaluate('accordOptions.find(option=>option.key==="woody").label==="چوبی"'));
    // OR within accords; AND across gender, season and normalized text search.
    await evaluate(`(() => {const season=document.querySelector('#seasonFilter');season.value='spring';season.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    assert(await evaluate('filteredProducts.length===166 && selected.length===4'));
    await evaluate(`document.querySelector('#accordFilter').open=true;document.querySelector('#accordSearch').value='چوبی';document.querySelector('#accordSearch').dispatchEvent(new Event('input',{bubbles:true}))`);
    assert(await evaluate('document.querySelectorAll("#accordOptions input").length===1'));
    await evaluate(`document.querySelector('#accordOptions input[value="woody"]').click()`);
    assert(await evaluate('filteredProducts.length===42 && activeAccords.size===1 && selected.length===4'));
    await evaluate(`document.querySelector('#accordSearch').value='';document.querySelector('#accordSearch').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#accordOptions input[value="mystery accord"]').click()`);
    assert(await evaluate('filteredProducts.length===83 && activeAccords.size===2'));
    await evaluate(`document.querySelector('[data-filter="مردانه"]').click();query='jasmine example';renderCatalog()`);
    assert(await evaluate('filteredProducts.length===1 && filteredProducts[0].pid==="0012" && selected.length===4'));
    await evaluate(`query='nothing matches';renderCatalog()`);
    assert(await evaluate('!document.querySelector("#emptyResults").hidden && document.querySelector("#resultCount").textContent.includes("۰")'));
    await evaluate(`document.querySelector('#resetFilters').click()`);
    assert(await evaluate('filteredProducts.length===498 && !activeAccords.size && activeSeason==="all" && query==="" && selected.length===4 && document.querySelector("#activeCatalogFilters").hidden'));
    // Individual active tags, unknown-label fallback, no-result accord search, XSS escaping.
    await evaluate(`activeAccords.add('mystery accord');renderCatalog();document.querySelector('[data-clear-group="accord"]').click()`);
    assert(await evaluate('activeAccords.size===0 && filteredProducts.length===498'));
    await evaluate(`document.querySelector('#accordSearch').value='does not exist';renderAccordOptions()`);
    assert(await evaluate('!document.querySelector("#accordEmpty").hidden && document.querySelectorAll("#accordOptions input").length===0'));
    await evaluate(`query='<img src=x onerror=alert(1)>';renderCatalog()`);
    assert(await evaluate('document.querySelectorAll("#activeFilterTags img").length===0'));
    await evaluate(`document.querySelector('#clearAllFilters').click()`);
    assert.equal(mode.catalogRequests, catalogCalls, 'Search/filter called the API');
    await evaluate('document.querySelector("#catalogMore").click()');
    assert(await evaluate('document.querySelectorAll(".product-card").length===48&&filteredProducts.length===498'));
    await click('.product-card .product-name'); await until('document.querySelector("#perfumeContent").dataset.state==="ready"');
    assert(await evaluate('document.querySelectorAll(".pd-trait").length===3&&document.querySelector(".pd-trait").textContent==="لوکس"&&document.querySelectorAll(".pd-traits img").length===0'));
    assert(await evaluate('document.querySelector("#perfumeSelect").getAttribute("aria-pressed")==="true"'));
    await click('#perfumeSelect');
    assert(await evaluate('!selected.includes("0012")&&document.querySelector("#perfumeDialog").open'));
    await close();
    const detailCalls = mode.detailRequests;
    await click('.product-card .product-name'); await until('document.querySelector("#perfumeContent").dataset.state==="ready"');
    assert.equal(mode.detailRequests, detailCalls, 'Successful detail was not cached');
    await close();
    await evaluate(`selected.splice(0);renderSelection();for(const p of PERFUMES.slice(0,10))toggleProduct(p.pid)`);
    assert(await evaluate('selected.length===10&&document.querySelectorAll(".product-button:disabled").length>0'));
    await evaluate('toggleProduct(PERFUMES[10].pid)'); assert(await evaluate('selected.length===10'));

    // API unavailable must never silently restore the twelve mock perfumes.
    mode.catalog = 'error'; await load();
    assert(await evaluate('catalogState==="error"&&PERFUMES.length===0&&document.querySelectorAll(".product-card").length===0'));
    mode.catalog = 'fixture'; await click('#retryCatalog'); await until('catalogState==="ready"');
    mode.catalog = 'empty'; await load();
    assert(await evaluate('catalogState==="empty"&&PERFUMES.length===0&&!document.querySelector("#catalogStatus").hidden'));
    mode.catalog = 'fixture'; mode.detail = 'error'; await load();
    await click('.product-card .product-name'); await until('document.querySelector("#perfumeContent").dataset.state==="error"');
    mode.detail = 'missing'; await click('#retryPerfume'); await until('document.querySelector("#perfumeContent").dataset.state==="ready"');
    assert(await evaluate('document.querySelectorAll(".pd-note-group").length===0&&document.querySelectorAll("[role=meter]").length===0&&document.querySelectorAll(".pd-traits").length===0'));
    await close();
    mode.detail = 'unavailable'; await evaluate('openPerfume("12")'); await until('document.querySelector("#perfumeContent").dataset.state==="error"');
    assert(await evaluate('document.querySelector("#perfumeSelect").disabled'));
    await close();

    // A late response from A must not replace the visible details for B.
    await load(); mode.detail = 'slow'; await evaluate('openPerfume("0012")'); await close();
    mode.detail = 'fixture';
    await evaluate('openPerfume("12"); document.querySelector("#perfumeDialog").dispatchEvent(new Event("close"))');
    assert(await evaluate('perfumeId==="12"&&document.body.style.overflow==="hidden"'), 'A delayed close event cleared the newly opened perfume');
    await until('document.querySelector("#perfumeContent").dataset.state==="ready"'); await wait(600);
    assert(await evaluate('perfumeId==="12"&&document.querySelector("#perfumeTitle").textContent===getProduct("12").name'));
    await close();
    assert.equal(errors.length, 0, JSON.stringify(errors));
    fs.writeFileSync(path.join(artifacts, 'browser-report.json'), JSON.stringify({ real, assertions: 'passed' }, null, 2));
    console.log(JSON.stringify(real));
    console.log('PASS: real catalog, responsive, pid identity, Persian/English normalization, local filtering, incremental DOM, selection persistence/limit, detail cache, retries, missing data, 404 eligibility, stale responses, no runtime errors.');
    await send('Browser.close');
  } finally {
    socket?.close();
    if (browser.exitCode === null) browser.kill();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });

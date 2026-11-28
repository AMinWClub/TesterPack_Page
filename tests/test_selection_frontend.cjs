/* Start: python -m tests.selection_preview --port 8789
 * Uses only the preview's owned database and synthetic orders. No SMS calls.
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');

async function main() {
  const { fixtures, database } = JSON.parse(fs.readFileSync('test-artifacts/selection-preview.json', 'utf8'));
  assert(database.startsWith('tester_selection_preview_'));
  assert(Object.values(fixtures).every(f=>/^\/[A-Za-z0-9_-]{18}$/.test(f.path)));
  assert(/[a-z]/.test(fixtures.success.path)&&/[A-Z]/.test(fixtures.success.path)&&fixtures.success.path.includes('-')&&fixtures.success.path.includes('_'), 'Preview must exercise exact case and both URL-safe symbols');
  const base = process.env.SELECTION_TEST_BASE_URL || 'http://127.0.0.1:8789';
  assert(['localhost','127.0.0.1'].includes(new URL(base).hostname), 'Only a local isolated preview is allowed');
  const artifacts = path.resolve('test-artifacts/selection');
  fs.mkdirSync(artifacts, { recursive:true });
  const executable = process.env.CATALOG_BROWSER_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
  const browser = spawn(executable, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--remote-debugging-port=9232',`--user-data-dir=${fs.mkdtempSync(path.join(artifacts,'edge-profile-'))}`,'about:blank'], { windowsHide:true, stdio:'ignore' });
  let socket;
  const wait = ms => new Promise(resolve=>setTimeout(resolve,ms));
  const modes = { context:'real', submit:'real' };
  let submits=0, catalogRequests=0, contextRequests=0, heldSubmitResponse;
  const errors=[], headers=[], responsive=[];
  try {
    let tabs;
    for(let i=0;i<80;i++){try{tabs=await(await fetch('http://127.0.0.1:9232/json/list')).json();break;}catch{await wait(200);}}
    assert(tabs, 'Chromium debug endpoint unavailable');
    socket=new WebSocket(tabs.find(tab=>tab.type==='page').webSocketDebuggerUrl);
    await new Promise(resolve=>socket.onopen=resolve);
    let id=0; const pending=new Map();
    const send=(method,params={})=>new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});socket.send(JSON.stringify({id:key,method,params}));});
    async function intercept(event){
      const action=event.request.url.endsWith('/submit')?'submit':'context';
      const response=event.responseStatusCode!==undefined;
      if(!response&&action==='submit')submits++;
      if(!response&&event.request.url.endsWith('/context'))contextRequests++;
      if(response){
        if(action==='submit'&&modes.submit==='hold-response'){
          heldSubmitResponse=event.requestId;return;
        }
        if(action==='submit'&&modes.submit==='lose-response'){
          modes.submit='real';return send('Fetch.failRequest',{requestId:event.requestId,errorReason:'ConnectionReset'});
        }
        return send('Fetch.continueRequest',{requestId:event.requestId});
      }
      const strategy=modes[action];
      if(strategy==='drop')return send('Fetch.failRequest',{requestId:event.requestId,errorReason:'ConnectionReset'});
      if(strategy==='error'||strategy==='unavailable')return send('Fetch.fulfillRequest',{requestId:event.requestId,responseCode:strategy==='error'?503:409,
        responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify({detail:{code:strategy==='error'?'database_unavailable':'products_unavailable',message:strategy==='error'?'ارتباط موقتاً برقرار نیست.':'یکی از عطرها دیگر موجود نیست؛ انتخاب‌ها را بررسی کنید.'}})).toString('base64')});
      return send('Fetch.continueRequest',{requestId:event.requestId});
    }
    socket.onmessage=({data})=>{
      const m=JSON.parse(data);
      if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}
      else if(m.method==='Fetch.requestPaused')intercept(m.params).catch(error=>errors.push(String(error)));
      else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);
      else if(m.method==='Network.requestWillBeSent'){
        const req=m.params.request;
        if(req.url.includes('/api/tester/catalog'))catalogRequests++;
        if(req.url.includes('/api/')){
          if(new URL(req.url).search||Object.values(fixtures).some(f=>req.url.includes(f.path.split('/').at(-1))))errors.push('Token leaked in an API URL');
        }
        const referer=Object.entries(req.headers).find(([key])=>key.toLowerCase()==='referer')?.[1];
        if(referer&&Object.values(fixtures).some(f=>referer.includes(f.path)))errors.push('Private URL leaked in Referer');
      }else if(m.method==='Network.responseReceived'){
        const response=m.params.response;
        const url=new URL(response.url);
        if(url.origin===new URL(base).origin&&(/^\/[A-Za-z0-9_-]{18}\/?$/.test(decodeURIComponent(url.pathname))||url.pathname.startsWith('/api/tester/selection/'))){
          const h=Object.fromEntries(Object.entries(response.headers).map(([k,v])=>[k.toLowerCase(),v]));
          if(response.status===200)headers.push({noStore:h['cache-control']==='no-store',noReferrer:h['referrer-policy']==='no-referrer',noIndex:h['x-robots-tag']?.includes('noindex')});
        }
      }
    };
    const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value;};
    const until=async expression=>{for(let i=0;i<180;i++){try{if(await evaluate(expression))return;}catch{}await wait(100);}throw new Error('Timed out: '+expression);};
    const countdown=()=>evaluate(`[...document.querySelectorAll('.selection-countdown')].map(el=>({
      visible:el.getClientRects().length>0, text:el.querySelector('[role="timer"]').textContent, urgency:el.dataset.urgency
    }))`);
    const assertCountdownHidden=async()=>{
      assert((await countdown()).every(timer=>!timer.visible));
      assert(await evaluate('selectionCountdownInterval===undefined'));
    };
    const click=async selector=>{
      const point=await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.scrollIntoView({block:'center',behavior:'instant'});const r=el.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};})()`);
      await send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
      await send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
    };
    const load=async(route,width=390,height=844)=>{
      await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<=680});
      await send('Page.navigate',{url:base+route});
      await until('typeof selectionState!=="undefined" && ["pending","submitted","error","preview","expired","awaiting_sms","review_required"].includes(selectionState)');
      await evaluate('document.fonts.ready.then(()=>true)');
    };
    const screenshot=async(name,clip)=>{const shot=await send('Page.captureScreenshot',{format:'png',clip});fs.writeFileSync(path.join(artifacts,name+'.png'),Buffer.from(shot.data,'base64'));};
    const chooseTesters=async()=>{
      await until('catalogState==="ready"');
      await evaluate('for(const item of PERFUMES.slice(0,MAX_TESTERS)){if(!selectedHas(item.pid))document.querySelector(`[data-toggle="${CSS.escape(item.pid)}"]`).click();}');
      assert(await evaluate('selected.length===MAX_TESTERS&&document.querySelectorAll(".product-button:disabled").length>0'));
    };
    const review=async()=>{
      if(await evaluate('innerWidth<=940')){await click('#openDrawer');await click('#mobileFinalize');}
      else await click('#finalizeButton');
      await until('document.querySelector("#demoModal").getAttribute("aria-hidden")==="false"');
      assert(await evaluate('document.querySelectorAll("#modalSelected li").length===MAX_TESTERS'));
    };
    await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
    await send('Page.addScriptToEvaluateOnNewDocument',{source:'Date.now=()=>4070908800000;'});
    await send('Network.setCacheDisabled',{cacheDisabled:true});
    await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    await send('Fetch.enable',{patterns:[{urlPattern:'*/api/tester/selection/*',requestStage:'Request'},{urlPattern:'*/api/tester/selection/submit',requestStage:'Response'}]});

    // Historical receipts use their actual count; frozen pending links use the
    // server's per-selection capacity rather than today's HTML default.
    for (const count of [10, 6]) {
      for (const width of [1440, 390]) {
        await load(fixtures['historical_' + count].path, width);
        assert(await evaluate(`selectionState==='submitted' && selected.length===${count}
          && document.querySelectorAll('#submittedList li').length===${count}
          && [...document.querySelectorAll('[data-tester-count]')].every(el=>el.textContent===farsiNumber(${count}))
          && document.querySelector('#progressTrack').getAttribute('aria-valuemax')==='${count}'
          && document.querySelector('#progressTrack').getAttribute('aria-valuenow')==='${count}'
          && !canEditSelection() && document.documentElement.scrollWidth<=innerWidth`));
        assert(await evaluate(`(async()=>{const result=await selectionApi('submit',{pids:selected});return result.selection.status==='submitted'&&result.order.tester_count===${count};})()`));
        assert(await evaluate(`(async()=>{const original=await selectionApi('context');
          const changes=[{selected_names:[]},{selected_names:['wrong length']},{selected_pids:['duplicate','duplicate']},
            {selected_names:original.selection.selected_names.map(()=>null)},
            {selected_pids:original.selection.selected_pids.map(()=> 'duplicate')}];
          return changes.every(change=>{const data=structuredClone(original);Object.assign(data.selection,change);
            try{applySelectionContext(data);return false;}catch{return true;}});})()`));
      }
    }
    await load(fixtures.frozen_10.path);
    assert(await evaluate('MAX_TESTERS===10 && Number(document.querySelector(\'meta[name="tester-selection-count"]\').content)!==10'));
    await chooseTesters();await review();await click('#modalDone');await until('selectionState==="submitted"&&!submitBusy');
    assert(await evaluate('submittedNames.length===10&&document.querySelector("#modalDescription").textContent.includes(farsiNumber(10))'));
    for(const [width,height]of [[1440,1000],[768,1024],[390,844],[320,740]]){
      await load(fixtures.success.path,width,height);await until('catalogState==="ready"');
      assert(await evaluate('isSelectionPage&&/^\\/[A-Za-z0-9_-]{18}$/.test(location.pathname)&&typeof selectionToken==="string"&&selectionToken.length===18'));
      assert.equal(await evaluate('selectionToken'),fixtures.success.path.slice(1));
      assert.equal(await evaluate('document.querySelector("#orderPhone").textContent'),fixtures.success.phone);
      assert.equal(await evaluate('document.querySelector("#orderNumber").textContent'),fixtures.success.order_id);
      assert.equal(await evaluate('getComputedStyle(document.querySelector("#orderPhone")).direction'),'ltr');
      assert.equal(await evaluate('document.querySelector("#orderCustomerName").textContent'),'مشتری آزمایشی');
      assert(await evaluate('!document.querySelector("#orderFields").hidden&&document.documentElement.scrollWidth<=innerWidth'));
      const timers=await countdown();
      assert.deepEqual(timers.map(timer=>timer.visible),[width>940,width<=940]);
      assert.equal(timers[0].text,timers[1].text);
      assert(await evaluate(`(()=>{
        const desktop=innerWidth>940;
        if(desktop)scrollTo({top:500,behavior:'instant'});
        const container=document.querySelector(desktop?'.site-header':'#mobileSelectionBar');
        const controls=desktop?['.selection-countdown-header','#headerCount']:['.mobile-selection-info','.selection-countdown-mobile','#openDrawer'];
        const bounds=container.getBoundingClientRect();
        const rects=controls.map(selector=>container.querySelector(selector).getBoundingClientRect());
        const withinBar=rects.every(rect=>rect.left>=bounds.left&&rect.right<=bounds.right&&rect.top>=bounds.top&&rect.bottom<=bounds.bottom);
        if(desktop)return withinBar&&scrollY>0&&bounds.top===0&&rects[1].right<=rects[0].left;
        const [info,timer,action]=rects;
        const [label,value]=[...container.querySelector('.selection-countdown-mobile').children].map(el=>el.getBoundingClientRect());
        const content=container.querySelector('.mobile-selection-content').getBoundingClientRect();
        return withinBar&&bounds.bottom===innerHeight&&timer.right<=info.left
          && Math.max(timer.top,info.top)<Math.min(timer.bottom,info.bottom)
          && Math.max(timer.bottom,info.bottom)<=action.top
          && action.width===content.width&&action.height>=44
          && value.right<=label.left&&Math.max(label.top,value.top)<Math.min(label.bottom,value.bottom);
      })()`),`Countdown overlaps controls or scrolls away at ${width}px`);
      await evaluate('scrollTo(0,0)');await screenshot('order-'+width);
      if(width===390){
        const clip=await evaluate(`(()=>{
          const sampleCount=Math.min(2,MAX_TESTERS);
          PERFUMES.slice(0,sampleCount).forEach(item=>toggleProduct(item.pid));
          if(selected.length!==sampleCount||document.querySelector('#mobileCount').textContent!==farsiNumber(sampleCount))
            throw Error('Mobile count did not reflect the selected testers');
          const {x,y,width,height}=document.querySelector('#mobileSelectionBar').getBoundingClientRect();
          return {x,y:y+scrollY,width,height,scale:1};
        })()`);
        await screenshot('mobile-selection-bar',clip);
      }
      responsive.push({width,orderVisible:true,fullPhone:true,overflow:false});
    }
    // A single changed letter must not authorize the original order.
    const changedCasePath=fixtures.success.path.replace(/[A-Za-z]/,char=>String.fromCharCode(char.charCodeAt(0)^32));
    await load(changedCasePath,320,740);
    assert(await evaluate('selectionState==="error"&&document.querySelector("#orderFields").hidden'));
    await assertCountdownHidden();
    // Percent-encoding must preserve the exact token case when it is extracted.
    const token=fixtures.success.path.slice(1);
    await load('/%'+token.charCodeAt(0).toString(16)+token.slice(1),320,740);
    await until('catalogState==="ready"');
    assert.equal(await evaluate('selectionToken'),token);
    // Existing details and the new order state must work together.
    await click('.product-card .product-name');await until('document.querySelector("#perfumeContent").dataset.state==="ready"');
    assert(await evaluate('document.querySelectorAll(".pd-trait").length===2'));
    await click('#perfumeSelect');assert(await evaluate('selected.length===1'));
    await click('#closePerfume');await until('!document.querySelector("#perfumeDialog").open');
    await chooseTesters();await review();await screenshot('review-mobile');
    const before=submits;
    await evaluate('document.querySelector("#modalDone").click();document.querySelector("#modalDone").click()');
    await until('selectionState==="submitted"&&!submitBusy');
    assert.equal(submits-before,1,'Double click sent two requests');
    await screenshot('success-mobile');await click('#modalDone');
    for(const [width,height]of [[1440,1000],[390,844],[320,740]]){
      const catalogs=catalogRequests;
      await load(fixtures.success.path,width,height);
      assert(await evaluate('selectionState==="submitted"&&document.querySelectorAll("#submittedList li").length===MAX_TESTERS&&document.querySelector("#collection").hidden'));
      await assertCountdownHidden();
      assert.equal(catalogRequests,catalogs,'Receipt unexpectedly fetched the live catalog');
      assert(await evaluate('document.querySelector("#submittedList").textContent.includes("Original Source Perfume")'));
      const selectedBefore=await evaluate('JSON.stringify(selected)');await evaluate('toggleProduct(selected[0])');
      assert.equal(await evaluate('JSON.stringify(selected)'),selectedBefore);
      assert(await evaluate('document.documentElement.scrollWidth<=innerWidth'));
      await evaluate('scrollTo(0,0)');await screenshot('submitted-'+width);
    }
    // The server committed, but its HTTP response was lost. Context proves success.
    await load(fixtures.lost_response.path);await chooseTesters();await review();
    modes.submit='lose-response';await click('#modalDone');await until('selectionState==="submitted"&&!submitBusy');
    await click('#modalDone');
    // Both requests are unreachable. Lock choices until Context proves pending.
    await load(fixtures.uncertain.path);await chooseTesters();await review();
    modes.submit='drop';modes.context='error';await click('#modalDone');await until('selectionState==="uncertain"&&!submitBusy');
    await assertCountdownHidden();
    assert(await evaluate('!canEditSelection()&&document.querySelector("#submissionMessage").textContent.includes("مشخص نیست")'));
    const frozen=await evaluate('JSON.stringify(selected)');await evaluate('toggleProduct(selected[0])');assert.equal(await evaluate('JSON.stringify(selected)'),frozen);
    await click('#closeModal');modes.context='real';modes.submit='real';await click('#retrySelectionContext');
    await until('selectionState==="pending"');assert(await evaluate('selected.length===MAX_TESTERS&&canEditSelection()'));
    await review();await click('#modalDone');await until('selectionState==="submitted"&&!submitBusy');await click('#modalDone');
    // A rejected product gives a readable error and editable choices after Context.
    await load(fixtures.unavailable.path);await chooseTesters();await review();modes.submit='unavailable';
    await click('#modalDone');await until('selectionState==="pending"&&!submitBusy&&!document.querySelector("#submissionMessage").hidden');
    assert(await evaluate('document.querySelector("#submissionMessage").textContent.includes("موجود نیست")&&canEditSelection()'));
    await click('#modalBack');modes.submit='real';

    // No successful notification means no editable selection, independently of creation time.
    await load(fixtures.expiry_waiting.path);
    assert(await evaluate('selectionState==="awaiting_sms"&&!canEditSelection()&&document.querySelector("#collection").hidden'));
    await assertCountdownHidden();
    assert(await evaluate('!document.querySelector("#selectionNotice").hidden&&document.querySelector("#selectionNoticeDefault").hidden'));
    const python=process.env.SELECTION_TEST_PYTHON || path.resolve(process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
    const setWindow=(scenario,seconds)=>execFileSync(python,['-c',`import asyncio; from tests.selection_preview import set_preview_window; asyncio.run(set_preview_window(${JSON.stringify(scenario)}, ${seconds}))`],{windowsHide:true,stdio:'pipe'});
    setWindow('expiry_waiting',1800);
    await click('#refreshSelectionNotice');await until('selectionState==="pending"&&catalogState==="ready"');

    // A real refresh reports elapsed time from the original server window.
    const readSeconds=async()=>{
      const {text}=(await countdown())[0];
      const [minutes,seconds]=text.replace(/[۰-۹]/g,digit=>'۰۱۲۳۴۵۶۷۸۹'.indexOf(digit)).split(':').map(Number);
      return minutes*60+seconds;
    };
    const initialSeconds=await readSeconds();
    assert(initialSeconds>=1795&&initialSeconds<=1800, `Initial countdown was ${initialSeconds}s`);
    const requestsBeforeTick=contextRequests;
    await wait(2100);
    const elapsedSeconds=await readSeconds();
    assert(elapsedSeconds<initialSeconds, 'Countdown did not tick');
    assert.equal(contextRequests,requestsBeforeTick,'Countdown tick fetched context');
    await evaluate('window.beforeCountdownReload=true');
    await send('Page.reload');
    await until('!window.beforeCountdownReload&&typeof selectionState!=="undefined"&&selectionState==="pending"&&catalogState==="ready"');
    assert((await readSeconds())<=elapsedSeconds,'Reload restarted the countdown');

    // Returning to a visible page reconciles with the server even after skipped ticks.
    await evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:true});
      document.dispatchEvent(new Event('visibilitychange'));`);
    setWindow('expiry_waiting',240);
    assert(await evaluate(`delete document.hidden; document.dispatchEvent(new Event('visibilitychange'));
      selectionState==='loading'&&selectionCountdowns.every(timer=>timer.hidden)&&selectionCountdownInterval===undefined;`));
    await until('selectionState==="pending"&&document.querySelector(".selection-countdown").dataset.urgency==="warning"');
    const synchronizedSeconds=await readSeconds();
    assert(synchronizedSeconds<=240&&synchronizedSeconds>=235);

    // Use a fixed monotonic clock to cover exact thresholds and a throttled tick.
    await evaluate(`(async()=>{
      const data=await selectionApi('context');
      performance.now=()=>1000;
      data.requestDurationMs=0;
      data.selection.remaining_seconds=2701;
      const interval=selectionCountdownInterval;
      applySelectionContext(data);
      applySelectionContext(data);
      if(selectionCountdownInterval!==interval)throw Error('Context refresh replaced the countdown interval');
    })()`);
    assert.deepEqual((await countdown()).map(({text,urgency})=>[text,urgency]),[['۴۵:۰۱','normal'],['۴۵:۰۱','normal']]);
    for(const [seconds,urgency] of [[301,'normal'],[300,'warning'],[61,'warning'],[60,'critical']]){
      await evaluate(`performance.now=()=>selectionDeadline-${seconds*1000};renderSelectionCountdown();`);
      assert.equal(await readSeconds(),seconds);
      assert((await countdown()).every(timer=>timer.urgency===urgency));
    }
    await evaluate('performance.now=()=>selectionDeadline+1;renderSelectionCountdown();delete performance.now;');
    assert(await evaluate('selectionState==="expired"&&!document.querySelector("#selectionNotice").hidden'));
    assert.equal(await readSeconds(),0);
    await assertCountdownHidden();

    // The server accepts the picks before expiry and delivers the reply after it.
    await load(fixtures.expiry_waiting.path);await chooseTesters();await review();
    modes.submit='hold-response';
    await evaluate('performance.now=()=>selectionDeadline-250;document.querySelector("#modalDone").click();');
    for(let i=0;i<80&&!heldSubmitResponse;i++)await wait(100);
    assert(heldSubmitResponse,'The submit response was not intercepted');
    await assertCountdownHidden();
    await evaluate('performance.now=()=>selectionDeadline+1;checkSelectionDeadline();');
    assert(await evaluate('submitBusy&&selectionState==="submitting"&&document.querySelector("#demoModal").getAttribute("aria-hidden")==="false"'));
    modes.submit='real';
    await send('Fetch.continueRequest',{requestId:heldSubmitResponse});
    await until('selectionState==="submitted"&&!submitBusy');
    assert(await evaluate('document.querySelector("#modalTitle").textContent.includes("موفقیت")'));
    await assertCountdownHidden();

    for(const [width,height] of [[1440,900],[390,844],[320,740]]){
      await load(fixtures.expiry_expired.path,width,height);
      assert(await evaluate('selectionState==="expired"&&document.querySelector("#collection").hidden&&document.querySelector("#mobileSelectionBar").hidden&&!canEditSelection()'));
      await assertCountdownHidden();
      assert(await evaluate('document.querySelector("#selectionNoticeTitle").textContent==="مهلت انتخاب تسترهای شما به پایان رسیده است."'));
      assert(await evaluate('document.querySelector("#selectionNoticeDefault").textContent.includes("متناسب با جنسیتی")&&document.documentElement.scrollWidth<=innerWidth'));
      await evaluate('scrollTo({top:0,behavior:"instant"})');await screenshot('expired-'+width);
    }
    // An expired incomplete order stays a support case, with no promise of a default box.
    for(const [width,height] of [[1440,900],[390,844]]){
      await load(fixtures.expiry_review.path,width,height);
      assert(await evaluate('selectionState==="review_required"&&!canEditSelection()&&document.querySelector("#collection").hidden&&document.querySelector("#mobileSelectionBar").hidden'));
      await assertCountdownHidden();
      assert(await evaluate('document.querySelector("#selectionNoticeDefault").hidden&&!document.querySelector("#selectionNoticeSupport").hidden'));
      assert(await evaluate('document.querySelector("#selectionNoticeTitle").textContent.includes("بررسی")&&!document.querySelector("#selectionNotice").innerText.includes("پک تستر پیش‌فرض")'));
      assert(await evaluate('document.documentElement.scrollWidth<=innerWidth'));
      const response=await evaluate(`(async()=>{const context=await selectionApi('context');const response=await fetch('/api/tester/selection/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:selectionToken,pids:context.selection.selected_pids})});return {status:response.status,body:await response.json()};})()`);
      assert.equal(response.status,409);
      assert.equal(response.body.detail.code,'selection_review_required');
      await evaluate('scrollTo({top:0,behavior:"instant"})');await screenshot('review-required-'+width);
    }
    // A live page and an open final modal lock without Refresh; Date.now cannot extend it.
    await load(fixtures.expiry_live.path);await chooseTesters();await review();
    setWindow('expiry_live',4);
    await evaluate('(async()=>{applySelectionContext(await selectionApi("context"));Date.now=()=>0;})()');
    assert(await evaluate('canEditSelection()&&document.querySelector("#demoModal").getAttribute("aria-hidden")==="false"'));
    await until('selectionState==="expired"');
    await assertCountdownHidden();
    assert(await evaluate('selected.length===MAX_TESTERS&&!canEditSelection()&&document.querySelector("#demoModal").getAttribute("aria-hidden")==="true"&&document.querySelector("#collection").hidden'));
    const beforeExpiredClick=submits;
    await evaluate('document.querySelector("#modalDone").click();toggleProduct(selected[0]);');
    assert.equal(submits,beforeExpiredClick);
    // Direct parallel HTTP requests still hit the real expired backend.
    const direct=await evaluate(`Promise.all(Array.from({length:3},async()=>{const response=await fetch('/api/tester/selection/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:selectionToken,pids:selected})});return {status:response.status,body:await response.json()};}))`);
    assert(direct.every(row=>row.status===410&&row.body.detail.code==='selection_expired'),JSON.stringify(direct));
    await load(fixtures.expiry_live.path);assert(await evaluate('selectionState==="expired"'));
    await load('/'+'9'.repeat(18));
    assert(await evaluate('selectionState==="error"&&document.querySelector("#orderFields").hidden&&document.querySelector("#collection").hidden'));
    await screenshot('invalid-link-mobile');
    await load('/');await chooseTesters();
    assert(await evaluate('selectionState==="preview"&&document.querySelector("#finalizeButton").disabled&&document.querySelector("#orderFields").hidden'));
    await assertCountdownHidden();
    assert.equal(errors.length,0,JSON.stringify(errors));
    assert(headers.length>0&&headers.every(h=>h.noStore&&h.noReferrer&&h.noIndex),'Missing privacy headers');
    fs.writeFileSync(path.join(artifacts,'browser-report.json'),JSON.stringify({responsive,privacyHeaders:headers.length,expiry:{responsiveWidths:[1440,390,320],liveModalLock:true,changedBrowserClock:true,directLateSubmit:410,awaitingAcceptance:true,expiredReviewNeedsSupport:true,noDefaultPackPromiseForReview:true},assertions:'passed'},null,2));
    console.log('PASS: real isolated Context/Submit, responsive countdown and controls, countdown refresh and visibility sync, warning thresholds, interval cleanup, submission across expiry, final review, readonly reload, lost-response recovery, ambiguous-network lock, inventory error, invalid link, preview, privacy headers, awaiting SMS, live deadline lock, and direct late submissions rejected.');
    await send('Browser.close');
  }finally{socket?.close();if(browser.exitCode===null)browser.kill();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

/* Start: python -m tests.selection_preview --port 8789
 * Uses only the preview's owned database and synthetic orders. No SMS calls.
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');

async function main() {
  const { fixtures, database } = JSON.parse(fs.readFileSync('test-artifacts/selection-preview.json', 'utf8'));
  assert(database.startsWith('tester_selection_preview_'));
  const base = process.env.SELECTION_TEST_BASE_URL || 'http://127.0.0.1:8789';
  assert(['localhost','127.0.0.1'].includes(new URL(base).hostname), 'Only a local isolated preview is allowed');
  const artifacts = path.resolve('test-artifacts/selection');
  fs.mkdirSync(artifacts, { recursive:true });
  const executable = process.env.CATALOG_BROWSER_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
  const browser = spawn(executable, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--remote-debugging-port=9232',`--user-data-dir=${path.join(artifacts,'edge-profile')}`,'about:blank'], { windowsHide:true, stdio:'ignore' });
  let socket;
  const wait = ms => new Promise(resolve=>setTimeout(resolve,ms));
  const modes = { context:'real', submit:'real' };
  let submits=0, catalogRequests=0;
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
      if(response){
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
        if(referer?.includes('/s/'))errors.push('Private URL leaked in Referer');
      }else if(m.method==='Network.responseReceived'){
        const response=m.params.response;
        const url=new URL(response.url);
        if(url.origin===new URL(base).origin&&(url.pathname.startsWith('/s/')||url.pathname.startsWith('/api/tester/selection/'))){
          const h=Object.fromEntries(Object.entries(response.headers).map(([k,v])=>[k.toLowerCase(),v]));
          if(response.status===200)headers.push({noStore:h['cache-control']==='no-store',noReferrer:h['referrer-policy']==='no-referrer',noIndex:h['x-robots-tag']?.includes('noindex')});
        }
      }
    };
    const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value;};
    const until=async expression=>{for(let i=0;i<180;i++){try{if(await evaluate(expression))return;}catch{}await wait(100);}throw new Error('Timed out: '+expression);};
    const click=async selector=>{
      const point=await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.scrollIntoView({block:'center',behavior:'instant'});const r=el.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};})()`);
      await send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
      await send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
    };
    const load=async(route,width=390,height=844)=>{
      await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<=680});
      await send('Page.navigate',{url:base+route});
      await until('typeof selectionState!=="undefined" && ["pending","submitted","error","preview"].includes(selectionState)');
      await evaluate('document.fonts.ready.then(()=>true)');
    };
    const screenshot=async name=>{const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(artifacts,name+'.png'),Buffer.from(shot.data,'base64'));};
    const chooseTen=async()=>{
      await until('catalogState==="ready"');
      await evaluate('for(const item of PERFUMES.slice(0,10)){if(!selectedHas(item.pid))document.querySelector(`[data-toggle="${CSS.escape(item.pid)}"]`).click();}');
      assert(await evaluate('selected.length===10&&document.querySelectorAll(".product-button:disabled").length>0'));
    };
    const review=async()=>{
      if(await evaluate('innerWidth<=940')){await click('#openDrawer');await click('#mobileFinalize');}
      else await click('#finalizeButton');
      await until('document.querySelector("#demoModal").getAttribute("aria-hidden")==="false"');
      assert(await evaluate('document.querySelectorAll("#modalSelected li").length===10'));
    };
    await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
    await send('Network.setCacheDisabled',{cacheDisabled:true});
    await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    await send('Fetch.enable',{patterns:[{urlPattern:'*/api/tester/selection/*',requestStage:'Request'},{urlPattern:'*/api/tester/selection/submit',requestStage:'Response'}]});

    for(const [width,height]of [[1440,1000],[768,1024],[390,844],[320,740]]){
      await load(fixtures.success.path,width,height);await until('catalogState==="ready"');
      assert.equal(await evaluate('document.querySelector("#orderPhone").textContent'),fixtures.success.phone);
      assert.equal(await evaluate('document.querySelector("#orderNumber").textContent'),fixtures.success.order_id);
      assert.equal(await evaluate('getComputedStyle(document.querySelector("#orderPhone")).direction'),'ltr');
      assert.equal(await evaluate('document.querySelector("#orderCustomerName").textContent'),'مشتری آزمایشی');
      assert(await evaluate('!document.querySelector("#orderFields").hidden&&document.documentElement.scrollWidth<=innerWidth'));
      await evaluate('scrollTo(0,0)');await screenshot('order-'+width);
      responsive.push({width,orderVisible:true,fullPhone:true,overflow:false});
    }
    // Existing details and the new order state must work together.
    await click('.product-card .product-name');await until('document.querySelector("#perfumeContent").dataset.state==="ready"');
    assert(await evaluate('document.querySelectorAll(".pd-trait").length===2'));
    await click('#perfumeSelect');assert(await evaluate('selected.length===1'));
    await click('#closePerfume');await until('!document.querySelector("#perfumeDialog").open');
    await chooseTen();await review();await screenshot('review-mobile');
    const before=submits;
    await evaluate('document.querySelector("#modalDone").click();document.querySelector("#modalDone").click()');
    await until('selectionState==="submitted"&&!submitBusy');
    assert.equal(submits-before,1,'Double click sent two requests');
    await screenshot('success-mobile');await click('#modalDone');
    for(const [width,height]of [[1440,1000],[390,844],[320,740]]){
      const catalogs=catalogRequests;
      await load(fixtures.success.path,width,height);
      assert(await evaluate('selectionState==="submitted"&&document.querySelectorAll("#submittedList li").length===10&&document.querySelector("#collection").hidden'));
      assert.equal(catalogRequests,catalogs,'Receipt unexpectedly fetched the live catalog');
      assert(await evaluate('document.querySelector("#submittedList").textContent.includes("Original Source Perfume")'));
      const selectedBefore=await evaluate('JSON.stringify(selected)');await evaluate('toggleProduct(selected[0])');
      assert.equal(await evaluate('JSON.stringify(selected)'),selectedBefore);
      assert(await evaluate('document.documentElement.scrollWidth<=innerWidth'));
      await evaluate('scrollTo(0,0)');await screenshot('submitted-'+width);
    }
    // The server committed, but its HTTP response was lost. Context proves success.
    await load(fixtures.lost_response.path);await chooseTen();await review();
    modes.submit='lose-response';await click('#modalDone');await until('selectionState==="submitted"&&!submitBusy');
    await click('#modalDone');
    // Both requests are unreachable. Lock choices until Context proves pending.
    await load(fixtures.uncertain.path);await chooseTen();await review();
    modes.submit='drop';modes.context='error';await click('#modalDone');await until('selectionState==="uncertain"&&!submitBusy');
    assert(await evaluate('!canEditSelection()&&document.querySelector("#submissionMessage").textContent.includes("مشخص نیست")'));
    const frozen=await evaluate('JSON.stringify(selected)');await evaluate('toggleProduct(selected[0])');assert.equal(await evaluate('JSON.stringify(selected)'),frozen);
    await click('#closeModal');modes.context='real';modes.submit='real';await click('#retrySelectionContext');
    await until('selectionState==="pending"');assert(await evaluate('selected.length===10&&canEditSelection()'));
    await review();await click('#modalDone');await until('selectionState==="submitted"&&!submitBusy');await click('#modalDone');
    // A rejected product gives a readable error and editable choices after Context.
    await load(fixtures.unavailable.path);await chooseTen();await review();modes.submit='unavailable';
    await click('#modalDone');await until('selectionState==="pending"&&!submitBusy&&!document.querySelector("#submissionMessage").hidden');
    assert(await evaluate('document.querySelector("#submissionMessage").textContent.includes("موجود نیست")&&canEditSelection()'));
    await click('#modalBack');modes.submit='real';
    await load('/s/not-a-valid-token');
    assert(await evaluate('selectionState==="error"&&document.querySelector("#orderFields").hidden&&document.querySelector("#collection").hidden'));
    await screenshot('invalid-link-mobile');
    await load('/');await chooseTen();
    assert(await evaluate('selectionState==="preview"&&document.querySelector("#finalizeButton").disabled&&document.querySelector("#orderFields").hidden'));
    assert.equal(errors.length,0,JSON.stringify(errors));
    assert(headers.length>0&&headers.every(h=>h.noStore&&h.noReferrer&&h.noIndex),'Missing privacy headers');
    fs.writeFileSync(path.join(artifacts,'browser-report.json'),JSON.stringify({responsive,privacyHeaders:headers.length,assertions:'passed'},null,2));
    console.log('PASS: real isolated Context/Submit, full phone, responsive, final review, double-click guard, readonly reload, saved names, lost-response recovery, ambiguous-network lock, inventory error, invalid link, preview blocked, privacy headers, no referrer/token leak.');
    await send('Browser.close');
  }finally{socket?.close();if(browser.exitCode===null)browser.kill();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const base=path.join(__dirname,'../src/issueintel/interfaces/static'),code=fs.readFileSync(path.join(base,'batches.js'),'utf8');
const messages=JSON.parse(fs.readFileSync(path.join(base,'locales.js'),'utf8').replace(/^window.IssueIntelMessages\s*=\s*/,'').replace(/;\s*$/,''));
const hash=c=>c.repeat(64),id=c=>c.repeat(24),clone=v=>JSON.parse(JSON.stringify(v));
function manifest(provider='none',run='batch-a'){return {schema:'product_batch_manifest_v1',run_key:run,items:[{key:'item-1',request:{mode:'example',provider,depth:'quick'}}]};}
function preview(value=manifest(),char='a'){
  const source={sha256:hash('3'),revision:'1'.repeat(40),case:{repository:'synthetic/example',problem_statement:'Parser fails for None <script>inert</script>'}};
  return {schema:'product_batch_preview_v1',manifest:clone(value),manifest_sha256:hash(char),policy_sha256:hash('4'),items:value.items.map(row=>({key:row.key,request_sha256:hash('5'),resolved_request:clone(row.request),source_sha256:hash('3'),source:clone(source),error:null,call_upper_bound:row.request.provider==='groq'?4:0})),can_start:true,requires_api:value.items.some(row=>row.request.provider==='groq'),currency:'USD',cost_bound_micro_usd:value.items.some(row=>row.request.provider==='groq')?null:0,concurrency:1,model_calls:0,sha256:hash(char==='a'?'b':'c')};
}
function read(p=preview(),status='completed',char='a'){
  const counts=Object.fromEntries(['pending','queued','running','completed','failed','cancelled','interrupted'].map(key=>[key,0]));counts[status]=p.items.length;counts.total=p.items.length;
  return {schema:'product_batch_v1',id:id(char),run_key:p.manifest.run_key,manifest_sha256:p.manifest_sha256,preview_sha256:p.sha256,policy_sha256:p.policy_sha256,
    created_at:'2026-10-23T00:00:00Z',updated_at:'2026-10-23T00:00:01Z',desired_state:'running',status:status==='pending'?'queued':status,requires_api:p.requires_api,concurrency:1,model_calls:0,counts,
    items:p.items.map((row,index)=>({key:row.key,job_id:id(String(index+1)),job_available:status!=='pending',request_sha256:row.request_sha256,source_sha256:row.source_sha256,mode:row.resolved_request.mode,provider:row.resolved_request.provider,depth:row.resolved_request.depth,revision:row.source.revision,status,stage:status,error:null})),sha256:hash('9')};
}
function boot(handler){
  const nodes=new Map(),events=new Map(),calls=[],dispatched=[],timers=[],language={value:'vi'};
  const all=item=>[item,...item.children.flatMap(all)],text=item=>(item.textContent||'')+' '+item.children.map(text).join(' ');
  const roots=()=>[...nodes.values()];let document;
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.disabled=false;this.checked=false;this.dataset={};}
    append(...rows){this.children.push(...rows);}replaceChildren(...rows){this.children=rows;}setAttribute(){}
    contains(item){return all(this).includes(item);}focus(){document.activeElement=this;}set innerHTML(_){throw Error('Source text must remain inert');}
  }
  for(const key of ['batch-panel','batch-form','batch-manifest','batch-file','batch-preview','batch-example','batch-history-refresh','batch-message','batch-preview-content','batch-detail','batch-history-list'])nodes.set(key,new Element());
  const get=key=>roots().flatMap(all).find(item=>item.id===key)||nodes.get(key);
  document={activeElement:null,getElementById:get,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>dispatched.push(event)};
  const window={IssueIntelUI:{t:(key,slots={})=>{assert.ok(messages[key]?.[language.value],'Missing batch translation: '+key);return messages[key][language.value].replace(/\{(\w+)\}/g,(_,slot)=>String(slots[slot]));}}};
  const api=async(route,body)=>{calls.push([route,clone(body??null)]);if(handler)return handler(route,body);if(route==='/api/batches/preview')return preview(JSON.parse(body.manifest_json));if(route==='/api/batches')return body?read(body.preview):{schema:'product_batch_history_v1',batches:[]};throw Error('Unexpected route '+route);};
  vm.runInNewContext(code,{window,document,api,TextEncoder,URL,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},setTimeout:fn=>{const row={fn,cancelled:false};timers.push(row);return row;},clearTimeout:row=>{row.cancelled=true;}});
  const click=identifier=>{const found=get(identifier);assert.ok(found,identifier);return found.onclick();};
  return {calls,dispatched,events,get,text,language,module:window.IssueIntelBatches,click,focused:()=>document.activeElement,
    fill:value=>{get('batch-manifest').value=typeof value==='string'?value:JSON.stringify(value);get('batch-manifest').oninput();},
    preview:()=>get('batch-form').onsubmit({preventDefault(){}}),toggle:identifier=>{const input=get(identifier);input.checked=!input.checked;return input.onchange();},
    history:()=>click('batch-history-refresh'),open:index=>get('batch-history-list').children[index].onclick(),
    tick:()=>{const row=timers.find(row=>!row.cancelled);assert.ok(row,'A live batch poll');row.cancelled=true;return row.fn();}};
}

test('synthetic manifest and preview are deliberate, default offline, inert and zero-model declarations',async()=>{
  const a=boot();assert.equal(a.calls.length,0);a.click('batch-example');assert.equal(a.calls.length,0);assert.match(a.get('batch-manifest').value,/"provider": "none"/);
  a.fill(manifest());await a.preview();assert.equal(a.calls.length,1);assert.equal(typeof a.calls[0][1].manifest_json,'string');
  assert.equal(a.get('batch-start').disabled,false);assert.match(a.text(a.get('batch-preview-content')),/<script>inert<\/script>/);assert.match(a.text(a.get('batch-preview-content')),/0 USD/);
});
test('Groq requires exact reviewed-preview consent; edit or language change invalidates consent without paid submit',async()=>{
  const a=boot();a.fill(manifest('groq'));await a.preview();assert.equal(a.get('batch-start').disabled,true);await a.click('batch-start');assert.equal(a.calls.length,1);
  a.toggle('batch-groq-consent');assert.equal(a.get('batch-start').disabled,false);a.language.value='en';a.events.get('issueintel:languagechange')();assert.equal(a.get('batch-groq-consent').checked,false);assert.equal(a.get('batch-start').disabled,true);
  a.toggle('batch-groq-consent');const stale=a.get('batch-start');a.fill(manifest('groq','batch-b'));await stale.onclick();assert.equal(a.get('batch-start'),undefined);assert.equal(a.calls.length,1);
});
test('start sends exact frozen manifest/preview once, while completion never opens a report automatically',async()=>{
  let release;const a=boot((route,body)=>{if(route.endsWith('/preview'))return preview(JSON.parse(body.manifest_json));if(body)return new Promise(resolve=>release=()=>resolve(read(body.preview)));return {schema:'product_batch_history_v1',batches:[]};});
  a.fill(manifest('groq'));await a.preview();a.toggle('batch-groq-consent');const start=a.get('batch-start'),pending=start.onclick();await start.onclick();assert.equal(a.calls.filter(row=>row[0]==='/api/batches'&&row[1]).length,1);
  const sent=a.calls[1][1];assert.equal(sent.confirmed,true);assert.equal(sent.allow_api_calls,true);assert.deepEqual(sent.preview,preview(manifest('groq')));release();await pending;
  assert.equal(a.dispatched.length,0);assert.equal(a.get('batch-start').disabled,true);assert.equal(a.get('batch-detail').dataset.batchId,id('a'));
  a.click('batch-item-open-item-1');assert.equal(a.dispatched[0].type,'issueintel:batchchildopen');assert.equal(a.dispatched[0].detail.job_id,id('1'));
});
test('lost acknowledgement locks the exact run_key and reconciles by read only without another POST',async()=>{
  const p=preview(manifest()),saved=read(p);const a=boot((route,body)=>{if(route.endsWith('/preview'))return p;if(route==='/api/batches'&&body)throw Error('Lost acknowledgement');return route==='/api/batches'?{schema:'product_batch_history_v1',batches:[saved]}:saved;});
  a.fill(p.manifest);await a.preview();await a.click('batch-start');assert.equal(a.get('batch-manifest').disabled,true);assert.ok(a.get('batch-reconcile'));
  await a.click('batch-reconcile');assert.equal(a.calls.filter(row=>row[0]==='/api/batches'&&row[1]).length,1);assert.equal(a.get('batch-manifest').disabled,false);assert.equal(a.get('batch-detail').dataset.batchId,saved.id);
});
test('opening saved batches is read-only; late preview and create acknowledgement cannot replace a newer batch',async()=>{
  const p=preview(),other=read(preview(manifest('none','batch-b'),'d'),'completed','c');let finish;
  const a=boot((route,body)=>{if(route.endsWith('/preview'))return p;if(body)return new Promise(resolve=>finish=()=>resolve(read(p)));return route==='/api/batches'?{schema:'product_batch_history_v1',batches:[other]}:other;});
  a.fill(p.manifest);await a.preview();const pending=a.click('batch-start');await a.history();await a.open(0);finish();await pending;
  assert.equal(a.get('batch-detail').dataset.batchId,other.id);assert.equal(a.dispatched.length,0);
  let complete;const b=boot(()=>new Promise(resolve=>complete=resolve));b.fill(p.manifest);const old=b.preview();b.fill(manifest('none','changed'));complete(p);await old;assert.equal(b.get('batch-start'),undefined);
});
test('stale poll, cancellation response and detached item controls cannot act on the newer batch',async()=>{
  const first=read(preview(),'running'),second=read(preview(manifest('none','batch-b'),'d'),'completed','c');let held;
  const a=boot((route,body)=>{if(route==='/api/batches')return {schema:'product_batch_history_v1',batches:[first,second]};if(route===`/api/batches/${first.id}`)return first;if(body)return new Promise(resolve=>held=()=>resolve(first));return second;});
  await a.history();await a.open(0);const staleCancel=a.get('batch-item-cancel-item-1'),pending=a.click('batch-pause');await a.open(1);held();await pending;await staleCancel.onclick();
  assert.equal(a.get('batch-detail').dataset.batchId,second.id);assert.equal(a.calls.filter(row=>row[1]).length,1);
});
test('restart pending batch exposes deliberate continue and renewed Groq consent; cancellation stays requested while active',async()=>{
  const row=read(preview(manifest('groq')),'pending');let desired=row;
  const a=boot((route,body)=>{if(route==='/api/batches')return {schema:'product_batch_history_v1',batches:[desired]};if(body){if(route.endsWith('/continue'))desired=read(preview(manifest('groq')),'running');else desired={...desired,desired_state:'cancelled'};}return desired;});
  await a.history();await a.open(0);assert.equal(a.get('batch-continue').disabled,true);a.toggle('batch-continue-consent');await a.click('batch-continue');assert.equal(a.calls.at(-1)[1].allow_api_calls,true);
  await a.click('batch-cancel');assert.match(a.text(a.get('batch-detail')),/Đã yêu cầu hủy lô/);assert.equal(a.get('batch-cancel'),undefined);
});
test('restart with a prepared queued child offers explicit continuation without changing its fixed job ID',async()=>{
  const row=read(preview(),'queued'),a=boot(route=>route==='/api/batches'?{schema:'product_batch_history_v1',batches:[row]}:row);
  await a.history();await a.open(0);assert.equal(a.get('batch-continue').disabled,false);assert.match(a.text(a.get('batch-detail')),new RegExp(id('1')));
  assert.equal(a.calls.every(call=>call[1]===null),true);
});
test('poll preserves keyboard focus and counts, without changing the selected child report',async()=>{
  const row=read(preview(),'running'),a=boot(route=>route==='/api/batches'?{schema:'product_batch_history_v1',batches:[row]}:row);
  await a.history();await a.open(0);a.get('batch-pause').focus();await a.tick();assert.equal(a.focused(),a.get('batch-pause'));assert.equal(a.dispatched.length,0);
  assert.equal(a.text(a.get('batch-detail')).includes('1'),true);
});
test('selected refresh and exact-bound actions keep saved history status/counts in sync',async()=>{
  const p=preview(),queued=read(p,'queued'),done=read(p);let latest=queued;
  const a=boot(route=>route==='/api/batches'?{schema:'product_batch_history_v1',batches:[queued]}:latest);
  await a.history();await a.open(0);assert.equal(a.get('batch-history-list').children[0].dataset.batchStatus,'queued');
  latest=done;await a.tick();const row=a.get('batch-history-list').children[0];
  assert.equal(row.dataset.batchStatus,'completed');assert.match(a.text(row),/1\/1/);assert.equal(a.get('batch-detail').dataset.batchStatus,'completed');
  const pending=read(p,'pending'),paused={...pending,status:'paused',desired_state:'paused'};
  const b=boot((route,body)=>route==='/api/batches'?{schema:'product_batch_history_v1',batches:[pending]}:body?paused:pending);
  await b.history();await b.open(0);await b.click('batch-pause');assert.equal(b.get('batch-history-list').children[0].dataset.batchStatus,'paused');
});
test('approved source cards collapse only after acceptance and remain inspectable across language changes',async()=>{
  const a=boot((route,body)=>{if(route.endsWith('/preview')){const m=JSON.parse(body.manifest_json);return preview(m,m.run_key==='batch-new'?'d':'a');}return body?read(body.preview):{schema:'product_batch_history_v1',batches:[]};});
  a.fill(manifest());await a.preview();assert.equal(a.get('batch-approved-preview'),undefined);
  await a.click('batch-start');assert.equal(a.get('batch-approved-preview').open,false);assert.match(a.text(a.get('batch-approved-preview')),/<script>inert<\/script>/);
  const details=a.get('batch-approved-preview');details.open=true;details.ontoggle();a.language.value='en';a.events.get('issueintel:languagechange')();
  assert.equal(a.get('batch-approved-preview').open,true);assert.match(a.text(a.get('batch-approved-preview')),/Review 1 approved sources/);
  a.fill(manifest('none','batch-new'));await a.preview();assert.equal(a.get('batch-approved-preview'),undefined);
});
test('failed import during an old preview clears busy state; edited text cannot retain paid authority',async()=>{
  let finish;const a=boot(()=>new Promise(resolve=>finish=resolve));a.fill(manifest());const old=a.preview();
  await a.get('batch-file').onchange({target:{files:[{size:600000,text:async()=>''}],value:'file'}});assert.equal(a.get('batch-preview').disabled,false);
  finish(preview());await old;assert.equal(a.get('batch-start'),undefined);assert.match(a.text(a.get('batch-message')),/vượt giới hạn/);
});
test('normal defaults and canonical local/GitHub revisions match frozen preview without weakening source checks',()=>{
  const a=boot(),m={schema:'product_batch_manifest_v1',run_key:'defaults',items:[{key:'one',request:{mode:'github',issue_url:'https://github.com/Org/Repo/issues/12'}}]};
  const canonical=clone(m);canonical.items[0].request={mode:'github',issue_url:'https://github.com/org/repo/issues/12',revision:'HEAD',provider:'none',depth:'quick'};
  const p=preview(canonical);p.items[0].resolved_request.revision=p.items[0].source.revision;assert.equal(a.module.validPreview(p,m),true);
  const changed=clone(p);changed.items[0].resolved_request.provider='groq';assert.equal(a.module.validPreview(changed,m),false);
  const local={schema:'product_batch_manifest_v1',run_key:'local',items:[{key:'one',request:{repository:' /tmp/demo ',revision:' main ',issue_text:'query'}}]};
  const expected=clone(local);expected.items[0].request={mode:'local',repository:'/tmp/demo',revision:'main',issue_text:'query',provider:'none',depth:'quick'};
  const lp=preview(expected);lp.items[0].resolved_request.revision=lp.items[0].source.revision;assert.equal(a.module.validPreview(lp,local),true);
});
test('changed batch scope, child identity, counters or provider flags cannot be accepted as the viewed batch',()=>{
  const a=boot(),row=read();for(const mutate of [r=>r.manifest_sha256=hash('e'),r=>r.items[0].job_id=id('e'),r=>r.items[0].source_sha256=hash('e'),r=>r.counts.total=2,r=>r.concurrency=2,r=>r.model_calls=1]){const changed=clone(row);mutate(changed);assert.equal(a.module.validRead(changed,row),false);}
});
test('a cancelled undispatched item keeps its stable child ID but offers no nonexistent job to open',async()=>{
  const row=read(preview(),'cancelled');row.items[0].job_available=false;const a=boot(route=>route==='/api/batches'?{schema:'product_batch_history_v1',batches:[row]}:row);
  await a.history();await a.open(0);assert.match(a.text(a.get('batch-detail')),new RegExp(id('1')));assert.equal(a.get('batch-item-open-item-1'),undefined);assert.equal(a.dispatched.length,0);
});
test('unavailable saved batches are reported by bounded ID while valid saved batches still open read-only',async()=>{
  const row=read(),unavailable=id('e'),a=boot(route=>route==='/api/batches'?{schema:'product_batch_history_v1',batches:[row],errors:[{id:unavailable,code:'source_unavailable'}]}:row);
  await a.history();assert.match(a.text(a.get('batch-history-list')),new RegExp(unavailable));assert.match(a.text(a.get('batch-history-list')),/các lô hợp lệ vẫn mở được/);
  await a.open(0);assert.equal(a.get('batch-detail').dataset.batchId,row.id);assert.equal(a.calls.every(call=>call[1]===null),true);
  a.language.value='en';a.events.get('issueintel:languagechange')();assert.match(a.text(a.get('batch-history-list')),/valid batches remain available/);
});
test('malformed unavailable-history IDs, codes and oversized inventories do not become selectable batches',async()=>{
  for(const errors of [[{id:'../private',code:'source_unavailable'}],[{id:id('e'),code:'invented'}],Array.from({length:51},()=>({id:id('e'),code:'source_unavailable'}))]){
    const a=boot(()=>({schema:'product_batch_history_v1',batches:[read()],errors}));await a.history();assert.equal(a.get('batch-history-list').children.length,0);assert.match(a.text(a.get('batch-message')),/Chưa đọc được danh sách/);
  }
});
test('batch labels, source wrapping and mobile controls are wired with complete bilingual translations',()=>{
  for(const match of code.matchAll(/\bt\('([^']+)'/g))assert.ok(messages[match[1]]?.vi&&messages[match[1]]?.en,match[1]);
  const html=fs.readFileSync(path.join(base,'index.html'),'utf8'),css=fs.readFileSync(path.join(base,'batches.css'),'utf8');
  for(const name of ['batch-panel','batch-manifest','batch-file','batch-preview','batch-history-list','batch-detail'])assert.ok(html.includes('id="'+name+'"'));
  assert.match(html,/src="\/batches.js"/);assert.match(css,/@media\(max-width:740px\)/);assert.match(css,/overflow-wrap:anywhere/);
});

test('batch child navigation reads the selected child and cannot start work or overwrite a newer selected view',async()=>{
  const app=fs.readFileSync(path.join(base,'app.js'),'utf8'),start=app.indexOf("document.addEventListener('issueintel:batchchildopen'"),end=app.indexOf('loadTypesafeStatus();',start);
  for(const status of ['completed','failed','running']){
    const events=new Map(),opened=[],shown=[],cost=[],context=vm.createContext({document:{addEventListener:(name,fn)=>events.set(name,fn)},viewGeneration:0,
      api:async()=>({id:id('1'),status}),openJob:async value=>opened.push(value),showStageArtifacts:row=>shown.push(row.id),showUsage:row=>cost.push(row.id),
      message(){},t:key=>key,runJob(){throw Error('Batch opening cannot start or resume work');}});
    vm.runInContext(app.slice(start,end),context);await events.get('issueintel:batchchildopen')({detail:{job_id:id('1')}});
    assert.deepEqual(opened,status==='completed'?[id('1')]:[]);assert.deepEqual(shown,status==='completed'?[]:[id('1')]);assert.deepEqual(cost,shown);
  }
  const events=new Map();let finish;const context=vm.createContext({document:{addEventListener:(name,fn)=>events.set(name,fn)},viewGeneration:0,api:()=>new Promise(resolve=>finish=resolve),
    openJob(){throw Error('Stale navigation');},showStageArtifacts(){throw Error('Stale stages');},showUsage(){throw Error('Stale accounting');},message(){throw Error('Stale message');}});
  vm.runInContext(app.slice(start,end),context);const pending=events.get('issueintel:batchchildopen')({detail:{job_id:id('1')}});context.viewGeneration++;finish({id:id('1'),status:'failed'});await pending;
});

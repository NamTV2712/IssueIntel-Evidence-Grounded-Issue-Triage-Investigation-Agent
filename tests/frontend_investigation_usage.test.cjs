const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const staticDir=path.join(__dirname,'../src/issueintel/interfaces/static');
const code=fs.readFileSync(path.join(staticDir,'investigation-usage.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(staticDir,'locales.js'),'utf8'),locale);
const messages=locale.window.IssueIntelMessages,job=c=>c.repeat(24),sha=c=>c.repeat(64),selected=job('a'),parent=job('b'),packSha=sha('2');
const clone=value=>JSON.parse(JSON.stringify(value));
function provider(name,owners,amount=0,extra={}){return {provider:name,applicable:true,owner_job_ids:owners,tracking_available:true,attempted_calls:amount?1:0,
 captured_receipt_records:amount?1:0,reservations:amount?1:0,duplicate_captures:0,conflicting_reservations:0,invalid_receipts:0,retained_bounds:0,usage_anomalies:0,unsettled_reservations:0,
 inventory_complete:true,all_usage_settled:true,known_micro_usd_charged_or_reserved:amount,complete_micro_usd_charged_or_reserved:amount,gap_codes:[],...extra};}
function scope(ids,groq=0,typesafe=0){return {job_ids:ids,providers:{groq:provider('groq',ids,groq),typesafe:provider('typesafe',ids,typesafe)},inventory_complete:true,all_usage_settled:true,known_micro_usd_charged_or_reserved:groq+typesafe,complete_micro_usd_charged_or_reserved:groq+typesafe};}
function fixture(id=selected,{pack=packSha,status='completed'}={}){
 const current=scope([id]),lineage=scope([parent,id],12000,1000),snapshot={sha256:sha('3'),captured_job_ids:[parent,id]};
 return {schema:'investigation_usage_v1',source:{selected_job_id:id,selected_pack_sha256:pack,snapshot_sha256:snapshot.sha256},snapshot,
 owners:[parent,id].map(owner=>({job_id:owner,mode:owner===id?'finalization':'local',job_status:owner===id?status:'failed',provider:owner===id?'none':'groq',request_sha256:sha('4'),job_sha256:sha('5'),pack_sha256:owner===id?pack:null,providers:clone(owner===id?current.providers:lineage.providers)})),
 current,lineage,gaps:[],currency:'USD',captured_receipts_only:true,is_provider_invoice:false,absence_of_uncaptured_calls_verified:false,consistency_basis:'declared_captured_local_accounting',limitations:['Captured local declarations only.'],sha256:sha('6')};
}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function boot(handler=()=>fixture(),exporter){
 const calls=[],exports=[],downloads=[],listeners={},language={value:'vi'},meta={content:'session-one'};let document;
 class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.attributes={};this.isConnected=true;this.parent=null;}
  append(...nodes){for(const node of nodes){node.parent=this;node.isConnected=this.isConnected;this.children.push(node);}}
  replaceChildren(...nodes){for(const child of this.children)disconnect(child);this.children=[];this.append(...nodes);}
  setAttribute(key,value){this.attributes[key]=String(value);}getAttribute(key){return this.attributes[key]??null;}
  contains(other){return !!other&&(this===other||this.children.some(c=>c.contains(other)));}
  focus(){document.activeElement=this;}click(){downloads.push(this.download||'clicked');}
  set innerHTML(_){throw Error('Accounting text must remain inert');}
 }
 const disconnect=e=>{e.isConnected=false;for(const child of e.children)disconnect(child);};
 const all=e=>[e,...e.children.flatMap(all)],text=e=>e.textContent+' '+e.children.map(text).join(' ');
 document={createElement:tag=>new Element(tag),querySelector:()=>meta,addEventListener:(type,fn)=>listeners[type]=fn,activeElement:null};
 const context={window:{IssueIntelUI:{t:(key,values={})=>{assert.ok(messages[key]?.[language.value],'Missing locale '+key);return messages[key][language.value].replace(/\{(\w+)\}/g,(_,name)=>values[name]);}}},document,
 api:async(route,body)=>{calls.push([route,body]);return handler(route,body);},fetch:async(route,options)=>{exports.push([route,options]);return exporter?exporter(route,options):response(fixture());},
 URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:accounting';},revokeObjectURL:()=>{}},Blob:class{constructor(parts,options){this.parts=parts;this.type=options?.type;}},setTimeout:fn=>fn()};
 vm.runInNewContext(code,context);
 const component=context.window.IssueIntelInvestigationUsage,target=new Element();
 const render=(id=selected,options={packSha},where=target)=>component.render(where,id,options);
 return {target,component,context,document,meta,language,calls,exports,downloads,all,text,render,Element,disconnect,
 button:(label,where=target)=>all(where).find(e=>e.tag==='button'&&e.textContent===label),
 emit:type=>listeners[type]?.(),flush:async()=>{for(let n=0;n<9;n++)await Promise.resolve();}};
}
function response(value,{accounting=value.sha256,pack=value.source.selected_pack_sha256,ok=true,body=value}={}){return {ok,headers:{get:name=>name==='X-IssueIntel-Accounting-SHA256'?accounting:name==='X-IssueIntel-Source-Pack-SHA256'?pack:null},json:async()=>body,blob:async()=>({type:'text/markdown',value:'captured snapshot'})};}
function section(app,heading){return app.all(app.target).find(e=>e.tag==='section'&&e.children.some(c=>c.tag==='h4'&&c.textContent===heading));}
function fieldValue(app,container,label){return app.all(container).find(e=>e.children.some(c=>c.tag==='dt'&&c.textContent===label))?.children.find(c=>c.tag==='dd')?.textContent;}

test('finalization shows current zero and historical parent costs separately by provider, not arbitrary dossier runs',async()=>{
 const app=boot();app.render();await app.flush();
 const current=section(app,'Phiên hiện tại'),lineage=section(app,'Toàn chuỗi điều tra');
 assert.equal(fieldValue(app,current,'Tổng trong phạm vi đã ghi'),'$0.000000');assert.equal(fieldValue(app,lineage,'Tổng trong phạm vi đã ghi'),'$0.013000');
 assert.match(app.text(lineage),/Groq/);assert.match(app.text(lineage),/Jev \/ Typesafe/);assert.match(app.text(lineage),/\$0.012000/);assert.match(app.text(lineage),/\$0.001000/);
 assert.match(app.text(app.target),/không phải hóa đơn/);assert.match(app.text(app.target),/Không cộng các nhánh khác hoặc chi phí của issue ứng viên/);
 assert.ok(!app.text(app.target).includes(job('c')));assert.equal(app.calls.length,1);assert.equal(app.calls[0][1],undefined);
});
test('unknown complete totals stay unknown even when the known subtotal is zero',async()=>{
 const value=fixture();for(const s of [value.current,value.lineage]){s.inventory_complete=false;s.all_usage_settled=false;s.complete_micro_usd_charged_or_reserved=null;s.known_micro_usd_charged_or_reserved=0;
  for(const p of ['groq','typesafe'])s.providers[p]=provider(p,s.job_ids,0,{tracking_available:false,attempted_calls:null,inventory_complete:false,all_usage_settled:false,complete_micro_usd_charged_or_reserved:null,gap_codes:['tracking_missing']});}
 value.gaps=[{code:'tracking_missing',job_id:parent,provider:'groq',detail:'Receipt inventory unavailable <script> remains inert.'}];
 const app=boot(()=>value);app.render();await app.flush();for(const label of ['Phiên hiện tại','Toàn chuỗi điều tra'])assert.equal(fieldValue(app,section(app,label),'Tổng trong phạm vi đã ghi'),'Chưa biết');
 assert.match(app.text(app.target),/không phải tổng đầy đủ/);assert.match(app.text(app.target),/phần thiếu là 0/);assert.match(app.text(app.target),/<script>/);assert.match(app.text(app.target),/tracking_missing/);
});
test('known accounting gaps explain unknowns in Vietnamese and English while preserving original technical details',async()=>{
 const gaps={tracking_unavailable:['chưa ghi receipt đầy đủ','incomplete receipt tracking'],attempt_count_unknown:['Chưa biết số lượt đã thử','attempt count is unknown'],
  attempt_receipt_count_mismatch:['Số lượt đã thử không khớp','attempt count does not match'],invalid_receipt:['receipt không hợp lệ','invalid receipt'],
  receipt_identity_conflict:['cùng reservation mâu thuẫn','same reservation conflict'],missing_parent_binding:['Thiếu liên kết nguồn','lacks its source binding'],
  lineage_source_unavailable:['nguồn của một phiên','source run in the investigation chain'],sidecar_source_unavailable:['nguồn của một tác vụ phụ','sidecar\'s source'],
  sidecar_registry_unavailable:['danh sách tác vụ phụ','sidecar registry'],source_pack_unavailable:['Pack nguồn chưa có','source pack is absent'],
  attempt_inventory_invalid:['Danh sách lượt thử không hợp lệ','attempt inventory is invalid'],receipt_usage_breach:['Usage vượt cận','Usage exceeds the captured bound']};
 const value=fixture();value.gaps=Object.keys(gaps).map(code=>({code,job_id:parent,provider:'groq',detail:'Original English collector detail: '+code}));
 value.gaps.push({code:'future_gap_code',job_id:null,provider:null,detail:'Future untranslated source detail <script> stays inert.'});
 value.gaps.push({code:'constructor',job_id:null,provider:null,detail:'Unknown prototype-named code preserves the original collector detail.'});
 const app=boot(()=>value);app.render();await app.flush();
 for(const [language,index] of [['vi',0],['en',1]]){app.language.value=language;app.emit('issueintel:languagechange');const content=app.text(app.target);
  for(const [code,phrases] of Object.entries(gaps)){assert.ok(content.includes(phrases[index]),language+' friendly gap '+code);assert.ok(content.includes(code));assert.ok(content.includes('Original English collector detail: '+code));}
  const technical=app.all(app.target).filter(e=>e.tag==='details'&&e.children.some(c=>c.tag==='summary'&&c.textContent===(language==='vi'?'Chi tiết kỹ thuật gốc':'Original technical details')));
  assert.equal(technical.length,Object.keys(gaps).length);assert.ok(technical.every(e=>e.open!==true));assert.match(content,/Future untranslated source detail <script> stays inert/);
  assert.match(content,/Unknown prototype-named code preserves the original collector detail/);
 }
 assert.equal(app.calls.length,1);
});
test('complete captured inventory can retain upper bounds without claiming settled usage or an invoice',async()=>{
 const value=fixture();value.lineage.all_usage_settled=false;Object.assign(value.lineage.providers.groq,{retained_bounds:1,unsettled_reservations:1,all_usage_settled:false,duplicate_captures:2});
 const app=boot(()=>value);app.render();await app.flush();const lineage=section(app,'Toàn chuỗi điều tra');
 assert.equal(fieldValue(app,lineage,'Tổng trong phạm vi đã ghi'),'$0.013000');assert.match(app.text(lineage),/Còn cận giữ chỗ/);assert.match(app.text(lineage),/duplicate_captures/);assert.equal(fieldValue(app,lineage,'Còn giữ cận trên'),'1');
});
test('failed and running jobs can read and export costs without a completed source pack',async()=>{
 for(const status of ['failed','interrupted','running']){const value=fixture(selected,{pack:null,status}),app=boot(()=>value,()=>response(value));app.render(selected,{});await app.flush();
  assert.match(app.text(app.target),/Chưa có pack hoàn tất/);assert.ok(section(app,'Toàn chuỗi điều tra'));await app.button('Xuất chi phí chuỗi JSON').onclick();assert.equal(app.exports.length,1);assert.equal(app.downloads.length,2);assert.equal(app.calls.length,1);}
});
test('binding and malformed summary failures expose unknown metadata instead of invented totals',async()=>{
 const mutations=[v=>v.source.selected_job_id=parent,v=>v.source.selected_pack_sha256=sha('9'),v=>v.source.snapshot_sha256=sha('9'),v=>v.current.job_ids=[parent],v=>v.lineage.job_ids=[parent],v=>v.current.complete_micro_usd_charged_or_reserved=null,v=>v.current.providers.groq.known_micro_usd_charged_or_reserved=-1,v=>v.is_provider_invoice=true,v=>v.absence_of_uncaptured_calls_verified=true];
 for(const mutate of mutations){const value=fixture();mutate(value);const app=boot(()=>value);app.render();await app.flush();assert.match(app.text(app.target),/Chi phí thiếu vẫn là chưa biết/);assert.equal(app.button('Xuất chi phí chuỗi JSON'),undefined);assert.ok(!app.text(app.target).includes('$'));}
});
test('two visible panels share one local read and language/theme rendering never calls a model',async()=>{
 const request=deferred(),app=boot(()=>request.promise),second=new app.Element();app.render(selected,{packSha,revision:'failed:one'});app.render(selected,{packSha},second);assert.equal(app.calls.length,1);
 request.resolve(fixture());await app.flush();assert.ok(section(app,'Phiên hiện tại'));assert.match(app.text(second),/Toàn chuỗi điều tra/);
 app.language.value='en';app.emit('issueintel:languagechange');app.emit('issueintel:themechange');app.render(selected,{packSha,revision:'failed:one'});await app.flush();
 assert.equal(app.calls.length,1);assert.match(app.text(app.target),/Current run/);assert.match(app.text(second),/Full investigation chain/);assert.match(app.text(app.target),/Known cost subtotal/);
});
test('explicit refresh reads only local accounting, preserves focused action and does not duplicate an in-flight read',async()=>{
 let next=fixture(),request;const app=boot(()=>request?request.promise:next);app.render();await app.flush();request=deferred();const button=app.button('Làm mới chi phí đã ghi');button.focus();const pending=button.onclick();
 assert.equal(app.calls.length,2);assert.equal(app.document.activeElement,app.button('Làm mới chi phí đã ghi'));assert.equal(app.document.activeElement.getAttribute('aria-disabled'),'true');await app.document.activeElement.onclick();assert.equal(app.calls.length,2);
 next=fixture();next.sha256=sha('7');request.resolve(next);await pending;assert.equal(app.document.activeElement,app.button('Làm mới chi phí đã ghi'));assert.equal(app.document.activeElement.getAttribute('aria-disabled'),'false');assert.ok(app.calls.every(([,body])=>body===undefined));
});
test('a receipt revision changed during an in-flight read queues one fresh snapshot for the visible run',async()=>{
 const request=deferred(),updated=fixture();updated.sha256=sha('7');updated.current=scope([selected],1807,0);updated.current.all_usage_settled=false;updated.current.providers.groq.retained_bounds=1;
 let reads=0;const app=boot(()=>++reads===1?request.promise:updated);app.render(selected,{packSha,revision:'running:old'});
 app.render(selected,{packSha,revision:'failed:new'});app.render(selected,{packSha,revision:'failed:newer'});assert.equal(app.calls.length,1);
 request.resolve(fixture());await app.flush();assert.equal(app.calls.length,2);assert.equal(fieldValue(app,section(app,'Phiên hiện tại'),'Tổng trong phạm vi đã ghi'),'$0.001807');
 app.render(selected,{packSha,revision:'failed:newer'});await app.flush();assert.equal(app.calls.length,2);
});
test('queued revision refresh is dropped if the run is no longer visible',async()=>{
 const request=deferred(),app=boot(route=>route.includes(selected)?request.promise:fixture(parent));app.render(selected,{packSha,revision:'running:old'});app.render(selected,{packSha,revision:'failed:new'});
 app.render(parent,{packSha});await app.flush();request.resolve(fixture());await app.flush();assert.equal(app.calls.length,2);assert.equal(app.calls.filter(([route])=>route.includes(selected)).length,1);
 assert.match(app.text(app.target),new RegExp('Chi phí của phiên: '+parent));
});
test('late old source results and errors cannot repaint the newly selected run',async()=>{
 for(const reject of [false,true]){const request=deferred(),app=boot(route=>route.includes(selected)?request.promise:fixture(parent));app.render();app.render(parent,{packSha});await app.flush();
  if(reject)request.reject(Error('old read failed'));else request.resolve(fixture());await app.flush();assert.match(app.text(app.target),new RegExp('Chi phí của phiên: '+parent));assert.ok(!app.text(app.target).includes('đúng phiên. Chi phí thiếu'));assert.equal(app.button('Xuất chi phí chuỗi JSON')!==undefined,true);}
});
test('cancelled tab and changed session ignore late local reads',async()=>{
 const old=deferred(),app=boot(()=>old.promise);app.render();app.component.cancel(app.target);app.target.replaceChildren();old.resolve(fixture());await app.flush();assert.equal(app.target.children.length,0);
 const sessionRead=deferred(),other=boot(()=>sessionRead.promise);other.render();other.meta.content='session-two';sessionRead.resolve(fixture());await other.flush();assert.ok(!other.text(other.target).includes('Toàn chuỗi điều tra'));
 other.render();await other.flush();assert.equal(other.calls.length,2);assert.match(other.text(other.target),/Toàn chuỗi điều tra/);
});
test('JSON and Markdown exports verify the shown accounting/source headers with no inference',async()=>{
 const app=boot();app.render();await app.flush();for(const format of ['JSON','Markdown'])await app.button('Xuất chi phí chuỗi '+format).onclick();
 assert.equal(app.exports.length,2);assert.equal(app.calls.length,1);assert.ok(app.exports.every(([route,options])=>/investigation-usage\.(json|md)$/.test(route)&&options.headers['X-IssueIntel-Token']==='session-one'&&!options.body));assert.equal(app.downloads.length,4);
});
test('changed accounting/source hashes or JSON snapshot are rejected and require deliberate refresh',async()=>{
 const value=fixture(),variants=[{accounting:sha('9')},{pack:sha('9')},{pack:null},{body:{...clone(value),sha256:sha('9')}},{body:{...clone(value),source:{...value.source,snapshot_sha256:sha('8')}}}];
 for(const variant of variants){const app=boot(()=>value,()=>response(value,variant));app.render();await app.flush();await app.button('Xuất chi phí chuỗi JSON').onclick();assert.equal(app.downloads.length,0);assert.match(app.text(app.target),/Làm mới trước khi xuất lại/);assert.equal(app.calls.length,1);}
});
test('late export success or failure cannot download or show notices for another source or session',async()=>{
 for(const failure of [false,true]){const request=deferred(),app=boot(route=>fixture(route.includes(parent)?parent:selected),()=>request.promise);app.render();await app.flush();const pending=app.button('Xuất chi phí chuỗi JSON').onclick();app.render(parent,{packSha});await app.flush();
  if(failure)request.reject(Error('Old export failed'));else request.resolve(response(fixture()));await pending;assert.equal(app.downloads.length,0);assert.ok(!app.text(app.target).includes('Làm mới trước khi xuất lại'));}
 const request=deferred(),app=boot(()=>fixture(),()=>request.promise);app.render();await app.flush();const pending=app.button('Xuất chi phí chuỗi Markdown').onclick();app.meta.content='session-two';request.resolve(response(fixture()));await pending;assert.equal(app.downloads.length,0);
});
test('responsive component uses shared theme tokens and native accessible controls',async()=>{
 const css=fs.readFileSync(path.join(staticDir,'investigation-usage.css'),'utf8'),app=boot();app.render();await app.flush();
 for(const button of app.all(app.target).filter(e=>e.tag==='button'))assert.equal(button.type,'button');
 assert.ok(app.all(app.target).some(e=>e.attributes.role==='status'&&e.attributes['aria-live']==='polite'));
 assert.match(css,/@media\(max-width:720px\)/);assert.match(css,/var\(--border\)/);assert.match(css,/var\(--focus\)/);assert.match(css,/overflow-wrap:anywhere/);
});
test('app mounts new accounting for stopped/running status and completed tabs while retaining legacy receipts',()=>{
 const app=fs.readFileSync(path.join(staticDir,'app.js'),'utf8'),html=fs.readFileSync(path.join(staticDir,'index.html'),'utf8');
 assert.match(app,/IssueIntelInvestigationUsage\?\.render\(\$\('investigation-usage-status'\),row\.id/);assert.match(app,/IssueIntelInvestigationUsage\?\.render\(chain,currentJob/);assert.match(app,/IssueIntelUsage\.render\(\$\('usage-content'\),row\.accounting,row\)/);
 assert.ok(!app.includes("el.disabled=['queued','running'].includes(row.status)"));assert.match(html,/id="investigation-usage-status"/);assert.match(html,/src="\/investigation-usage\.js"/);assert.match(html,/href="\/investigation-usage\.css"/);
});

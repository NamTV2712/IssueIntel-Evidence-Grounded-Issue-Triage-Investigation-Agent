const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/readiness.js'),'utf8');
const localeContext={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/locales.js'),'utf8'),localeContext);
const capabilityIds=['offline','groq','jev','repository_history','github_app','saved_reports','batch','questions','check_plan','claim_review','stage_recovery','handoff','versions','releases','memory','followup','planned'];
function readiness(){return {schema:'product_readiness_v1',checked_at:'2026-10-23T08:00:00Z',
  runtime:{ready:true,python:'3.11.9',git_available:true,fts5_available:true,source_complete:true,problems:[]},
  providers:{groq:{ready:true,key_configured:true,model:'synthetic-model',reason:'ready',price_date:'2026-10-23',budget_checked:false,live_verified:false},
    typesafe:{ready:false,key_configured:true,enabled:false,model:'synthetic-jev',reason:'disabled',price_date:null,budget_checked:false,live_verified:false}},
  sync:{status:'available',scheduler_running:false,truncated:false,connections:[{repository:'<img src=x onerror=bad>/repo',phase:'ready',paused:false,pending_tasks:2,pending_updates:null,last_updated_at:1792742400,reason:null}]},
  cache:['repositories','repository_index','repository_memory','saved_reports'].map(id=>({id,status:'present'})),
  capabilities:capabilityIds.map(id=>({id,status:id==='offline'?'available':id==='github_app'?'unknown':'conditional',reason:id==='github_app'?'app_https_permissions_not_verified':id==='offline'?'runtime_ready':'next_call_budget_unchecked'})),
  model_calls:0,evaluation_performed:false,live_services_verified:false};}
function storage(){return {schema:'product_storage_preview_v1',scanned_at:'2026-10-23T08:01:00Z',measured_bytes:111,files:3,directories:2,complete:false,limit_reasons:['max_entries'],skipped_links:1,errors:0,
  categories:['git','source','environment','tool_cache','data','artifacts','other'].map((id,index)=>({id,measured_bytes:index===0?100:index===3?11:0,files:index===0?2:index===3?1:0,directories:index<2?1:0})),
  candidates:[{path:'src/synthetic/__pycache__',measured_bytes:11,files:1,reason:'python_bytecode',complete:true}],
  protected:[{path:'artifacts/<script>receipt</script>',reason:'artifacts_provenance'},{path:'data',reason:'data_private'}],
  volume:{total_bytes:20000000,free_bytes:10000000},limits:{max_entries:25000,max_depth:40,max_seconds:3},scan:{entries:25000,elapsed_ms:25},deletion_supported:false,model_calls:0};}
function boot(handler=(route)=>route==='/api/readiness'?readiness():storage(),language='vi'){
  const controls=new Map(),events=new Map(),calls=[];let token='synthetic-session';
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.attributes={};this.events=new Map();this.open=false;this.disabled=false;this.value='unchanged';}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;this.textContent='';}
    setAttribute(key,value){this.attributes[key]=value;}
    addEventListener(key,value){this.events.set(key,value);}
    set innerHTML(_){throw Error('Readiness must never parse source HTML');}
  }
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const document={getElementById:get,createElement:tag=>new Element(tag),querySelector:()=>({content:token}),addEventListener:(key,value)=>events.set(key,value)};
  const t=(key,values={})=>(localeContext.window.IssueIntelMessages[key]?.[language]||key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??'{'+name+'}'));
  vm.runInNewContext(source,{document,window:{IssueIntelUI:{t,locale:()=>language==='vi'?'vi-VN':'en-US'}},api:async(route,body)=>{calls.push([route,body]);return handler(route,body);}});
  const text=node=>node.textContent+' '+node.children.map(text).join(' ');
  const open=value=>{get('system-readiness').open=value;return get('system-readiness').events.get('toggle')();};
  return {get,calls,events,open,text,locale:value=>{language=value;events.get('issueintel:languagechange')();},session:value=>{token=value;},settle:()=>new Promise(resolve=>setImmediate(resolve))};
}
test('system inspection is lazy and storage requires its own deliberate POST',async()=>{
  const app=boot();assert.equal(app.calls.length,0);app.locale('en');assert.equal(app.calls.length,0);
  await app.open(true);assert.deepEqual(app.calls.map(call=>call[0]),['/api/readiness']);
  assert.match(app.text(app.get('readiness-content')),/Local runtime/);assert.equal(app.text(app.get('storage-content')).trim(),'');
  await app.get('storage-preview').onclick();assert.equal(app.calls[1][0],'/api/storage/preview');assert.equal(JSON.stringify(app.calls[1][1]),'{}');
  app.locale('vi');assert.equal(app.calls.length,2);assert.match(app.text(app.get('readiness-content')),/Runtime cục bộ/);
  assert.equal(app.get('issue-text').value,'unchanged');assert.equal(app.get('github-draft').value,'unchanged');
});
test('configured provider keys never imply budget or live GitHub acceptance',async()=>{
  const app=boot();await app.open(true);const value=app.text(app.get('readiness-content'));
  assert.match(value,/Đã cấu hình; không hiển thị khóa/);assert.match(value,/Có key không đồng nghĩa gọi được/);
  assert.match(value,/Ngân sách cho lời gọi tiếp theo chưa được kiểm/);assert.match(value,/Provider đang tắt/);
  assert.match(value,/GitHub App và webhook HTTPS chưa được xác minh/);assert.match(value,/Chưa nghiệm thu App, HTTPS/);
  assert.match(value,/Verifier nhận định/);assert.match(value,/Gói bàn giao/);assert.match(value,/Phục hồi stage/);
});
test('known GitHub App signing blockers are actionable in both languages without showing secrets',async()=>{
  const reasons={app_configuration_invalid:/configuration is invalid/,app_id_missing:/App ID is missing/,
    app_private_key_missing:/private-key file is missing/,app_private_key_unsafe:/reparse point/,
    app_private_key_unavailable:/metadata is unavailable/,app_webhook_secret_missing:/webhook secret is missing/,
    app_signing_dependencies_missing:/install the github-app extra/};
  for(const [reason,english] of Object.entries(reasons)){
    const report=readiness();report.capabilities.find(c=>c.id==='github_app').status='blocked';report.capabilities.find(c=>c.id==='github_app').reason=reason;
    const app=boot(()=>report);await app.open(true);assert.match(app.text(app.get('readiness-content')),/Bị chặn/);
    app.locale('en');const output=app.text(app.get('readiness-content'));assert.match(output,english);assert.match(output,/Blocked/);
    assert.doesNotMatch(output,/Xem mã trạng thái|synthetic-webhook|private-name\.pem/);
  }
});
test('repository text stays inert and malformed readiness clears prior success',async()=>{
  let valid=true;const app=boot(()=>valid?readiness():{...readiness(),providers:{}});await app.open(true);
  assert.match(app.text(app.get('readiness-content')),/<img src=x onerror=bad>/);valid=false;
  const pending=app.get('readiness-refresh').onclick();assert.equal(app.text(app.get('readiness-content')).trim(),'');await pending;
  assert.equal(app.text(app.get('readiness-content')).trim(),'');assert.match(app.get('readiness-message').textContent,/Chưa đọc được/);
  assert.equal(app.get('readiness-refresh').disabled,false);
});
test('partial storage is labelled as logical measured bytes with protected provenance',async()=>{
  const app=boot();await app.open(true);await app.get('storage-preview').onclick();const output=app.text(app.get('storage-content'));
  assert.match(output,/Đo một phần; chưa phải tổng dung lượng project/);assert.match(output,/111 B/);assert.match(output,/không phải dung lượng có thể giải phóng/);
  assert.match(output,/artifacts\/<script>receipt<\/script>/);assert.match(output,/Hồ sơ và receipt provenance cần giữ/);
  assert.match(output,/Không có nút xóa/);assert.ok(!app.calls.some(call=>/delete|cleanup|investigation|sync/.test(call[0])));
  app.locale('en');const english=app.text(app.get('storage-content'));assert.match(english,/Partial measurement/);assert.match(english,/not reclaimable disk space/);assert.match(english,/Protected sources/);
  assert.doesNotMatch(english,/Đo một phần|Nguồn được bảo vệ/);
});
test('failed storage refresh clears old totals and preserves separate readiness state',async()=>{
  let fail=false;const app=boot(route=>{if(route==='/api/readiness')return readiness();if(fail)throw Error('secret exception text');return storage();});
  await app.open(true);await app.get('storage-preview').onclick();fail=true;
  const pending=app.get('storage-preview').onclick();assert.equal(app.text(app.get('storage-content')).trim(),'');await pending;
  assert.equal(app.text(app.get('storage-content')).trim(),'');assert.match(app.get('storage-message').textContent,/Bản đo trước đã bỏ/);
  assert.doesNotMatch(app.get('storage-message').textContent,/secret/);assert.match(app.text(app.get('readiness-content')),/Runtime cục bộ/);
});
test('close and reopen reject a late readiness response from the previous opening',async()=>{
  let release,n=0;const app=boot(()=>++n===1?new Promise(resolve=>{release=resolve;}):{...readiness(),runtime:{...readiness().runtime,python:'3.12.0'}});
  const first=app.open(true);await app.settle();await app.open(false);assert.equal(app.text(app.get('readiness-content')).trim(),'');
  await app.open(true);release(readiness());await first;
  assert.match(app.text(app.get('readiness-content')),/3.12.0/);assert.doesNotMatch(app.text(app.get('readiness-content')),/3.11.9/);assert.equal(app.calls.length,2);
});
test('late storage cannot reappear after closing, reopening, or a changed session',async()=>{
  let release;const app=boot(route=>route==='/api/readiness'?readiness():new Promise(resolve=>{release=resolve;}));await app.open(true);
  const scanning=app.get('storage-preview').onclick();await app.settle();await app.open(false);await app.open(true);release(storage());await scanning;
  assert.equal(app.text(app.get('storage-content')).trim(),'');assert.equal(app.get('storage-preview').disabled,false);
  const next=app.get('storage-preview').onclick();await app.settle();app.session('new-session');release(storage());await next;
  assert.equal(app.text(app.get('storage-content')).trim(),'');
});
test('a newer refresh supersedes a late response and errors are not hidden by old success',async()=>{
  const releases=[];const app=boot(()=>new Promise(resolve=>releases.push(resolve)));
  const first=app.open(true);await app.settle();const second=app.get('readiness-refresh').onclick();await app.settle();
  releases[1]({...readiness(),model_calls:1});await second;releases[0](readiness());await first;
  assert.equal(app.text(app.get('readiness-content')).trim(),'');assert.match(app.get('readiness-message').textContent,/Chưa đọc được/);
});
test('malformed, misleading or unsafe storage responses never become a cleanup suggestion',async()=>{
  const mutations=[v=>v.measured_bytes++,v=>v.complete=true,v=>v.deletion_supported=true,v=>v.model_calls=1,
    v=>v.candidates[0].path='data/raw',v=>v.candidates[0].path='../__pycache__',v=>v.candidates[0].path='src/link/../__pycache__',
    v=>v.candidates[0].path='C:/private',v=>v.protected[0].path='/absolute',v=>v.volume.free_bytes=v.volume.total_bytes+1,
    v=>v.categories.push({...v.categories[0]}),v=>v.scan.elapsed_ms=-1];
  for(const mutate of mutations){const value=storage();mutate(value);const app=boot(route=>route==='/api/readiness'?readiness():value);await app.open(true);await app.get('storage-preview').onclick();
    assert.equal(app.text(app.get('storage-content')).trim(),'');assert.match(app.get('storage-message').textContent,/Chưa đo được/);}
});
test('invalid readiness cannot claim live verification, call admission or complete capabilities',async()=>{
  const mutations=[v=>v.live_services_verified=true,v=>v.evaluation_performed=true,v=>v.providers.groq.budget_checked=true,
    v=>v.providers.typesafe.live_verified=true,v=>v.providers.groq.key_configured=false,v=>v.providers.groq.price_date=null,
    v=>v.capabilities.pop(),v=>v.runtime.git_available=false,v=>v.sync.connections[0].last_updated_at=1e300];
  for(const mutate of mutations){const value=readiness();mutate(value);const app=boot(()=>value);await app.open(true);assert.equal(app.text(app.get('readiness-content')).trim(),'');assert.match(app.get('readiness-message').textContent,/Chưa đọc được/);}
});
test('an unsafe root is visible as protected and large inventories have bounded DOM rendering',async()=>{
  const value=storage();value.protected=[{path:'.',reason:'unsafe_root'},...Array.from({length:1200},(_,index)=>({path:'artifacts/receipt-'+index,reason:'artifacts_provenance'}))];
  value.candidates=Array.from({length:1200},(_,index)=>({path:'src/module_'+index+'/__pycache__',reason:'python_bytecode',measured_bytes:0,files:0,complete:true}));
  const app=boot(route=>route==='/api/readiness'?readiness():value,'en');await app.open(true);await app.get('storage-preview').onclick();const text=app.text(app.get('storage-content'));
  assert.match(text,/The scan root is unsafe/);assert.match(text,/Showing 200\/1,201 paths/);assert.match(text,/Showing 200\/1,200 eligible caches/);
  assert.doesNotMatch(text,/receipt-1199|module_1199/);assert.equal(app.get('storage-message').textContent,'');
});
test('readiness errors recover on explicit refresh without repeating a storage scan',async()=>{
  let fail=true;const app=boot(route=>{if(route==='/api/readiness'&&fail)throw Error('private detail');return route==='/api/readiness'?readiness():storage();});
  await app.open(true);assert.match(app.get('readiness-message').textContent,/Chưa đọc được/);await app.get('storage-preview').onclick();const before=app.text(app.get('storage-content'));
  fail=false;await app.get('readiness-refresh').onclick();assert.match(app.text(app.get('readiness-content')),/Runtime cục bộ/);assert.equal(app.get('readiness-message').textContent,'');
  assert.equal(app.text(app.get('storage-content')),before);assert.equal(app.calls.filter(call=>call[0]==='/api/storage/preview').length,1);
});

test('large sizes show readable units and exact logical bytes, and zero-depth previews remain visible',async()=>{
  const value=storage();value.measured_bytes=2*1024**3;value.categories[0].measured_bytes=value.measured_bytes-11;
  value.limits.max_depth=0;value.limit_reasons=['max_depth'];
  const app=boot(route=>route==='/api/readiness'?readiness():value,'en');await app.open(true);await app.get('storage-preview').onclick();
  const result=app.text(app.get('storage-content'));assert.match(result,/2 GiB/);assert.match(result,/2,147,483,648 B/);assert.match(result,/Partial measurement/);
});

test('dynamic readiness labels translate with the selected interface language',async()=>{
  const app=boot(undefined,'en');await app.open(true);await app.get('storage-preview').onclick();
  const content=app.text(app.get('readiness-content'))+' '+app.text(app.get('storage-content'));
  assert.match(content,/Saved reports/);assert.match(content,/Compare revisions/);assert.match(content,/Present/);assert.match(content,/Other/);
  assert.doesNotMatch(content,/Báo cáo đã lưu|Đã có|Đối chiếu phiên bản|Khác/);
});

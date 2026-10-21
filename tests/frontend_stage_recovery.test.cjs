const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const dir=path.join(__dirname,'../src/issueintel/interfaces/static'),code=fs.readFileSync(path.join(dir,'stage-recovery.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(dir,'locales.js'),'utf8'),locale);const messages=locale.window.IssueIntelMessages;
const hash=c=>c.repeat(64),id=c=>c.repeat(24),clone=v=>JSON.parse(JSON.stringify(v));
function row(c='a'){return {id:id(c),mode:'example',provider:'groq',depth:'quick',status:'failed',stage_checkpoints:{execution_input:{sha256:hash('b')}}};}
test('raw Jev checkpoint download labels are bilingual rather than exposing internal stage names',()=>{
  for(let n=1;n<=4;n++)assert.deepEqual(clone(messages['planned_decision_'+n]),{vi:'Quyết định Jev '+n,en:'Jev decision '+n});
});
function preview(r){return {schema:'product_stage_recovery_preview_v1',available:true,source_job_id:r.id,binding_sha256:hash('c'),context_sha256:hash('d'),
  commit_sha:'e'.repeat(40),provider:r.provider,depth:r.depth,completed_stages:['execution_input','initial_retrieval','prepared_input'],next_stage:'seed_generation',
  prior_provider_calls:r.provider==='none'?0:1,max_provider_calls:r.provider==='none'?0:4,requires_api:r.provider==='groq',automatic_retry:false,model_calls:0};}
function boot(handler){
  const nodes=new Map(),events=[],listeners=new Map(),calls=[];
  class El{constructor(tag){this.tag=tag;this.textContent='';this.children=[];this.hidden=false;this.disabled=false;this.checked=false;}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}setAttribute(){}set innerHTML(_){throw Error('Unsafe markup');}}
  for(const key of ['stage-recovery-panel','stage-recovery-content','run-button'])nodes.set(key,new El('div'));
  const document={documentElement:{lang:'vi'},getElementById:key=>nodes.get(key),createElement:tag=>new El(tag),addEventListener:(name,fn)=>listeners.set(name,fn),dispatchEvent:event=>events.push(event)};
  const window={IssueIntelUI:{t:(key,slots={})=>{assert.ok(messages[key]?.[document.documentElement.lang],key);return messages[key][document.documentElement.lang].replace(/\{(\w+)\}/g,(_,name)=>slots[name]);}}};
  let selected=row();
  async function normal(route,body){return body?{id:id('f'),mode:'stage_resume',provider:selected.provider,depth:selected.depth,status:'queued',stage_recovery_source:{source_job_id:selected.id,binding_sha256:body.binding_sha256}}:preview(selected);}
  vm.runInNewContext(code,{document,window,CustomEvent:class{constructor(type,o){this.type=type;this.detail=o.detail;}},api:async(route,body)=>{calls.push([route,clone(body||null)]);return handler?handler(route,body,normal):normal(route,body);}});
  const area=nodes.get('stage-recovery-content'),all=n=>[n,...n.children.flatMap(all)],text=n=>n.textContent+' '+n.children.map(text).join(' ');
  const button=label=>{const b=all(area).find(n=>n.tag==='button'&&n.textContent===label);assert.ok(b,label);return b;};
  return {calls,events,area,all,text,button,click:label=>button(label).onclick(),nodes,show:r=>{selected=r;window.IssueIntelStageRecovery.show(r);},
    consent:()=>all(area).find(n=>n.id==='stage-recovery-consent'),language:lang=>{document.documentElement.lang=lang;listeners.get('issueintel:languagechange')();}};
}

test('opening a stopped source is read-only and paid recovery requires deliberate preview and consent',async()=>{
  const a=boot();a.show(row());assert.equal(a.calls.length,0);await a.click('Kiểm tra các stage để tiếp tục');
  assert.match(a.text(a.area),/1\/4/);assert.equal(a.button('Tiếp tục phần còn thiếu').disabled,true);await a.click('Tiếp tục phần còn thiếu');assert.equal(a.calls.length,1);
  a.consent().checked=true;a.consent().onchange();await a.click('Tiếp tục phần còn thiếu');
  assert.deepEqual(a.calls[1][1],{binding_sha256:hash('c'),confirmation:'resume_product_stages_with_api_cost'});
  assert.equal(a.events[0].type,'issueintel:stagerecoverycreated');assert.deepEqual(clone(a.events[0].detail),{job_id:id('f'),source_job_id:id('a')});
});

test('Jev preview separates original attempt bounds, retained receipt gaps and provider consent',async()=>{
  const r={...row(),controller:'jev',depth:'planned'},a=boot(async(route,body,normal)=>{
    const p=await normal(route,body);if(!body)Object.assign(p,{controller:'jev',max_provider_calls:9,
      prior_controller_calls:2,max_controller_calls:4,orphan_terminal_receipts:1,next_provider:'typesafe',
      api_providers:['groq','typesafe'],next_stage:'planned_decision_2',
      completed_stages:['execution_input','initial_retrieval','planned_input','seed_generation','planned_decision_1']});return p;
  });
  a.show(r);assert.equal(a.calls.length,0);await a.click('Kiểm tra các stage để tiếp tục');
  assert.match(a.text(a.area),/Jev \/ Typesafe: 2\/4/);assert.match(a.text(a.area),/thiếu quyết định đã lưu/);
  assert.match(a.text(a.area),/groq, typesafe/);assert.equal(a.button('Tiếp tục phần còn thiếu').disabled,true);
  a.language('en');assert.match(a.text(a.area),/2\/4/);assert.match(a.text(a.area),/Typesafe/);
  a.consent().checked=true;a.consent().onchange();await a.click('Continue missing work');
  assert.equal(a.calls[1][1].confirmation,'resume_product_stages_with_api_cost');
});

test('controller receipt ambiguity and inherited Jev exhaustion cannot expose paid consent',async()=>{
  for(const reason of ['controller_receipt_unsettled','original_controller_budget_exhausted']){
    const a=boot(async()=>({available:false,source_job_id:id('a'),reason,model_calls:0,automatic_retry:false}));
    a.show({...row(),controller:'jev',depth:'planned'});await a.click('Kiểm tra các stage để tiếp tục');
    assert.ok(!a.consent());assert.match(a.text(a.area),reason==='controller_receipt_unsettled'?/reserved/:/bốn lượt thử/);
    a.language('en');assert.ok(!a.consent());assert.equal(a.calls.length,1);
  }
});

test('Jev preview must bind its controller, provider scope and original four-attempt allowance',async()=>{
  for(const mutation of [p=>p.max_controller_calls=8,p=>p.prior_controller_calls=5,
    p=>p.api_providers=['invented'],p=>p.next_provider='invented',p=>delete p.controller]){
    const r={...row(),controller:'jev',depth:'planned'},a=boot(async(route,body,normal)=>{
      const p=await normal(route,body);Object.assign(p,{controller:'jev',max_provider_calls:9,
        prior_controller_calls:1,max_controller_calls:4,orphan_terminal_receipts:0,next_provider:'typesafe',api_providers:['typesafe']});mutation(p);return p;
    });
    a.show(r);await a.click('Kiểm tra các stage để tiếp tục');assert.ok(!a.consent());assert.match(a.text(a.area),/Chưa có journal hợp lệ/);
  }
});

test('offline completion is distinct and cannot request paid execution',async()=>{
  const a=boot();a.show({...row(),provider:'none'});await a.click('Kiểm tra các stage để tiếp tục');assert.match(a.text(a.area),/không cần API/);
  a.consent().checked=true;a.consent().onchange();await a.click('Tiếp tục phần còn thiếu');assert.equal(a.calls[1][1].confirmation,'resume_product_stages_without_api');
});

test('lost acknowledgement locks the exact request and cannot create an implicit retry',async()=>{
  let lost=true;const a=boot((route,body,normal)=>{if(body&&lost)throw Error('Lost reply');return normal(route,body);});a.show(row());await a.click('Kiểm tra các stage để tiếp tục');
  a.consent().checked=true;a.consent().onchange();await a.click('Tiếp tục phần còn thiếu');assert.equal(a.events.length,0);assert.match(a.text(a.area),/đúng yêu cầu đã gửi/);
  lost=false;await a.click('Kiểm trạng thái yêu cầu phục hồi');assert.deepEqual(a.calls[1][1],a.calls[2][1]);assert.equal(a.events.length,1);
});

test('detached controls and late responses cannot relabel a different source',async()=>{
  let finish;const a=boot((route,body,normal)=>{const value=normal(route,body);return new Promise(resolve=>finish=()=>value.then(resolve));});a.show(row());
  const stale=a.button('Kiểm tra các stage để tiếp tục'),pending=stale.onclick();a.show(row('b'));finish();await pending;await stale.onclick();
  assert.equal(a.calls.length,1);assert.doesNotMatch(a.text(a.area),/Source SHA256/);assert.match(a.text(a.area),new RegExp(id('b')));
});

test('source/provider/call-budget changes or truth promotion in preview are rejected',async()=>{
  for(const mutate of [p=>p.source_job_id=id('b'),p=>p.provider='none',p=>p.max_provider_calls=99,p=>p.automatic_retry=true,p=>p.model_calls=1,p=>p.completed_stages=['invented']]){
    const a=boot(async(route,body,normal)=>{const value=await normal(route,body);mutate(value);return value;});a.show(row());await a.click('Kiểm tra các stage để tiếp tục');
    assert.match(a.text(a.area),/Chưa có journal hợp lệ/);assert.ok(!a.consent());
  }
});

test('late paid acknowledgement never navigates away from a newer selected source',async()=>{
  let finish;const a=boot((route,body,normal)=>{const value=normal(route,body);return body?new Promise(resolve=>finish=()=>value.then(resolve)):value;});
  a.show(row());await a.click('Kiểm tra các stage để tiếp tục');a.consent().checked=true;a.consent().onchange();const pending=a.click('Tiếp tục phần còn thiếu');a.show(row('b'));finish();await pending;
  assert.equal(a.events.length,0);assert.match(a.text(a.area),new RegExp(id('b')));
});

test('language changes preserve preview/consent and make no extra requests',async()=>{
  const a=boot();a.show(row());await a.click('Kiểm tra các stage để tiếp tục');a.consent().checked=true;a.consent().onchange();a.language('en');
  assert.ok(a.consent().checked);assert.equal(a.calls.length,1);await a.click('Continue missing work');assert.equal(a.calls.length,2);
});

test('another tracked job blocks POST and an invalid acknowledgement cannot open a run',async()=>{
  const a=boot();a.show(row());await a.click('Kiểm tra các stage để tiếp tục');a.consent().checked=true;a.consent().onchange();a.nodes.get('run-button').disabled=true;
  await a.click('Tiếp tục phần còn thiếu');assert.equal(a.calls.length,1);
  const b=boot(async(route,body,normal)=>{const v=await normal(route,body);if(body)v.stage_recovery_source.source_job_id=id('b');return v;});b.show(row());await b.click('Kiểm tra các stage để tiếp tục');b.consent().checked=true;b.consent().onchange();await b.click('Tiếp tục phần còn thiếu');assert.equal(b.events.length,0);
});

test('legacy verifier/export, benchmark and running sources do not expose the new action',()=>{
  const a=boot();for(const r of [{...row(),mode:'main_dev'},{...row(),status:'running'},
    {...row(),stage_checkpoints:{execution_input:{sha256:hash('b')},verification_input:{sha256:hash('e')}}}]){
    a.show(r);assert.equal(a.nodes.get('stage-recovery-panel').hidden,true);
  }assert.equal(a.calls.length,0);
});

test('failed recovered source can resume a critic with its original global attempt bound',async()=>{
  const r={...row(),mode:'stage_resume',pipeline_context_sha256:hash('d'),
    stage_checkpoints:{seed_generation:{sha256:hash('b')},verification_input:{sha256:hash('e')}}};
  const a=boot(async(route,body,normal)=>{
    const value=await normal(route,body);if(!body)Object.assign(value,{root_job_id:id('b'),source_job_ids:[id('b'),r.id],
      prior_provider_calls:3,next_stage:'verification',completed_stages:[...value.completed_stages,'seed_generation','verification_input']});
    return value;
  });
  a.show(r);assert.equal(a.nodes.get('stage-recovery-panel').hidden,false);assert.equal(a.calls.length,0);
  await a.click('Kiểm tra các stage để tiếp tục');assert.match(a.text(a.area),/3\/4/);
  assert.match(a.text(a.area),new RegExp('Lượt gốc: '+id('b')));assert.match(a.text(a.area),/Đã ghép nguồn từ 2 phiên/);
  assert.equal(a.button('Tiếp tục phần còn thiếu').disabled,true);
  a.language('en');assert.match(a.text(a.area),/Combined sources from 2 runs/);
  a.consent().checked=true;a.consent().onchange();await a.click('Continue missing work');
  assert.equal(a.events[0].detail.source_job_id,r.id);assert.equal(a.calls[1][1].confirmation,'resume_product_stages_with_api_cost');
});

test('unknown recovered context does not expose any continuation action',()=>{
  const a=boot();a.show({...row(),mode:'stage_resume',stage_checkpoints:{}});
  assert.equal(a.nodes.get('stage-recovery-panel').hidden,true);assert.equal(a.calls.length,0);
});

test('product fork journals expose only explicit preview and bind consent to each original source',async()=>{
  for(const mode of ['followup','clarification','check_followup','memory_followup','context']){
    const r={...row(),mode},a=boot();a.show(r);
    assert.equal(a.nodes.get('stage-recovery-panel').hidden,false);assert.equal(a.calls.length,0);
    await a.click('Kiểm tra các stage để tiếp tục');assert.match(a.text(a.area),/1\/4/);
    assert.equal(a.button('Tiếp tục phần còn thiếu').disabled,true);
    a.consent().checked=true;a.consent().onchange();await a.click('Tiếp tục phần còn thiếu');
    assert.equal(a.calls[1][0],'/api/jobs/'+r.id+'/stage-recovery');
    assert.equal(a.calls[1][1].confirmation,'resume_product_stages_with_api_cost');
  }
});

test('Planned preview respects a narrower 5/7/9 route bound and rejects attempts past it',async()=>{
  for(const cap of [5,7,9]){const r={...row(),depth:'planned'},a=boot(async(route,body,normal)=>{
      const p=await normal(route,body);if(!body)Object.assign(p,{max_provider_calls:cap,prior_provider_calls:cap-1,next_stage:'verification'});return p;});
    a.show(r);await a.click('Kiểm tra các stage để tiếp tục');assert.match(a.text(a.area),new RegExp((cap-1)+'/'+cap));
    assert.ok(a.consent());
  }
  const a=boot(async(route,body,normal)=>{const p=await normal(route,body);Object.assign(p,{max_provider_calls:7,prior_provider_calls:8});return p;});
  a.show({...row(),depth:'planned'});await a.click('Kiểm tra các stage để tiếp tục');assert.equal(a.consent(),undefined);
});

test('exhausted inherited route budget is explicit and never offers another paid call',async()=>{
  const a=boot(()=>({available:false,source_job_id:id('a'),reason:'original_call_budget_exhausted',
    prior_provider_calls:7,max_provider_calls:7,automatic_retry:false,model_calls:0}));
  a.show({...row(),depth:'planned',mode:'stage_resume',pipeline_context_sha256:hash('d'),stage_checkpoints:{}});
  await a.click('Kiểm tra các stage để tiếp tục');assert.match(a.text(a.area),/Đã dùng 7\/7 lượt thử/);
  assert.equal(a.consent(),undefined);a.language('en');assert.match(a.text(a.area),/Used 7\/7 attempts/);assert.equal(a.calls.length,1);
});

test('failed Planned answers display inherited root use and require consent for only the remaining budget',async()=>{
  const r={...row(),mode:'planned_continuation',depth:'planned'};
  const a=boot(async(route,body,normal)=>{const p=await normal(route,body);if(!body)Object.assign(p,{
    budget_scope:'planned_continuation_remaining',root_max_provider_calls:9,inherited_root_provider_calls:3,max_provider_calls:6});return p;});
  a.show(r);assert.equal(a.nodes.get('stage-recovery-panel').hidden,false);assert.equal(a.calls.length,0);
  await a.click('Kiểm tra các stage để tiếp tục');assert.match(a.text(a.area),/Phiên cha đã dùng 3\/9 lượt/);
  assert.match(a.text(a.area),/1\/6/);assert.equal(a.button('Tiếp tục phần còn thiếu').disabled,true);
  a.language('en');assert.match(a.text(a.area),/parent has used 3\/9 attempts/);
  a.consent().checked=true;a.consent().onchange();await a.click('Continue missing work');
  assert.equal(a.calls[1][1].confirmation,'resume_product_stages_with_api_cost');
});

test('unbound or changed inherited budget never enables a recovery request',async()=>{
  for(const mutate of [p=>delete p.budget_scope,p=>p.root_max_provider_calls=4,p=>p.inherited_root_provider_calls=0,
    p=>p.inherited_root_provider_calls=3.5,p=>p.max_provider_calls=9,p=>p.budget_scope='unknown']){
    const a=boot(async(route,body,normal)=>{const p=await normal(route,body);Object.assign(p,{
      budget_scope:'planned_continuation_remaining',root_max_provider_calls:9,inherited_root_provider_calls:3,max_provider_calls:6});mutate(p);return p;});
    a.show({...row(),mode:'planned_continuation',depth:'planned'});await a.click('Kiểm tra các stage để tiếp tục');
    assert.equal(a.consent(),undefined);assert.equal(a.calls.length,1);
  }
});

test('exhausted Planned answer recovery shows both budget scopes without another POST',async()=>{
  const a=boot(()=>({available:false,source_job_id:id('a'),reason:'original_call_budget_exhausted',
    budget_scope:'planned_continuation_remaining',root_max_provider_calls:9,inherited_root_provider_calls:3,
    prior_provider_calls:6,max_provider_calls:6,automatic_retry:false,model_calls:0}));
  a.show({...row(),mode:'stage_resume',depth:'planned',pipeline_context_sha256:hash('d'),stage_checkpoints:{}});
  await a.click('Kiểm tra các stage để tiếp tục');assert.match(a.text(a.area),/Đã dùng 6\/6 lượt thử/);
  assert.match(a.text(a.area),/Phiên cha đã dùng 3\/9 lượt/);assert.equal(a.consent(),undefined);
  assert.equal(a.calls.length,1);
});

test('altered recovery ancestry cannot enable consent or another paid request',async()=>{
  const mutations=[p=>p.root_job_id=id('c'),p=>p.source_job_ids.reverse(),p=>p.source_job_ids=[id('a'),id('a')],
    p=>p.source_job_ids=[],p=>p.source_job_ids=[id('b'),'bad',id('a')],p=>p.source_job_ids=Array.from({length:9},(_,i)=>String(i).repeat(24))];
  for(const mutate of mutations){const a=boot(async(route,body,normal)=>{const p=await normal(route,body);
      Object.assign(p,{root_job_id:id('b'),source_job_ids:[id('b'),id('a')]});mutate(p);return p;});
    a.show({...row(),mode:'stage_resume',pipeline_context_sha256:hash('d'),stage_checkpoints:{}});
    await a.click('Kiểm tra các stage để tiếp tục');assert.match(a.text(a.area),/Chưa có journal hợp lệ/);
    assert.equal(a.consent(),undefined);assert.equal(a.calls.length,1);
  }
});

test('component is bilingual, inert and wired to the Workbench',()=>{
  for(const m of code.matchAll(/\bt\(['"]([^'"]+)['"]/g)){assert.ok(messages[m[1]]?.vi,m[1]);assert.ok(messages[m[1]]?.en,m[1]);}
  const html=fs.readFileSync(path.join(dir,'index.html'),'utf8');assert.match(html,/id="stage-recovery-panel"/);assert.match(html,/src="\/stage-recovery.js"/);
  const app=fs.readFileSync(path.join(dir,'app.js'),'utf8');assert.match(app,/IssueIntelStageRecovery\?\.show\(row\)/);assert.match(app,/issueintel:stagerecoverycreated/);
});

test('a running recovery cannot replace another report or its accounting after navigation',async()=>{
  const app=fs.readFileSync(path.join(dir,'app.js'),'utf8'),start=app.indexOf('async function runJob('),end=app.indexOf('async function openJob(',start);
  for(const navigateDuringPost of [false,true]){
    const controls=new Map(),opened=[],usage=[],logs=[];let polls=0;
    const get=key=>{if(!controls.has(key))controls.set(key,{disabled:false,hidden:false});return controls.get(key);};
    const context={window:{},$:get,viewGeneration:0,stageView:null,activeJob:null,
      t:(key,slots={})=>messages[key].en.replace(/\{(\w+)\}/g,(_,k)=>slots[k]),message:v=>logs.push(v),
      api:async route=>{
        if(route==='/api/investigations'){context.viewGeneration++;return {id:id('f')};}
        polls++;if(!navigateDuringPost&&polls===2)context.viewGeneration++;
        return polls===1&&!navigateDuringPost?{status:'running',stage:'replay_stages'}:{status:'completed'};
      },showUsage:v=>usage.push(v),showStageArtifacts(){},openJob:async value=>opened.push(value),history:async()=>{},setTimeout:fn=>fn()};
    vm.createContext(context);vm.runInContext(app.slice(start,end),context);
    await context.runJob({mode:'stage_resume'},navigateDuringPost?null:{id:id('f')});
    assert.equal(opened.length,0);assert.equal(usage.length,navigateDuringPost?0:1);
    assert.match(logs.at(-1),new RegExp(id('f')));assert.match(logs.at(-1),/history/);
  }
});

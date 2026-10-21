const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const base=path.join(__dirname,'../src/issueintel/interfaces/static');
const appCode=fs.readFileSync(path.join(base,'app.js'),'utf8');
const html=fs.readFileSync(path.join(base,'index.html'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(base,'locales.js'),'utf8'),locale);
const messages=locale.window.IssueIntelMessages;
const ready={ready:true,enabled:true,key_configured:true,model:'jev-1.13.0',max_calls_per_run:4,per_call_usd_bound:'0.002688',daily_requests:10,daily_usd:'1',monthly_usd:'5'};

function controls({status=ready,fail=false}={}){
  const calls=[],elements=new Map();let language='vi';
  for(const id of ['mode','depth','use-groq','use-typesafe','typesafe-controls','typesafe-status','case-id','github-url','github-revision','repository','revision','issue-text'])elements.set(id,{value:'',checked:false,disabled:false,hidden:false});
  elements.get('mode').value='local';elements.get('depth').value='planned';
  elements.get('repository').value='synthetic/repo';elements.get('revision').value='a'.repeat(40);elements.get('issue-text').value='Parser returns the wrong value.';
  const context=vm.createContext({$:id=>elements.get(id),t:(key,values={})=>{assert.ok(messages[key]?.[language],key);return messages[key][language].replace(/\{(\w+)\}/g,(_,name)=>values[name]);},
    api:async path=>{calls.push(path);if(fail)throw Error('synthetic server failure');return status;},fetch:()=>{throw Error('Selectors must not make external calls');}});
  vm.runInContext('let typesafeStatus=null,typesafeStatusLoaded=false,typesafeStatusRequested=false;'+
    appCode.slice(appCode.indexOf('function updateTypesafeControls()'),appCode.indexOf('function updateDepthControls()'))+
    appCode.slice(appCode.indexOf('function requestInput()'),appCode.indexOf('async function submit(')),context);
  return {calls,elements,context,language:value=>language=value,update:()=>context.updateTypesafeControls(),load:()=>context.loadTypesafeStatus(),
    request:()=>JSON.parse(JSON.stringify(context.requestInput())),state:value=>{context.nextStatus=value;vm.runInContext('typesafeStatus=nextStatus;typesafeStatusLoaded=true;',context);}};
}

test('Typesafe starts unchecked and cannot enable Groq or bypass missing configuration',async()=>{
  assert.match(html,/<div id="typesafe-controls"[^>]* hidden>/);
  assert.match(html,/<input id="use-typesafe" type="checkbox" disabled aria-describedby="typesafe-status typesafe-disclosure">/);
  assert.doesNotMatch(html,/<input id="use-typesafe"[^>]*\bchecked\b/);
  for(const status of [null,{ready:false,enabled:false,key_configured:true},{...ready,enabled:false},{...ready,key_configured:false},{...ready,ready:false},{ready:true}]){
    const app=controls();app.state(status);app.elements.get('use-typesafe').checked=true;app.update();
    assert.equal(app.elements.get('use-typesafe').checked,false);assert.equal(app.elements.get('use-typesafe').disabled,true);
    assert.equal(app.elements.get('use-groq').checked,false);assert.equal(app.request().controller,undefined);
    app.elements.get('use-groq').checked=true;app.update();assert.equal(app.elements.get('use-typesafe').disabled,true);
    app.elements.get('use-typesafe').checked=true;assert.equal(app.request().controller,undefined);
  }
});

test('Jev is an explicit eligible Planned option and resets when Groq, depth or source changes',()=>{
  const app=controls();app.state(ready);app.update();assert.equal(app.elements.get('use-typesafe').disabled,true);
  assert.match(app.elements.get('typesafe-status').textContent,/Bật Groq trước/);
  app.elements.get('use-groq').checked=true;app.update();assert.equal(app.elements.get('use-typesafe').disabled,false);
  assert.equal(app.request().controller,undefined);app.elements.get('use-typesafe').checked=true;assert.equal(app.request().controller,'jev');
  for(const mode of ['local','github','example']){
    app.elements.get('mode').value=mode;app.elements.get('depth').value='planned';app.elements.get('use-typesafe').checked=true;app.update();
    assert.equal(app.elements.get('typesafe-controls').hidden,false);assert.equal(app.elements.get('use-typesafe').disabled,false);
    assert.equal(app.request().controller,'jev');
  }
  for(const [mode,depth,groq] of [['main_dev','planned',true],['followup','planned',true],['context','planned',true],['local','quick',true],['local','deep',true],['local','planned',false]]){
    app.elements.get('mode').value=mode;app.elements.get('depth').value=depth;app.elements.get('use-groq').checked=groq;app.elements.get('use-typesafe').checked=true;app.update();
    assert.equal(app.elements.get('use-typesafe').disabled,true);assert.equal(app.elements.get('use-typesafe').checked,false);
    assert.equal(app.request().controller,undefined);assert.equal(app.elements.get('use-groq').checked,groq);
  }
  assert.deepEqual(app.calls,[]);
});

test('status is read once locally; rendering, language and selecting Jev make no provider calls',async()=>{
  const app=controls();await app.load();await app.load();assert.deepEqual(app.calls,['/api/typesafe/status']);
  app.elements.get('use-groq').checked=true;app.update();app.elements.get('use-typesafe').checked=true;
  for(const language of ['en','vi','en']){app.language(language);app.update();app.request();}
  assert.equal(app.elements.get('use-typesafe').checked,true);assert.deepEqual(app.calls,['/api/typesafe/status']);
  assert.match(app.elements.get('typesafe-status').textContent,/only after you select Jev and start/);
  assert.equal((appCode.match(/^loadTypesafeStatus\(\);/gm)||[]).length,1);
  const change=appCode.slice(appCode.indexOf("document.addEventListener('issueintel:languagechange'"));
  assert.doesNotMatch(change,/loadTypesafeStatus|\/api\/typesafe/);
  const failed=controls({fail:true});await failed.load();await failed.load();assert.deepEqual(failed.calls,['/api/typesafe/status']);
  assert.equal(failed.elements.get('use-typesafe').disabled,true);assert.match(failed.elements.get('typesafe-status').textContent,/Tải lại trang/);
});

test('configuration states and consent disclose separate paid decision calls in both languages',()=>{
  const app=controls();app.update();assert.match(app.elements.get('typesafe-status').textContent,/Đang kiểm cấu hình/);
  for(const language of ['vi','en']){
    app.language(language);
    for(const value of [null,{ready:false,enabled:false,key_configured:false},{ready:false,enabled:false,key_configured:true},{...ready,ready:false},ready]){
      app.state(value);app.update();assert.ok(app.elements.get('typesafe-status').textContent.length>25);
    }
  }
  const key=[...html.matchAll(/id="typesafe-disclosure"[^>]*data-i18n="([^"]+)"/g)][0][1];
  assert.match(messages[key].en,/issue, hypotheses and code excerpts/);assert.match(messages[key].en,/Up to 4 separately billed decision calls/);
  assert.match(messages[key].en,/additional to Groq calls/);assert.match(messages[key].en,/fall back to rules/);
  assert.match(html,/aria-describedby="typesafe-status typesafe-disclosure"/);
});

const hash=letter=>letter.repeat(64),jobId='a'.repeat(24);
function dom(file){
  let language='vi';const listeners=new Map();
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.attributes={};}
    append(...values){this.children.push(...values);}prepend(...values){this.children.unshift(...values);}replaceChildren(...values){this.children=values;}
    setAttribute(key,value){this.attributes[key]=value;}set innerHTML(_){throw Error('Source HTML must stay inert');}
  }
  const context={document:{createElement:tag=>new Element(tag),addEventListener:(event,fn)=>listeners.set(event,fn),dispatchEvent:event=>listeners.get(event.type)?.(event)},
    window:{IssueIntelUI:{t:(key,values={})=>{assert.ok(messages[key]?.[language],key);return messages[key][language].replace(/\{(\w+)\}/g,(_,name)=>values[name]);}}},
    fetch(){throw Error('Saved rendering must not fetch');},api(){throw Error('Saved rendering must not call APIs');}};
  vm.runInNewContext(fs.readFileSync(path.join(base,file),'utf8'),context);
  const root=new Element(),all=node=>[node,...node.children.flatMap(all)],text=node=>node.textContent+' '+node.children.map(text).join(' ');
  return {context,root,all,text,language:value=>language=value};
}
function receipt(status='settled',id='d'.repeat(32)){
  return {schema:'typesafe_decision_receipt_v1',provider:'typesafe',reservation_id:id,model:'jev-1.13.0',
    binding:{job_id:jobId,decision_index:1,query_sha256:hash('a'),snapshot_sha256:hash('b'),commit_sha:'c'.repeat(40)},
    request_sha256:hash('e'),status,input_tokens:status==='settled'?100:null,output_tokens:status==='settled'?20:null,
    micro_usd_charged_or_reserved:status==='settled'?5:2688,price_date:'2026-10-17',currency:'USD',is_provider_invoice:false,sha256:hash('f')};
}
function saved(){
  const source={query_sha256:hash('a'),snapshot_sha256:hash('b'),commit_sha:'c'.repeat(40)},paid=receipt();
  const hypotheses=[{id:'H1',statement:'Parser may fail.',support_ids:['E1'],counter_ids:[],falsification_test:'Inspect the parser.'}];
  const plan={schema:'planned_investigation_v1',policy:'bounded_planner_v1',source,sha256:hash('9'),
    routing:{kind:'observable_heuristic',reason_codes:['single_file_or_no_missing_counter'],max_retrieval_rounds:1,signals:{code_files:1,not_found_hypotheses:1,parse_error_count:0,skipped_file_count:0}},
    limits:{max_retrieval_rounds:1,max_queries_per_round:3,max_added_per_round:6,max_added_total:18,max_generation_passes:2,max_verifier_passes:1,max_provider_calls:5},
    seed_evidence_ids:['I1','E1'],seed_annotation:{hypotheses},rounds:[],generation_passes:1,verifier_passes:1,provider_calls:2,
    stop_reason:'controller_skipped_retrieval',final_decision:{action:'verify',controller:'jev',fallback:false,reason:'controller_selected'}};
  const decision={decision_index:1,allowed:['retrieve','verify','stop'],state_sha256:hash('0'),binding:{...paid.binding},request_sha256:paid.request_sha256,
    action:'verify',fallback:false,reason:'selected',attempt_status:'settled',receipt:paid};
  const sidecar={schema:'typesafe_controller_v1',provider:'typesafe',model:'jev-1.13.0',source:{...source},workflow_plan_sha256:plan.sha256,
    job_id:jobId,max_decisions:4,records:[decision],sha256:hash('1')};
  const pack={snapshot:{commit_sha:source.commit_sha,snapshot_sha256:source.snapshot_sha256},workflow_plan:plan,controller_decisions:sidecar,
    evidence:[{id:'I1',kind:'issue',sha256:source.query_sha256},{id:'E1',kind:'code'}]};
  return {plan,pack,sidecar,decision};
}

test('saved Trace keeps Typesafe actions, fallback and receipts separate from generation calls',()=>{
  const app=dom('workflow.js'),value=saved();app.context.window.IssueIntelWorkflow.render(app.root,value.plan,jobId,value.pack);
  let output=app.text(app.root);assert.match(output,/Call sinh và verifier đã ghi nhận\s+2 \/ 5/);
  assert.match(output,/1 \/ 4 lượt quyết định đã ghi/);assert.match(output,/Quyết định 1: Chuyển sang verifier/);
  assert.match(output,/Fallback theo luật: Không/);assert.match(output,/0\.000005; chưa phải hóa đơn/);assert.doesNotMatch(output,/\d+%/);
  value.plan.final_decision.fallback=true;value.decision.fallback=true;value.decision.reason='request_failed';
  value.decision.attempt_status='failed_bound';value.decision.receipt.status='failed_bound';value.decision.receipt.micro_usd_charged_or_reserved=2688;
  app.root.replaceChildren();app.language('en');app.context.window.IssueIntelWorkflow.render(app.root,value.plan,jobId,value.pack);
  output=app.text(app.root);assert.match(output,/Rule fallback: Yes/);assert.match(output,/Call failed; cost bound retained/);
  assert.match(output,/Recorded\/reserved USD: 0\.002688/);assert.match(output,/Choosing a step does not validate evidence quality or a cause/);
});

test('Trace suppresses mismatched Typesafe bindings and handles zero-decision offline artifacts',()=>{
  for(const mutate of [value=>value.sidecar.job_id='b'.repeat(24),value=>value.sidecar.workflow_plan_sha256=hash('8'),
    value=>value.sidecar.source.query_sha256=hash('8'),value=>value.sidecar.max_decisions=5,value=>value.sidecar.model='unconfigured-model',
    value=>value.decision.binding.job_id='b'.repeat(24),value=>value.decision.action='stop',value=>value.decision.receipt.status='reserved',
    value=>value.decision.receipt.binding=null,value=>value.sidecar.records=[null],value=>value.decision.receipt.micro_usd_charged_or_reserved=-1]){
    const app=dom('workflow.js'),value=saved();mutate(value);app.context.window.IssueIntelWorkflow.render(app.root,value.plan,jobId,value.pack);
    assert.match(app.text(app.root),/Không thể gắn quyết định Typesafe/);
    assert.doesNotMatch(app.text(app.root),/USD ghi nhận\/giữ chỗ:/);
  }
  const app=dom('workflow.js'),value=saved();value.plan.seed_annotation=null;value.sidecar.records=[];
  app.context.window.IssueIntelWorkflow.render(app.root,value.plan,jobId,value.pack);assert.match(app.text(app.root),/Không gọi Typesafe trong phiên này/);
});

function generationUsage(){return {job_id:jobId,tracking_available:true,reservations:2,tokens_charged_or_reserved:600,usd_charged_or_reserved:'0.0001',retained_bounds:0,receipts:[]};}
test('running and failed Typesafe receipts retain their bounds without changing Groq summaries',()=>{
  for(const status of ['reserved','failed_bound','missing_usage_bound','settled','breach']){
    const app=dom('usage.js'),paid=receipt(status),usage=generationUsage(),before=JSON.stringify(usage);
    app.context.window.IssueIntelUsage.render(app.root,usage,{id:jobId,controller:'jev',typesafe_receipts:{[paid.reservation_id]:paid}});
    const output=app.text(app.root);assert.equal(JSON.stringify(usage),before);assert.match(output,/Chi phí sinh giả thuyết \/ verifier/);
    assert.match(output,/\$0\.0001/);assert.match(output,/Chi phí quyết định Jev \/ Typesafe/);
    assert.match(output,status==='settled'?/USD ghi nhận\/giữ chỗ 0\.000005/:/USD ghi nhận\/giữ chỗ 0\.002688/);
    if(status!=='settled')assert.match(output,/vào Chưa biết, ra Chưa biết/);
    if(status==='breach')assert.match(output,/Usage Typesafe vượt cận/);
    assert.match(output,/Xuất receipt Typesafe JSON/);assert.doesNotMatch(output,/\$0\.002788/);
  }
});

test('missing, malformed or foreign Typesafe accounting remains unknown even with zero Groq usage',()=>{
  const foreign=receipt();foreign.binding.job_id='b'.repeat(24);
  const negative=receipt();negative.micro_usd_charged_or_reserved=-1;
  const missing=receipt();delete missing.micro_usd_charged_or_reserved;
  const values=[undefined,{},[],{wrong:receipt()},{[foreign.reservation_id]:foreign},{[negative.reservation_id]:negative},{[missing.reservation_id]:missing}];
  for(const typesafe_receipts of values){
    const app=dom('usage.js');app.context.window.IssueIntelUsage.render(app.root,{...generationUsage(),reservations:0,tokens_charged_or_reserved:0,usd_charged_or_reserved:'0'},
      {id:jobId,controller:'jev',typesafe_receipts});
    const section=app.all(app.root).find(item=>item.className==='workflow-controller'),output=app.text(section);
    assert.match(output,/chưa coi chi phí là 0|chi phí chưa biết/);assert.doesNotMatch(output,/USD ghi nhận\/giữ chỗ 0\.000000/);
    assert.doesNotMatch(output,/Xuất receipt Typesafe JSON/);
  }
  const app=dom('usage.js');app.language('en');const paid=receipt('failed_bound');
  app.context.window.IssueIntelUsage.render(app.root,undefined,{id:jobId,controller:'jev',typesafe_receipts:{[paid.reservation_id]:paid},typesafe_decisions:[{fallback:true,binding:{job_id:jobId}}]});
  assert.match(app.text(app.root),/1 decisions used rule fallback/);assert.match(app.text(app.root),/Call failed; cost bound retained/);
  assert.match(app.text(app.root),/not a provider invoice/);
});

const childId='b'.repeat(24),copy=value=>JSON.parse(JSON.stringify(value));
function recoveredSaved({newCalls=1}={}){
  const value=saved(),{plan,pack,sidecar,decision}=value;
  plan.schema='planned_investigation_v2';plan.policy='bounded_planner_v2';
  sidecar.schema='typesafe_controller_v2';
  sidecar.recovery={schema:'typesafe_stage_recovery_v1',owner_job_id:childId,origin_job_id:jobId,
    inherited_calls:1,new_calls:newCalls,max_calls:4,automatic_retry:false,billing_verified:false};
  if(newCalls){
    decision.action='retrieve';
    plan.rounds=[{round:1,input_annotation_sha256:hash('2'),input_evidence_ids:[...plan.seed_evidence_ids],
      input_hypotheses:copy(plan.seed_annotation.hypotheses),action:'retrieve',controller:'jev',fallback:false,
      reason:'unspent_retrieval_budget',search:null,added_ids:[],output_annotation:null,output_annotation_sha256:null}];
    const current=copy(decision);current.decision_index=2;current.action='verify';current.allowed=['verify','stop'];
    current.binding.job_id=childId;current.binding.decision_index=2;
    current.receipt.reservation_id='e'.repeat(32);current.receipt.binding.job_id=childId;current.receipt.binding.decision_index=2;
    sidecar.records.push(current);
  }
  sidecar.decision_owners=sidecar.records.map(record=>record.binding.job_id);
  return value;
}
function usageProjection(value,current=childId){
  const context=vm.createContext({pack:value.pack,currentJob:current,fetch(){throw Error('Saved projection must not fetch');},api(){throw Error('Saved projection must not call APIs');}});
  vm.runInContext(appCode.slice(appCode.indexOf('function savedTypesafeUsage()'),appCode.indexOf('function planDrafts()')),context);
  return copy(context.savedTypesafeUsage());
}

test('recovered Trace preserves mixed decision owners and bilingual source attribution',()=>{
  const value=recoveredSaved(),before=JSON.stringify(value),app=dom('workflow.js');
  app.context.window.IssueIntelWorkflow.render(app.root,value.plan,childId,value.pack);
  let output=app.text(app.root);
  assert.doesNotMatch(output,/Không thể gắn quyết định Typesafe/);
  assert.match(output,/2 \/ 4 lượt quyết định đã ghi/);assert.match(output,/1 đã ghi trước đó \+ 1 mới/);
  assert.ok(output.includes('Phiên sở hữu quyết định: '+jobId));assert.ok(output.includes('Phiên sở hữu quyết định: '+childId));
  assert.match(output,/không gọi Typesafe lại/);assert.match(output,/không cộng vào chi phí phiên phục hồi/);
  app.root.replaceChildren();app.language('en');app.context.window.IssueIntelWorkflow.render(app.root,value.plan,childId,value.pack);
  output=app.text(app.root);assert.match(output,/1 previously recorded \+ 1 new in this run \/ 4/);
  assert.match(output,/2 \/ 4 saved decisions/);
  assert.ok(output.includes('Decision owner run: '+jobId));assert.match(output,/reused without calling Typesafe again/);
  assert.equal(JSON.stringify(value),before);
});

test('recovered Trace rejects changed roots, current owners, receipt owners and reset attempt budgets',()=>{
  const mutations=[value=>value.sidecar.recovery.origin_job_id=childId,
    value=>value.sidecar.recovery.owner_job_id=jobId,value=>value.sidecar.recovery.max_calls=8,
    value=>value.sidecar.recovery.inherited_calls=0,value=>value.sidecar.recovery.new_calls=0,
    value=>value.sidecar.recovery.new_calls=4,value=>value.sidecar.recovery.automatic_retry=true,
    value=>value.sidecar.recovery.billing_verified=true,value=>value.sidecar.decision_owners[0]=childId,
    value=>value.sidecar.records[0].receipt.binding.job_id=childId,
    value=>value.sidecar.records[1].receipt.binding.job_id=jobId];
  for(const mutation of mutations){
    const value=recoveredSaved(),app=dom('workflow.js');mutation(value);
    app.context.window.IssueIntelWorkflow.render(app.root,value.plan,childId,value.pack);
    assert.match(app.text(app.root),/Không thể gắn quyết định Typesafe/);
    assert.doesNotMatch(app.text(app.root),/USD ghi nhận\/giữ chỗ:/);
  }
});

test('saved usage projection retains only current-owner decisions and receipts',()=>{
  const value=recoveredSaved(),before=JSON.stringify(value),projected=usageProjection(value);
  assert.equal(projected.controller,'jev');assert.equal(projected.typesafe_decisions.length,1);
  assert.equal(projected.typesafe_decisions[0].binding.job_id,childId);
  assert.deepEqual(Object.keys(projected.typesafe_receipts),['e'.repeat(32)]);
  assert.equal(projected.typesafe_receipts['e'.repeat(32)].binding.job_id,childId);
  assert.deepEqual(projected.typesafe_recovery,value.sidecar.recovery);
  assert.deepEqual(usageProjection(value,'f'.repeat(24)),{controller:'jev'});
  const wrongOrigin=recoveredSaved();wrongOrigin.sidecar.recovery.origin_job_id=childId;
  assert.deepEqual(usageProjection(wrongOrigin),{controller:'jev'});
  assert.equal(JSON.stringify(value),before);
});

test('a critic-only recovered run reports no new Jev attempts without moving historical charges',()=>{
  const value=recoveredSaved({newCalls:0}),projected=usageProjection(value),before=JSON.stringify(value);
  assert.deepEqual(projected.typesafe_decisions,[]);assert.deepEqual(projected.typesafe_receipts,{});
  for(const language of ['vi','en']){
    const app=dom('usage.js');app.language(language);
    app.context.window.IssueIntelUsage.render(app.root,{...generationUsage(),job_id:childId},{id:childId,...projected});
    const panel=app.all(app.root).find(item=>item.className==='workflow-controller'),output=app.text(panel);
    assert.match(output,language==='vi'?/không tạo lượt thử Jev mới/:/no new Jev attempts/);
    assert.match(output,language==='vi'?/giữ ở phiên nguồn/:/remain in their source runs/);
    assert.doesNotMatch(output,/0\.000005|0\.000000/);
    assert.ok(!app.all(panel).some(item=>item.tag==='button'));
  }
  assert.equal(JSON.stringify(value),before);
});

test('current Jev usage excludes ancestors and malformed recovery metadata stays unknown',()=>{
  const value=recoveredSaved(),projected=usageProjection(value),app=dom('usage.js');
  app.context.window.IssueIntelUsage.render(app.root,{...generationUsage(),job_id:childId},{id:childId,...projected});
  let panel=app.all(app.root).find(item=>item.className==='workflow-controller'),output=app.text(panel);
  assert.match(output,/1 receipt Typesafe/);assert.doesNotMatch(output,/2 receipt Typesafe/);
  const receiptViews=app.all(panel).filter(item=>item.tag==='pre').map(item=>JSON.parse(item.textContent));
  assert.equal(receiptViews.length,1);assert.ok(receiptViews.every(item=>item.binding.job_id===childId));
  projected.typesafe_recovery.new_calls=0;app.root.replaceChildren();
  app.context.window.IssueIntelUsage.render(app.root,generationUsage(),{id:childId,...projected});
  panel=app.all(app.root).find(item=>item.className==='workflow-controller');output=app.text(panel);
  assert.match(output,/chi phí chưa biết/);assert.doesNotMatch(output,/1 receipt Typesafe/);
});

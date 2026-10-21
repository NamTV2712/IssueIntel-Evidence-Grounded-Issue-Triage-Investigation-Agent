const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const dir=path.join(__dirname,'../src/issueintel/interfaces/static');
const code=fs.readFileSync(path.join(dir,'planned-waiting.js'),'utf8'),workflow=fs.readFileSync(path.join(dir,'workflow.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(dir,'locales.js'),'utf8'),locale);const messages=locale.window.IssueIntelMessages;
const sha=c=>c.repeat(64),job=c=>c.repeat(24),sourceJob=job('a'),childJob=job('b'),packSha=sha('4'),clone=value=>JSON.parse(JSON.stringify(value));
function fixture({inherited=false,noRoundsLeft=false}={}){
  const source={query_sha256:sha('1'),snapshot_sha256:sha('2'),commit_sha:'3'.repeat(40)};
  const h={id:'H1',statement:'Empty input might be discarded',support_ids:['E1'],counter_ids:[],counter_status:'not_found',falsification_test:'Inspect empty input'};
  const questions={schema:'issue_clarifications_v1',query_sha256:source.query_sha256,cue_text_sha256:source.query_sha256,cue_basis:'public_query',sha256:sha('5'),limitations:[],questions:[
    {id:'missing:reproduction',field:'reproduction',reason:'cue_not_found',prompts:{en:'Which minimal steps or input reproduce the problem? Include an example if available, or say that you have not reproduced it.',vi:'Các bước hoặc đầu vào tối thiểu nào tái hiện được vấn đề? Nếu có, hãy đưa ví dụ; nếu chưa tái hiện được, hãy nói rõ.'}},
    {id:'missing:environment',field:'environment',reason:'cue_not_found',prompts:{en:'Which software/runtime versions and operating system were involved? Include relevant configuration if known; leave unknown details explicitly unknown.',vi:'Vấn đề xảy ra với phiên bản phần mềm/runtime và hệ điều hành nào? Nếu biết, hãy nêu cấu hình liên quan; ghi rõ chi tiết nào chưa biết.'}}]};
  const limits={max_retrieval_rounds:3,max_queries_per_round:3,max_added_per_round:6,max_added_total:18,max_generation_passes:4,max_verifier_passes:1,max_provider_calls:9};
  const prior={retrieval_rounds:inherited?(noRoundsLeft?2:1):0,generation_passes:inherited?1:0,verifier_passes:0,provider_calls:inherited?1:0,added_evidence:inherited?2:0};
  const round={round:1,input_annotation_sha256:sha('6'),input_evidence_ids:['I1','E1'],input_hypotheses:[h],action:'retrieve',reason:'unspent_retrieval_budget',controller:'rules',fallback:false,
    added_ids:[],output_annotation:null,output_annotation_sha256:null,search:{policy:'gap_directed_retrieval_v1',max_queries:3,max_added_evidence:6,
      excluded_query_sha256s:[sha('1')],skipped_queries:[],queries:[{action_id:'gap:counter:1',purpose:'counter',gap_code:'missing_counter_evidence',reason:'Search falsification condition',
        query:'Inspect empty input',query_sha256:sha('7'),seed_hypothesis_id:'H1',source_ids:['E1'],target_node_ids:[],inspected_ids:[],returned_ids:[],added_ids:[],status:'executed',unavailable_reason:null}],
      added_ids:[],counter_evidence_confirmed:false,snapshot_sha256:source.snapshot_sha256,stop_reason:'no_new_evidence'}};
  const remaining={retrieval_rounds:limits.max_retrieval_rounds-prior.retrieval_rounds-1,generation_passes:limits.max_generation_passes-prior.generation_passes-1,
    verifier_passes:1,provider_calls:limits.max_provider_calls-prior.provider_calls-1,added_evidence:limits.max_added_total-prior.added_evidence};
  const plan={schema:'planned_investigation_v3',policy:'bounded_planner_v3',source,sha256:sha('8'),policy_sha256:sha('9'),provider:'groq',model:'openai/gpt-oss-120b',
    routing:{kind:'observable_heuristic',reason_codes:['snapshot_gaps'],max_retrieval_rounds:3,signals:{code_files:1,not_found_hypotheses:1,parse_error_count:1,skipped_file_count:0}},limits,
    inherited:inherited?{parent_plan_sha256:sha('a'),root_source:{...source,query_sha256:sha('0')},routing:null,limits,counts:prior,used_query_sha256s:[sha('0')],answered_fields:[]}:null,
    seed_evidence_ids:['I1','E1'],seed_annotation:{hypotheses:[h]},rounds:[round],generation_passes:1,verifier_passes:0,provider_calls:1,
    final_decision:{action:'ask',reason:'unreviewed_user_input',controller:'rules',fallback:false},stop_reason:'waiting_for_user',
    waiting:{schema:'planned_waiting_v1',state:'waiting_for_user',reason:'no_new_evidence_with_unreviewed_input',after_round:1,input_annotation_sha256:sha('6'),questions,remaining,sha256:sha('b')}};
  if(plan.inherited)plan.inherited.routing=clone(plan.routing);
  const pack={workflow_plan:plan,snapshot:{commit_sha:source.commit_sha,snapshot_sha256:source.snapshot_sha256},evidence:[{id:'I1',kind:'issue',sha256:source.query_sha256},{id:'E1',kind:'code'}]};
  return {plan,pack};
}
function boot(handler){
  const calls=[],events=[],listeners=new Map();
  class El{constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.dataset={};this.textContent='';this.value='';this.isConnected=true;}
    append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(k,v){this.attributes[k]=v;}set innerHTML(_){throw Error('Source markup must be inert');}}
  const all=el=>[el,...el.children.flatMap(all)],text=el=>el.textContent+' '+el.children.map(text).join(' '),target=new El('div');
  const document={documentElement:{lang:'vi'},createElement:tag=>new El(tag),addEventListener:(name,fn)=>{if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);},
    dispatchEvent:event=>{events.push(event);for(const fn of listeners.get(event.type)||[])fn(event);}};
  const context={document,window:{IssueIntelUI:{t:(key,values={})=>{assert.ok(messages[key]?.[document.documentElement.lang],'Missing translation '+key);
    return messages[key][document.documentElement.lang].replace(/\{(\w+)\}/g,(_,name)=>values[name]);}}},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},
    api:async(route,body)=>{calls.push([route,body]);if(handler)return handler(route,body);if(body)return {id:childJob,mode:'planned_continuation',status:'queued'};return previewValue(value);},
    fetch(){throw Error('Only the local API helper may be used');},localStorage:{setItem(){throw Error('Never persist answer text');}}};
  vm.runInNewContext(code,context);vm.runInNewContext(workflow,context);let value=fixture();
  const button=label=>all(target).find(el=>el.tag==='button'&&el.textContent===label),inputs=()=>all(target).filter(el=>el.tag==='textarea'),check=()=>all(target).find(el=>el.tag==='input'&&el.type==='checkbox');
  const render=(next=value,id=sourceJob,options={sourcePackSha256:packSha})=>{value=next;target.replaceChildren();context.window.IssueIntelWorkflow.render(target,next.plan,id,next.pack,options);};
  const changeLanguage=lang=>{document.documentElement.lang=lang;document.dispatchEvent(new context.CustomEvent('issueintel:languagechange'));};
  const answer=(text,index=0)=>{const input=inputs()[index];input.value=text;input.oninput();};
  const consent=()=>{check().checked=true;check().onchange();};
  return {render,target,all,text,calls,events,context,button,inputs,check,answer,consent,changeLanguage,value:()=>value,
    preview:()=>button('Kiểm tra khả năng tiếp tục').onclick(),submit:()=>button('Tiếp tục Planned với dữ kiện này').onclick()};
}
function previewValue({plan},extra={}){return {source_job_id:sourceJob,source_pack_sha256:packSha,workflow_plan_sha256:plan.sha256,waiting_sha256:plan.waiting.sha256,
  policy_sha256:plan.policy_sha256,available:true,waiting:clone(plan.waiting),remaining:clone(plan.waiting.remaining),continuation_job:null,...extra};}

function recoveredFixture(){
  const data=fixture();data.pack.recovered_from={schema:'product_stage_resume_v1',automatic_retry:false,
    source:{provider:'groq',depth:'planned',sha256:sha('c'),context_sha256:sha('d'),core_policy_sha256:sha('e'),prior_provider_calls:1}};
  data.pack.stage_recovery={schema:'product_pipeline_recovery_v1',context_sha256:sha('d'),policy_sha256:sha('e'),
    inherited_provider_calls:1,new_provider_calls:1,max_provider_calls:9,call_count_kind:'attempt_upper_bound',automatic_retry:false,billing_verified:false};
  return data;
}

test('recovered pause deducts failed original attempts without editing the saved waiting plan',async()=>{
  const data=recoveredFixture(),before=JSON.stringify(data.plan),remaining={...data.plan.waiting.remaining,provider_calls:7};
  const a=boot(()=>previewValue(data,{remaining}));a.render(data);
  assert.match(a.text(a.target),/Lượt thử API trong ngân sách\s+2 \/ 9/);
  assert.match(a.text(a.target),/Stage logic đã hoàn tất trong lượt gốc/);
  assert.match(a.text(a.target),/Call API còn lại\s+7/);assert.match(a.text(a.target),/Đã trừ thêm 1 lượt thử/);
  await a.preview();a.answer('Chưa biết');a.consent();assert.equal(a.button('Tiếp tục Planned với dữ kiện này').disabled,false);
  a.changeLanguage('en');assert.match(a.text(a.target),/Deducted 1 additional/);assert.equal(a.check().checked,false);
  assert.equal(JSON.stringify(data.plan),before);
});

test('server preview cannot restore the logical budget that recovery already consumed',async()=>{
  const data=recoveredFixture(),a=boot(()=>previewValue(data));a.render(data);await a.preview();
  assert.match(a.text(a.target),/Chưa kiểm được nguồn tiếp tục/);assert.equal(a.check().disabled,true);
  a.answer('unknown');a.consent();await a.submit();assert.equal(a.calls.filter(([,body])=>body).length,0);
});

test('unknown recovery accounting hides continuation controls while leaving the saved report usable',()=>{
  const mutations=[d=>delete d.pack.stage_recovery,d=>d.pack.stage_recovery.new_provider_calls=-1,
    d=>d.pack.stage_recovery.inherited_provider_calls=0,d=>d.pack.stage_recovery.new_provider_calls=10,
    d=>d.pack.recovered_from.source.depth='quick',d=>d.pack.stage_recovery.billing_verified=true];
  for(const mutate of mutations){const data=recoveredFixture(),a=boot();mutate(data);a.render(data);
    assert.match(a.text(a.target),/Không xác định được ngân sách attempt/);assert.equal(a.inputs().length,0);
    assert.equal(a.button('Kiểm tra khả năng tiếp tục'),undefined);assert.equal(a.calls.length,0);}
});

test('v3 waiting is a distinct state with current and chain budgets, no automatic reads or paid calls',()=>{
  const a=boot();a.render(fixture({inherited:true}));const output=a.text(a.target);
  assert.match(output,/Đang chờ dữ kiện từ người dùng/);assert.match(output,/Ngân sách toàn chuỗi điều tra/);assert.match(output,/Vòng tìm thêm\s+2 \/ 3/);
  assert.match(output,/Lượt thử API trong ngân sách\s+2 \/ 9/);
  assert.match(output,/Sử dụng trong phiên đang xem\s+Vòng tìm thêm\s+1 \/ 3/);assert.match(output,/Lượt sinh còn lại\s+2/);
  assert.match(output,/thiếu cue không chứng minh thiếu thông tin/);assert.match(output,/chưa xác minh/);assert.equal(a.inputs().length,2);
  assert.equal(a.calls.length,0);assert.equal(a.button('Tiếp tục Planned với dữ kiện này').disabled,true);
});
test('root waiting has zero inherited use and can continue even after the last search round',async()=>{
  const a=boot();a.render();assert.equal(a.inputs().length,2);assert.match(a.text(a.target),/Vòng tìm thêm\s+1 \/ 3/);
  const b=boot();b.render(fixture({inherited:true,noRoundsLeft:true}));assert.equal(b.inputs().length,2);assert.match(b.text(b.target),/Vòng tìm thêm còn lại\s+0/);
  await b.preview();b.answer('unknown');b.consent();assert.equal(b.button('Tiếp tục Planned với dữ kiện này').disabled,false);
});
test('explicit source preview plus answer plus cost consent are all required; partial unknown answer is valid',async()=>{
  const a=boot();a.render();a.answer('chưa biết');await a.submit();assert.equal(a.calls.length,0);
  await a.preview();assert.equal(a.calls.length,1);assert.equal(a.calls[0][1],undefined);assert.equal(a.button('Tiếp tục Planned với dữ kiện này').disabled,true);
  a.consent();const old=a.button('Tiếp tục Planned với dữ kiện này');await old.onclick();await old.onclick();
  const posts=a.calls.filter(([,body])=>body);assert.equal(posts.length,1);const body=posts[0][1];
  assert.equal(posts[0][0],'/api/jobs/'+sourceJob+'/planned-continuation');assert.equal(body.source_pack_sha256,packSha);
  assert.equal(body.workflow_plan_sha256,a.value().plan.sha256);assert.equal(body.waiting_sha256,a.value().plan.waiting.sha256);assert.equal(body.policy_sha256,a.value().plan.policy_sha256);
  assert.equal(body.language,'vi');assert.equal(body.confirmation,'continue_planned_with_api_cost');
  assert.deepEqual(JSON.parse(JSON.stringify(body.answers)),[{question_id:'missing:reproduction',answer:'chưa biết'}]);
  const event=a.events.find(e=>e.type==='issueintel:plannedcontinuation-created');assert.equal(event.detail.job_id,childJob);assert.equal(event.detail.source_job_id,sourceJob);
});
test('blank and oversized answers cannot trigger a call, exact source text remains inert',async()=>{
  const a=boot();a.render();await a.preview();a.answer('   ');a.consent();await a.submit();assert.equal(a.calls.filter(([,body])=>body).length,0);
  a.answer('x'.repeat(4001));assert.equal(a.button('Tiếp tục Planned với dữ kiện này').disabled,true);
  a.answer('<script>untrusted answer</script>');assert.equal(a.inputs()[0].value,'<script>untrusted answer</script>');
  assert.equal(a.all(a.target).some(el=>el.tag==='script'),false);
});
test('drafts survive language and navigation in memory while consent is revoked and prompts translate',async()=>{
  const a=boot();a.render();await a.preview();a.answer('reproduction draft');a.answer('environment draft',1);a.consent();
  a.changeLanguage('en');assert.match(a.text(a.target),/Add context to continue Planned/);assert.match(a.text(a.target),/Which minimal steps/);
  assert.equal(a.inputs()[0].value,'reproduction draft');assert.equal(a.check().checked,false);assert.equal(a.calls.length,1);
  const original=a.value();a.render(fixture(),job('c'));a.render(original);assert.equal(a.inputs()[0].value,'reproduction draft');assert.equal(a.inputs()[1].value,'environment draft');
  assert.equal(a.check().checked,false);assert.equal(a.calls.filter(([,body])=>body).length,0);
});
test('source, policy, waiting, question and remaining-budget drift cannot enable continuation',async()=>{
  const mutations=[v=>v.source_job_id=job('c'),v=>v.source_pack_sha256=sha('0'),v=>v.workflow_plan_sha256=sha('0'),v=>v.waiting_sha256=sha('0'),
    v=>v.policy_sha256=sha('0'),v=>v.remaining.provider_calls++,v=>v.waiting.questions.questions[0].prompts.en='Changed question',v=>v.continuation_job={id:childJob,mode:'other',status:'queued'}];
  for(const mutate of mutations){const data=fixture(),response=previewValue(data);mutate(response);const a=boot(()=>response);a.render(data);await a.preview();
    assert.match(a.text(a.target),/Chưa kiểm được nguồn tiếp tục/);assert.equal(a.check().disabled,true);a.answer('unknown');a.consent();await a.submit();assert.equal(a.calls.filter(([,body])=>body).length,0);}
});
test('malformed v3 cumulative budgets and altered fixed question cues suppress all continuation controls',()=>{
  const mutations=[v=>v.plan.inherited=undefined,v=>v.plan.policy_sha256='bad',v=>v.plan.waiting.remaining.provider_calls++,v=>v.plan.waiting.after_round=2,
    v=>v.plan.waiting.questions.cue_text_sha256=sha('0'),v=>v.plan.waiting.questions.cue_basis='model_guess',v=>v.plan.waiting.questions.questions[0].reason='model_fact',
    v=>v.plan.waiting.questions.questions[0].prompts.vi='<script>substituted question</script>',v=>v.plan.waiting.questions.questions.reverse(),v=>v.plan.waiting.input_annotation_sha256=sha('0'),
    v=>v.plan.waiting.remaining.verifier_passes=0,v=>v.plan.waiting.questions.questions=[],v=>v.plan.final_decision.reason='certain_cause',
    v=>v.plan.rounds[0].search.queries=[],v=>v.plan.inherited={counts:{retrieval_rounds:3,generation_passes:1,verifier_passes:0,provider_calls:1,added_evidence:0}}];
  for(const mutate of mutations){const data=fixture(),a=boot();mutate(data);a.render(data);assert.match(a.text(a.target),/Không thể gắn kế hoạch/);assert.equal(a.inputs().length,0);assert.equal(a.button('Kiểm tra khả năng tiếp tục'),undefined);}
});
test('a lost POST response requires explicit GET reconciliation and opens an existing job without another POST',async()=>{
  const data=fixture();let submitted=false;const a=boot((route,body)=>{if(body){submitted=true;throw Error('Lost acknowledgement');}
    return previewValue(data,submitted?{continuation_job:{id:childJob,mode:'planned_continuation',status:'failed'}}:{});});
  a.render(data);await a.preview();a.answer('unknown');a.consent();await a.submit();assert.match(a.text(a.target),/Chưa biết yêu cầu đã được nhận chưa/);
  assert.equal(a.button('Tiếp tục Planned với dữ kiện này').disabled,true);await a.submit();assert.equal(a.calls.filter(([,body])=>body).length,1);
  await a.preview();assert.match(a.text(a.target),/Đã có một phiên tiếp tục/);assert.equal(a.inputs()[0].disabled,true);
  a.button('Mở phiên Planned tiếp tục').onclick();assert.equal(a.events.filter(e=>e.type==='issueintel:plannedcontinuation-created').length,1);assert.equal(a.calls.filter(([,body])=>body).length,1);
});
test('navigation during GET or POST never repaints or opens a different investigation',async()=>{
  let finish;const data=fixture(),a=boot(()=>new Promise(resolve=>finish=resolve));a.render(data);const pending=a.preview();a.render(fixture(),job('c'));
  finish(previewValue(data));await pending;assert.doesNotMatch(a.text(a.target),/Đã kiểm nguồn và phần ngân sách/);assert.equal(a.check().disabled,true);
  let resolvePost;const b=boot((route,body)=>body?new Promise(resolve=>resolvePost=resolve):previewValue(data));b.render(data);await b.preview();b.answer('unknown');b.consent();const sent=b.submit();
  b.render(fixture(),job('c'));resolvePost({id:childJob,mode:'planned_continuation',status:'queued'});await sent;
  assert.equal(b.events.filter(e=>e.type==='issueintel:plannedcontinuation-created').length,0);
  assert.equal(b.all(b.target).some(el=>el.textContent.startsWith(childJob+' · ')),false);
});
test('returning to an in-flight source reattaches its draft and single intent rather than posting again',async()=>{
  const data=fixture();let finish;const a=boot((route,body)=>body?new Promise(resolve=>finish=resolve):previewValue(data));
  a.render(data);await a.preview();a.answer('draft');a.consent();const pending=a.submit();a.render(fixture(),job('c'));a.render(data);
  assert.equal(a.inputs()[0].value,'draft');assert.equal(a.button('Tiếp tục Planned với dữ kiện này').disabled,true);
  finish({id:childJob,mode:'planned_continuation',status:'queued'});await pending;assert.equal(a.calls.filter(([,body])=>body).length,1);
  assert.equal(a.events.filter(e=>e.type==='issueintel:plannedcontinuation-created').length,1);
});
test('read-only preview accepts reordered object keys, preserves unknowns and never invents historical verification',async()=>{
  const data=fixture(),response=previewValue(data);response.waiting=Object.fromEntries(Object.entries(response.waiting).reverse());
  const a=boot(()=>response);a.render(data);await a.preview();a.answer('unknown');a.consent();assert.equal(a.button('Tiếp tục Planned với dữ kiện này').disabled,false);
  a.changeLanguage('en');const output=a.text(a.target);assert.match(output,/Answers are unverified context, not historical proof/);
  assert.match(output,/does not prove information is absent/);assert.match(output,/remaining budget/);assert.doesNotMatch(output,/verified cause|human validated|100%/);
});
test('missing displayed pack SHA prevents the waiting form; all authored strings are bilingual',()=>{
  const a=boot();a.render(fixture(),sourceJob,{});assert.equal(a.inputs().length,0);assert.equal(a.calls.length,0);
  for(const match of code.matchAll(/\bt\(['"]([^'"]+)['"]/g)){assert.ok(messages[match[1]]?.vi,match[1]);assert.ok(messages[match[1]]?.en,match[1]);}
});

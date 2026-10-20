const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const base=path.join(__dirname,'../src/issueintel/interfaces/static');
const code=fs.readFileSync(path.join(base,'workflow.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(base,'locales.js'),'utf8'),locale);
const messages=locale.window.IssueIntelMessages,hash=char=>char.repeat(64),job=char=>char.repeat(24);
function hypothesis(ids){return [{id:'H1',statement:'The parser may discard empty input.',support_ids:ids,counter_ids:[],counter_status:'not_found',falsification_test:'Inspect parser dispatch.'}];}
function fixture(){
  const source={query_sha256:hash('a'),snapshot_sha256:hash('b'),commit_sha:'c'.repeat(40)};
  const round=(number,input,added)=>({round:number,input_annotation_sha256:hash('d'),input_evidence_ids:input,input_hypotheses:hypothesis(['E1']),
    action:'retrieve',reason:'unspent_retrieval_budget',controller:'rules',fallback:false,added_ids:added,
    output_annotation_sha256:hash('e'),output_annotation:{hypotheses:hypothesis(['E1',...added])},
    search:{queries:[{seed_hypothesis_id:'H1',query:'Inspect empty parse dispatch '+number,query_sha256:hash('f'),added_ids:added}],
      added_ids:added,counter_evidence_confirmed:false,snapshot_sha256:source.snapshot_sha256}});
  const plan={schema:'planned_investigation_v1',policy:'bounded_planner_v1',source,
    routing:{kind:'observable_heuristic',reason_codes:['multiple_code_files','missing_counter_evidence'],max_retrieval_rounds:2,
      signals:{code_files:3,not_found_hypotheses:1,parse_error_count:0,skipped_file_count:1}},
    limits:{max_retrieval_rounds:2,max_queries_per_round:3,max_added_per_round:6,max_added_total:18,max_generation_passes:3,max_verifier_passes:1,max_provider_calls:7},
    seed_evidence_ids:['I1','E1'],seed_annotation:{hypotheses:hypothesis(['E1'])},
    rounds:[round(1,['I1','E1'],['E2']),round(2,['I1','E1','E2'],['E3'])],
    final_decision:{action:'verify',reason:'retrieval_complete',controller:'rules',fallback:false},
    generation_passes:3,verifier_passes:1,provider_calls:4,stop_reason:'round_limit',sha256:hash('9')};
  const pack={snapshot:{commit_sha:source.commit_sha,snapshot_sha256:source.snapshot_sha256},
    evidence:[{id:'I1',kind:'issue',sha256:source.query_sha256},{id:'E1',kind:'code'},{id:'E2',kind:'code'},{id:'E3',kind:'code'}],workflow_plan:plan};
  return {plan,pack};
}
function gapFixture(){
  const value=fixture(),{plan,pack}=value;
  plan.schema='planned_investigation_v2';plan.policy='bounded_planner_v2';
  plan.seed_evidence_ids.push('R1');pack.evidence.push({id:'R1',kind:'repository_context'});
  const query=(purpose,gap,number,extra={})=>({action_id:'gap:'+purpose+':'+number,purpose,gap_code:gap,
    reason:'Inspect this recorded gap within the pinned source scope.',query:'Query for '+purpose+' '+number,
    query_sha256:hash(String(number)),seed_hypothesis_id:'H1',source_ids:['E1'],target_node_ids:[],
    inspected_ids:[],returned_ids:[],added_ids:[],status:'executed',unavailable_reason:null,...extra});
  const search=(queries,added,excluded=[])=>({policy:'gap_directed_retrieval_v1',max_queries:3,max_added_evidence:6,
    queries,added_ids:added,counter_evidence_confirmed:false,snapshot_sha256:plan.source.snapshot_sha256,
    excluded_query_sha256s:[plan.source.query_sha256,...excluded],skipped_queries:[],stop_reason:'new_candidates_added'});
  plan.rounds[0].input_evidence_ids=['I1','E1','R1'];
  plan.rounds[0].search=search([
    query('support','support_scope_incomplete',1,{returned_ids:['E2'],added_ids:['E2']}),
    query('counter','missing_counter_evidence',2,{returned_ids:['candidate-outside-final-evidence']}),
    query('caller','uninspected_callers',3,{status:'unavailable',unavailable_reason:'no_exact_callers_in_available_graph',target_node_ids:['module.py:function:parse']})],['E2']);
  plan.rounds[1].input_evidence_ids=['I1','E1','R1','E2'];
  plan.rounds[1].search=search([
    query('documentation','uninspected_documentation',4,{returned_ids:['E3'],added_ids:['E3']}),
    query('history','uninspected_history',5,{inspected_ids:['R1'],source_ids:['E1','R1'],returned_ids:['R1']}),
    query('support','support_scope_incomplete',6,{status:'unavailable',unavailable_reason:'no_matching_snapshot_evidence'})],['E3'],[hash('1'),hash('2'),hash('3')]);
  return value;
}
function boot(){
  const dispatched=[],events=new Map();
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.attributes={};}
    append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
    setAttribute(name,value){this.attributes[name]=value;}
    set innerHTML(_){throw Error('Source HTML must remain inert');}
  }
  const document={documentElement:{lang:'vi'},createElement:tag=>new Element(tag),addEventListener:(key,fn)=>events.set(key,fn),dispatchEvent:event=>{dispatched.push(event);events.get(event.type)?.(event);}};
  const translate=(key,values={})=>{assert.ok(messages[key]?.[document.documentElement.lang],'Missing translation '+key);return messages[key][document.documentElement.lang].replace(/\{(\w+)\}/g,(_,name)=>values[name]);};
  const context={document,window:{IssueIntelUI:{t:translate}},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    fetch(){throw Error('Workflow rendering must not fetch');},api(){throw Error('Workflow rendering must not call API');}};
  vm.runInNewContext(code,context);
  const target=new Element(),all=item=>[item,...item.children.flatMap(all)],text=item=>item.textContent+' '+item.children.map(text).join(' ');
  return {target,all,text,dispatched,events,render:({plan,pack}=fixture(),id=job('a'))=>{target.replaceChildren();context.window.IssueIntelWorkflow.render(target,plan,id,pack);},language:value=>document.documentElement.lang=value};
}

test('saved workflow presents bounded routing, observed counters and search steps without quality claims',()=>{
  const app=boot();app.render();const output=app.text(app.target);
  assert.match(output,/Điều phối điều tra/);assert.match(output,/chưa đánh giá độ khó hoặc chất lượng/);
  assert.match(output,/chưa phải phản bác đã xác nhận/);assert.match(output,/3 file code · 1 giả thuyết thiếu phản bác/);
  assert.match(output,/Vòng tìm thêm\s+2 \/ 2/);assert.match(output,/Lượt sinh giả thuyết\s+3 \/ 3/);
  assert.match(output,/Giới hạn sinh\/verifier: 7 call/);assert.match(output,/Bước 1: Tìm thêm bằng chứng/);
  assert.match(output,/Call sinh và verifier đã ghi nhận\s+4 \/ 7/);
  assert.match(output,/Bước 2: Tìm thêm bằng chứng/);assert.match(output,/Đã chạm giới hạn vòng tìm thêm/);
  assert.match(output,/chưa phải kiểm người/);assert.doesNotMatch(output,/\d+%/);
  assert.ok(app.all(app.target).filter(item=>item.tag==='details').length>=4);
});

test('citations keep the exact source job and become inert after a new pack or render',()=>{
  const app=boot();app.render();const button=app.all(app.target).find(item=>item.tag==='button'&&item.textContent==='E2');
  button.onclick();assert.equal(app.dispatched.length,1);assert.equal(app.dispatched[0].type,'issueintel:showevidence');
  assert.equal(app.dispatched[0].detail.job_id,job('a'));assert.equal(app.dispatched[0].detail.evidence_id,'E2');
  assert.equal(button.attributes['aria-label'],'Xem bằng chứng E2');
  app.render(fixture(),job('b'));button.onclick();assert.equal(app.dispatched.length,1);
  const current=app.all(app.target).find(item=>item.tag==='button');app.events.get('issueintel:packchange')({detail:{job_id:job('c')}});
  current.onclick();assert.equal(app.dispatched.length,1);
  app.render();const fresh=app.all(app.target).find(item=>item.tag==='button');app.render({plan:null,pack:{}});fresh.onclick();assert.equal(app.dispatched.length,1);
});

test('wrong source binding, stale evidence references and out-of-budget artifacts suppress citations',()=>{
  const mutations=[value=>value.plan.source.query_sha256=hash('0'),value=>value.plan.source.commit_sha='0'.repeat(40),
    value=>value.plan.source.snapshot_sha256=hash('0'),value=>value.plan.schema='other',value=>value.plan.sha256='bad',
    value=>value.plan.limits.max_provider_calls=10,value=>value.plan.provider_calls=8,value=>value.plan.routing.max_retrieval_rounds=4,
    value=>value.plan.seed_evidence_ids.push('missing'),value=>value.plan.seed_evidence_ids.push('I1'),
    value=>value.plan.rounds[0].input_evidence_ids=['I1'],value=>value.plan.rounds[1].input_evidence_ids=['I1','E1'],
    value=>value.plan.rounds[0].input_hypotheses[0].support_ids=['E3'],value=>value.plan.rounds[0].added_ids=['missing'],
    value=>value.plan.rounds[0].search.queries[0].seed_hypothesis_id='missing',value=>value.plan.rounds[0].search.counter_evidence_confirmed=true,
    value=>value.plan.rounds[0].search.snapshot_sha256=hash('0'),value=>value.plan.rounds[0].search.queries[0].added_ids=['E3'],
    value=>value.plan.rounds[0].output_annotation.hypotheses[0].support_ids=['E3'],value=>value.plan.generation_passes=5,
    value=>value.plan.stop_reason='verified_fix',value=>value.plan.final_decision.action='retrieve',value=>value.pack.evidence.push({id:'extra',kind:'code'}),
    value=>value.plan.rounds[0]=null,value=>value.pack.evidence[0]=null,value=>value.plan.source=null];
  for(const mutate of mutations){const value=fixture(),app=boot();mutate(value);app.render(value);
    assert.match(app.text(app.target),/Không thể gắn kế hoạch/);assert.equal(app.all(app.target).filter(item=>item.tag==='button').length,0);}
  const value=fixture(),app=boot();value.pack.workflow_plan={...value.plan};app.render(value);assert.match(app.text(app.target),/Không thể gắn kế hoạch/);
  app.render(fixture(),'bad-job');assert.match(app.text(app.target),/Không thể gắn kế hoạch/);
});

test('literal controller notes and query text remain inert while authored labels switch language',()=>{
  const app=boot(),value=fixture();value.plan.rounds[0].controller='<img src=x onerror=alert(1)>';
  value.plan.rounds[0].reason='<script>source</script>';value.plan.rounds[0].search.queries[0].query='<img src=x> do not run';
  value.plan.rounds[0].search.skipped_queries=[{query_sha256:hash('e')}];app.render(value);
  assert.match(app.text(app.target),/<script>source<\/script>/);assert.match(app.text(app.target),/1 truy vấn trùng đã bỏ qua; chưa chạy lại/);
  app.language('en');app.render(value);const output=app.text(app.target);
  assert.match(output,/Investigation workflow/);assert.match(output,/Search round limit reached/);
  assert.match(output,/<img src=x onerror=alert\(1\)>/);assert.match(output,/<img src=x> do not run/);
  assert.match(output,/not human validation/);
});

test('gap-directed workflow presents distinct actions, source gaps and unavailable scope without inventing findings',()=>{
  const app=boot(),value=gapFixture();app.render(value);let output=app.text(app.target);
  for(const label of ['Tìm nguồn hỗ trợ','Tìm nguồn phản bác','Lần theo caller tĩnh','Tìm tài liệu trong snapshot','Tìm lịch sử trong nguồn đã khóa'])assert.match(output,new RegExp(label));
  assert.match(output,/2 hành động đã chạy · 1 hành động chưa khả dụng · 1 trích đoạn mới/);
  assert.match(output,/Caller tĩnh chưa được xem/);assert.match(output,/Chưa tìm thấy caller chính xác trong graph được cung cấp/);
  assert.match(output,/1 ứng viên trả về · 1 nguồn lịch sử đã xem · 0 trích đoạn mới/);
  assert.match(output,/chưa xác minh tính sẵn có trước thời điểm issue/);
  assert.match(output,/candidate-outside-final-evidence/);
  assert.equal(app.all(app.target).some(item=>item.tag==='button'&&item.textContent==='candidate-outside-final-evidence'),false);
  assert.match(output,/Giới hạn sinh\/verifier: 7 call/);assert.match(output,/chưa phải phản bác đã xác nhận/);
  app.language('en');app.render(value);output=app.text(app.target);
  for(const label of ['Search for supporting sources','Search for counter-evidence','Trace static callers','Search snapshot documentation','Search source-bound history'])assert.ok(output.includes(label),label);
  assert.match(output,/Static callers have not been inspected/);assert.match(output,/No exact caller was found in the supplied graph/);
  assert.match(output,/availability before the issue time has not been verified/);
  assert.match(output,/2 actions executed · 1 actions unavailable · 1 new excerpts/);
  assert.match(output,/Gap code: uninspected_history/);assert.match(output,/not human validation/);
  assert.doesNotMatch(output,/waiting for user|verified cause|verified fix/i);
});

test('gap action summaries localize authored purposes while recorded reasons remain verbatim in source details',()=>{
  const app=boot(),value=gapFixture();
  const visible=item=>item.tag==='details'?'':item.textContent+' '+item.children.map(visible).join(' ');
  app.render(value);let output=visible(app.target);
  assert.match(output,/Tìm code bổ sung trong cùng snapshot/);assert.match(output,/Tìm theo điều kiện bác bỏ đã đề xuất/);
  assert.match(output,/Xem caller tĩnh chính xác/);assert.match(output,/Tìm tài liệu trong cùng snapshot/);
  assert.match(output,/Xem context có receipt và changelog/);
  assert.doesNotMatch(output,/Inspect this recorded gap within the pinned source scope/);
  assert.match(app.text(app.target),/Lý do đã ghi: Inspect this recorded gap within the pinned source scope/);
  app.language('en');app.render(value);output=visible(app.target);
  assert.match(output,/Search for additional code in the same snapshot/);assert.match(output,/Search the proposed falsification condition/);
  assert.match(output,/Inspect exact static callers/);assert.match(output,/Search documentation in the same snapshot/);
  assert.match(output,/Inspect receipt-bound context and snapshot changelog/);
  assert.doesNotMatch(output,/Inspect this recorded gap within the pinned source scope/);
  assert.match(app.text(app.target),/Recorded reason: Inspect this recorded gap within the pinned source scope/);
});

test('every unavailable gap reason describes its supplied source scope and never appears as executed',()=>{
  const variants=[['support','support_scope_incomplete','no_matching_snapshot_evidence','No matching evidence'],
    ['counter','missing_counter_evidence','no_matching_snapshot_evidence','No matching evidence'],
    ['caller','uninspected_callers','no_exact_callers_in_available_graph','No exact caller'],
    ['caller','uninspected_callers','no_matching_snapshot_evidence','No matching evidence'],
    ['documentation','uninspected_documentation','no_documentation_in_snapshot','No matching documentation'],
    ['history','uninspected_history','no_history_in_available_sources','No matching history']];
  for(const [purpose,gap_code,unavailable_reason,label] of variants){
    const app=boot(),value=gapFixture(),row=value.plan.rounds[0];
    row.search.queries=[{...row.search.queries[0],purpose,gap_code,status:'unavailable',unavailable_reason,returned_ids:[],added_ids:[]}];
    row.search.added_ids=[];row.search.stop_reason='no_new_evidence';row.added_ids=[];row.output_annotation=null;row.output_annotation_sha256=null;
    value.plan.rounds=[row];value.plan.generation_passes=1;value.plan.provider_calls=2;value.plan.stop_reason='no_new_evidence';
    value.pack.evidence=value.pack.evidence.filter(item=>!['E2','E3'].includes(item.id));app.language('en');app.render(value);
    const output=app.text(app.target);assert.match(output,new RegExp(label));assert.match(output,/0 actions executed · 1 actions unavailable · 0 new excerpts/);
    assert.match(output,/unavailable describes only the scope of the supplied sources/);assert.match(output,/No new evidence found/);
  }
});

test('gap-directed source citations become inert after navigation and source text never becomes markup',()=>{
  const app=boot(),value=gapFixture(),query=value.plan.rounds[1].search.queries[1];
  query.query='<script>never execute</script>';query.reason='<img src=x onerror=alert(1)>';
  query.action_id='<iframe src=https://example.invalid>';query.returned_ids=['<img candidate>'];
  app.render(value);const output=app.text(app.target);
  for(const literal of [query.query,query.reason,query.action_id,query.returned_ids[0]])assert.ok(output.includes(literal));
  assert.equal(app.all(app.target).some(item=>['script','img','iframe'].includes(item.tag)),false);
  const history=app.all(app.target).find(item=>item.tag==='button'&&item.textContent==='R1');assert.ok(history);
  assert.equal(history.attributes['aria-label'],'Xem bằng chứng R1');history.onclick();
  assert.equal(app.dispatched[0].detail.job_id,job('a'));assert.equal(app.dispatched[0].detail.evidence_id,'R1');
  app.render(gapFixture(),job('b'));history.onclick();assert.equal(app.dispatched.length,1);
  const fresh=app.all(app.target).find(item=>item.tag==='button'&&item.textContent==='R1');
  app.events.get('issueintel:packchange')({detail:{job_id:job('c')}});fresh.onclick();assert.equal(app.dispatched.length,1);
});

test('malformed gap actions cannot expose citations or be relabeled as a different source search',()=>{
  const mutations=[value=>value.plan.policy='bounded_planner_v1',value=>value.plan.rounds[0].search.policy='hypothesis_focused_retrieval_v1',
    value=>value.plan.rounds[0].search.max_queries=4,value=>value.plan.rounds[0].search.max_added_evidence=7,
    value=>value.plan.rounds[0].search.excluded_query_sha256s=['bad'],value=>value.plan.rounds[0].search.excluded_query_sha256s=Array.from({length:11},(_,index)=>String(index).padStart(64,'0')),
    value=>value.plan.rounds[0].search.skipped_queries=[{executed:true}],value=>value.plan.rounds[0].search.stop_reason='confirmed_counter_evidence',
    value=>value.plan.rounds[0].search.queries[0].purpose='web',value=>value.plan.rounds[0].search.queries[0].gap_code='missing_counter_evidence',
    value=>value.plan.rounds[0].search.queries[0].reason='',value=>value.plan.rounds[0].search.queries[0].reason='x'.repeat(201),
    value=>value.plan.rounds[0].search.queries[0].query='x'.repeat(4001),value=>value.plan.rounds[0].search.queries[0].source_ids=['foreign'],
    value=>value.plan.rounds[0].search.queries[0].target_node_ids=['same','same'],value=>value.plan.rounds[0].search.queries[0].inspected_ids=['R1'],
    value=>value.plan.rounds[1].search.queries[1].inspected_ids=['E1'],value=>value.plan.rounds[0].search.queries[0].status='verified',
    value=>value.plan.rounds[0].search.queries[0].unavailable_reason='no_matching_snapshot_evidence',
    value=>value.plan.rounds[0].search.queries[2].returned_ids=['E1'],value=>value.plan.rounds[0].search.queries[2].added_ids=['E2'],
    value=>value.plan.rounds[0].search.queries[2].unavailable_reason='no_history_in_available_sources',
    value=>value.plan.rounds[0].search.queries[1].action_id=value.plan.rounds[0].search.queries[0].action_id,
    value=>value.plan.rounds[0].search.queries[0]=null];
  for(const mutate of mutations){const app=boot(),value=gapFixture();mutate(value);app.render(value);
    assert.match(app.text(app.target),/Không thể gắn kế hoạch/);assert.equal(app.all(app.target).filter(item=>item.tag==='button').length,0);}
});

test('skipped gap actions are not counted as searches and their recorded duplicate intent stays inert',()=>{
  const app=boot(),value=gapFixture(),row=value.plan.rounds[0];
  row.search.skipped_queries=[{action_id:'gap:counter:old',purpose:'counter',seed_hypothesis_id:'H1',query_sha256:hash('0'),reason:'already_executed',executed:false}];
  app.language('en');app.render(value);const output=app.text(app.target);
  assert.match(output,/1 repeated queries skipped; they were not executed again/);
  assert.match(output,/2 actions executed · 1 actions unavailable/);
  assert.doesNotMatch(output,/3 actions executed/);
});

test('every saved stop reason is distinct and legacy reports never invent a planner trace',()=>{
  const reasons=['provider_not_configured','insufficient_evidence','no_new_queries','no_new_evidence','round_limit','controller_stopped','controller_skipped_retrieval'];
  const rendered=new Set();for(const reason of reasons){const app=boot(),value=fixture();value.plan.stop_reason=reason;app.language('en');app.render(value);
    rendered.add(app.all(app.target).find(item=>item.className==='workflow-stop').children[0].textContent);}
  assert.equal(rendered.size,reasons.length);
  const app=boot();app.render({plan:undefined,pack:{}});assert.match(app.text(app.target),/không có kế hoạch Planned đã lưu/);
  assert.equal(app.all(app.target).filter(item=>item.tag==='button').length,0);assert.equal(app.all(app.target).filter(item=>item.className==='workflow-round').length,0);
  const value=fixture();value.plan.rounds=[];value.plan.seed_evidence_ids=value.pack.evidence.map(item=>item.id);value.plan.seed_annotation=null;
  value.plan.generation_passes=0;value.plan.verifier_passes=0;value.plan.provider_calls=0;value.plan.stop_reason='provider_not_configured';
  value.plan.final_decision={action:'stop',reason:'provider_not_configured',controller:'rules',fallback:false};app.render(value);
  assert.match(app.text(app.target),/Chưa bật provider; dừng ở bằng chứng/);assert.match(app.text(app.target),/Lượt sinh giả thuyết\s+0 \/ 3/);
});

test('Planned is an explicit product-only selector and changing it never enables Groq',()=>{
  const html=fs.readFileSync(path.join(base,'index.html'),'utf8'),appCode=fs.readFileSync(path.join(base,'app.js'),'utf8');
  const selects=[...html.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)];
  assert.deepEqual(selects.filter(match=>/value="planned"/.test(match[2])).map(match=>match[1]),['depth']);
  const main=selects.find(match=>match[1]==='depth');assert.match(main[2],/^<option value="quick"/);
  assert.match(main[2],/id="planned-depth-option" value="planned" disabled/);
  assert.match(html,/src="\/workflow.js"/);assert.match(appCode,/IssueIntelWorkflow\?\.render\(content,pack.workflow_plan,currentJob,pack,\{sourcePackSha256:reportView\?\.source\?\.pack_sha256\}\)/);
  assert.match(appCode,/workflow_plan:pack.workflow_plan/);assert.match(appCode,/\$\('depth'\)\.onchange=updateDepthControls/);
  const source=appCode.slice(appCode.indexOf('function updateDepthControls()'),appCode.indexOf('function switchMode()'));
  const controls=new Map([['mode',{value:'local'}],['depth',{value:'planned'}],['planned-depth-option',{disabled:true}],['depth-note',{dataset:{}}],['use-groq',{checked:false}]]);
  const context={$:id=>controls.get(id),t:key=>messages[key].en,fetch(){throw Error('Selector must not fetch');}};vm.runInNewContext(source,context);
  for(const mode of ['local','github','example']){controls.get('mode').value=mode;controls.get('depth').value='planned';context.updateDepthControls();
    assert.equal(controls.get('planned-depth-option').disabled,false);assert.equal(controls.get('use-groq').checked,false);
    assert.match(controls.get('depth-note').textContent,/up to 9 calls/);assert.match(controls.get('depth-note').textContent,/1–3 search rounds/);}
  for(const mode of ['main_dev','followup','context']){controls.get('mode').value=mode;controls.get('depth').value='planned';context.updateDepthControls();
    assert.equal(controls.get('depth').value,'quick');assert.equal(controls.get('planned-depth-option').disabled,true);assert.equal(controls.get('use-groq').checked,false);}
});

test('workflow labels and numbered checkpoint stages are bilingual with matching templates',()=>{
  for(const match of code.matchAll(/\bt\(['"]([^'"]+)['"]/g)){const key=match[1];assert.ok(messages[key]?.vi,key);assert.ok(messages[key]?.en,key);}
  for(const key of ['planned_input','planned_complete',...[1,2,3].flatMap(number=>['planned_retrieval_'+number,'planned_generation_'+number])]){
    assert.ok(messages[key]?.vi,key);assert.ok(messages[key]?.en,key);}
  for(const [key,value] of Object.entries(messages)){
    const slots=text=>[...text.matchAll(/\{(\w+)\}/g)].map(item=>item[1]).sort();assert.deepEqual(slots(value.vi),slots(value.en),key);}
});

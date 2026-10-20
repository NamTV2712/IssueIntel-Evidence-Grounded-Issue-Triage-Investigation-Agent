const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const base=path.join(__dirname,'../src/issueintel/interfaces/static');
const code=fs.readFileSync(path.join(base,'report.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(base,'locales.js'),'utf8'),locale);
const messages=locale.window.IssueIntelMessages,hash=letter=>letter.repeat(64),job=letter=>letter.repeat(24);
function report(){
  const hypotheses=Array.from({length:4},(_,index)=>({id:'H'+(index+1),statement:'Distinct mechanism '+(index+1),
    support_ids:['E1'],counter_ids:[],counter_status:'not_found',falsification_test:'Inspect input '+(index+1),
    verification:{citation_integrity:'valid',semantic_support:'unverified',model_assessment:'supported',model_reason:'The model sees a potential path.',model_evidence_ids:['E1'],independent_human:false}}));
  return {schema:'investigation_report_v1',source:{job_id:job('a'),pack_sha256:hash('b'),pack_hash_kind:'serialized_pack_bytes',query_sha256:hash('c'),snapshot_sha256:hash('d'),commit_sha:'1'.repeat(40)},
    issue:{case_id:'fixture',repository:'synthetic/fixture',issue_url:'https://github.com/synthetic/fixture/issues/12'},
    execution:{status:'completed',stop_reason:'fixture'},findings:{status:'hypotheses_unverified',causal_status:'unverified',severity:'unassessed',confidence_probability:null,confidence_method:null},
    summary:{text:'The reporter says parsing empty input fails.',kind:'issue_excerpt',evidence_ids:['I1'],truncated:false},
    evidence:[{id:'I1',kind:'issue',locator:'fixture',revision:null,sha256:hash('f'),excerpt_sha256:hash('f'),url:'https://github.com/synthetic/fixture/issues/12'},
      {id:'E1',kind:'code',locator:'src/parser.py:2-8',revision:'1'.repeat(40),sha256:hash('9'),excerpt_sha256:hash('9'),url:'https://github.com/synthetic/fixture/blob/'+'1'.repeat(40)+'/src/parser.py#L2-L8'}],
    hypotheses,candidate_locations:[{path:'src/parser.py',start_line:2,end_line:8,evidence_ids:['E1']}],
    next_checks:hypotheses.map((item,index)=>({id:'check'+index,text:item.falsification_test,kind:'proposed_check',execution_status:'not_executed_by_issueintel',hypothesis_ids:[item.id],evidence_ids:['E1']})),
    unknowns:['runtime_not_executed','causal_unverified','no_independent_human','unassessed_severity','uncalibrated_confidence','counter_evidence_not_found_in_retrieved_scope'],
    limitations:['Synthetic sources only.'],sha256:hash('e')};
}
function boot(){
  const dispatched=[];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.attributes={};}
    append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
    setAttribute(name,value){this.attributes[name]=value;}
    set innerHTML(_){throw Error('Unsafe HTML');}
  }
  const document={documentElement:{lang:'vi'},createElement:tag=>new Element(tag),dispatchEvent:event=>dispatched.push(event)};
  const context={document,window:{IssueIntelUI:{t:(key,values={})=>{
    assert.ok(messages[key]?.[document.documentElement.lang],'Missing translation '+key);
    return messages[key][document.documentElement.lang].replace(/\{(\w+)\}/g,(_,name)=>values[name]);
  }}},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
  fetch(){throw Error('Rendering must not fetch');},api(){throw Error('Rendering must not call API');}};
  vm.runInNewContext(code,context);
  const target=new Element(),all=item=>[item,...item.children.flatMap(all)],text=item=>item.textContent+' '+item.children.map(text).join(' ');
  return {target,all,text,dispatched,render:(value=report(),id=job('a'),expected=value?.source)=>context.window.IssueIntelReport.render(target,value,id,expected),
    language:value=>document.documentElement.lang=value};
}

test('overview separates reporter excerpt, hypotheses, proposed checks and independent verification',()=>{
  const app=boot();app.render();const output=app.text(app.target);
  assert.match(output,/người báo issue/);assert.match(output,/chưa xác minh hành vi/);
  assert.match(output,/The reporter says parsing/);assert.match(output,/Cần xác minh thêm/);
  assert.match(output,/chưa chạy code hoặc test/);assert.match(output,/Nguồn model dùng để hỗ trợ/);
  assert.match(output,/Tính toàn vẹn citation: Khớp nguồn/);assert.match(output,/Ý nghĩa bằng chứng: Chưa xác minh/);
  assert.match(output,/Nhận xét model: Được hỗ trợ/);
  assert.match(output,/Chưa có kiểm người độc lập/);assert.match(output,/Chưa có xác suất tin cậy đã hiệu chỉnh/);
  assert.match(output,/không chứng minh giả thuyết đúng/);assert.match(output,/chưa xác nhận là vị trí gây lỗi hoặc cần sửa/);
  assert.doesNotMatch(output,/\d+%/);
});

test('progressive detail preserves source order and shows all saved hypotheses and checks',()=>{
  const app=boot();app.render();const elements=app.all(app.target);
  const sections=elements.filter(item=>item.className==='report-section');
  const ideas=sections.find(item=>item.children[0].textContent==='Giả thuyết cần kiểm tra');
  assert.equal(ideas.children.filter(item=>item.className==='report-hypothesis').length,2);
  const more=ideas.children.find(item=>item.tag==='details');assert.equal(more.children[0].textContent,'Xem thêm 2 giả thuyết');
  assert.deepEqual(elements.filter(item=>item.tag==='h4').map(item=>item.textContent.slice(0,2)),['H1','H2','H3','H4']);
  assert.equal(elements.filter(item=>item.className==='report-check').length,4);
  assert.ok(elements.some(item=>item.tag==='summary'&&item.textContent==='Xem thêm 1 bước kiểm tra'));
});

test('evidence buttons retain exact source job and reject stale buttons after another report is rendered',()=>{
  const app=boot();app.render();const button=app.all(app.target).find(item=>item.tag==='button'&&item.textContent==='E1');
  button.onclick();assert.equal(app.dispatched[0].type,'issueintel:showevidence');assert.equal(app.dispatched[0].detail.job_id,job('a'));
  assert.equal(app.dispatched[0].detail.evidence_id,'E1');assert.equal(button.attributes['aria-label'],'Xem bằng chứng E1');
  const other=report();other.source.job_id=job('9');app.render(other,job('9'));button.onclick();assert.equal(app.dispatched.length,1);
  const current=app.all(app.target).find(item=>item.tag==='button');app.render(null);current.onclick();assert.equal(app.dispatched.length,1);
});

test('same run with a mismatched source hash or malformed contract fails closed',()=>{
  const mutations=[value=>value.schema='other',value=>value.source.job_id=job('9'),value=>delete value.source.pack_sha256,
    value=>value.findings.confidence_probability=.86,value=>value.findings.severity='high',value=>value.findings.causal_status='verified',
    value=>value.summary.evidence_ids=['missing'],value=>value.evidence.push(value.evidence[0]),value=>delete value.evidence[0].sha256,
    value=>value.hypotheses[0].support_ids=['missing'],value=>value.hypotheses[0].verification.model_evidence_ids=['missing'],
    value=>value.hypotheses[0].verification.independent_human=true,value=>value.hypotheses[0].verification.semantic_support='verified',
    value=>value.candidate_locations[0].evidence_ids=['missing'],value=>value.next_checks[0].hypothesis_ids=['missing'],
    value=>value.next_checks[0].execution_status='passed'];
  for(const mutate of mutations){const value=report(),app=boot();mutate(value);app.render(value);assert.match(app.text(app.target),/Chưa đọc được báo cáo tổng quan/);assert.equal(app.all(app.target).filter(item=>item.tag==='button').length,0);}
  for(const key of ['pack_sha256','query_sha256','snapshot_sha256','commit_sha']){
    const value=report(),expected={...value.source,[key]:'0'.repeat(key==='commit_sha'?40:64)},app=boot();app.render(value,job('a'),expected);
    assert.match(app.text(app.target),/Chưa đọc được báo cáo tổng quan/);
  }
});

test('code links must match repository, exact commit and locator; forged URLs never become links',()=>{
  const good=boot();good.render();const links=good.all(good.target).filter(item=>item.tag==='a');
  assert.ok(links.length);assert.equal(links[0].href,report().evidence[1].url);assert.equal(links[0].rel,'noreferrer noopener');
  for(const mutate of [value=>value.evidence[1].url='javascript:alert(1)',value=>value.evidence[1].url='https://example.com/source',
    value=>value.evidence[1].url=value.evidence[1].url.replace('/blob/','/blob/HEAD/'),value=>value.evidence[1].revision='2'.repeat(40),
    value=>value.issue.issue_url='https://github.com/other/repo/issues/1',value=>value.issue.issue_url=null,
    value=>value.evidence[1].locator='../secret:2-8']){
    const value=report(),app=boot();mutate(value);app.render(value);assert.equal(app.all(app.target).filter(item=>item.tag==='a').length,0);
  }
});

test('source text remains inert and language changes translate only authored presentation without API reads',()=>{
  const app=boot(),value=report();value.summary.text='<img src=x onerror=alert(1)>';value.hypotheses[0].statement='<script>source</script>';
  value.next_checks[0].text='Original source check';app.render(value);assert.match(app.text(app.target),/<img src=x onerror=alert\(1\)>/);
  app.language('en');app.render(value);const output=app.text(app.target);assert.match(output,/What does the issue report/);
  assert.match(output,/No independent human review/);assert.match(output,/<script>source<\/script>/);assert.match(output,/Original source check/);
});

test('fixed report limitations localize while dynamic limitations and all source text remain verbatim',()=>{
  const authored=[
    'The summary is an exact issue excerpt, not an independently verified observation.',
    'Candidate locations are retrieved code, not confirmed affected files.',
    'Citation integrity and model critique do not establish causal truth.',
    'No independent human validation is claimed.',
    'Suggested checks were not executed by IssueIntel; user checklist states are separate.',
    'Unknown severity and uncalibrated probability remain unassessed/null.'
  ];
  const value=report(),app=boot(),dynamic='<script>Unrecognized source limitation</script>';
  value.limitations=[...authored,dynamic,'constructor'];value.unknowns.push('constructor');
  value.summary.text=authored[0];app.render(value);
  const unknowns=()=>app.all(app.target).find(item=>item.className==='report-unknowns');
  let output=app.text(unknowns());assert.match(output,/Tóm tắt là trích nguyên văn issue/);
  assert.match(output,/Vị trí ứng viên là code được truy xuất/);assert.match(output,/Citation khớp nguồn/);
  assert.match(output,/Chưa có xác nhận kiểm người độc lập/);assert.match(output,/trạng thái checklist do người dùng tự báo cáo/);
  assert.match(output,/xác suất chưa hiệu chỉnh được giữ ở null/);
  for(const limitation of authored)assert.ok(!output.includes(limitation));
  assert.ok(output.includes(dynamic));assert.ok(output.includes('constructor'));
  assert.equal(app.all(app.target).find(item=>item.tag==='blockquote').textContent,authored[0]);
  app.language('en');app.render(value);output=app.text(unknowns());
  for(const limitation of authored)assert.ok(output.includes(limitation));
  assert.ok(output.includes(dynamic));assert.equal(value.limitations[0],authored[0]);
});

test('evidence-only and unavailable reports remain useful without inventing model or verification output',()=>{
  const value=report();value.hypotheses=[];value.next_checks=[];value.findings.status='evidence_only';
  const app=boot();app.render(value);let output=app.text(app.target);assert.match(output,/chưa có giả thuyết/);assert.match(output,/chưa sinh giả thuyết/);
  assert.doesNotMatch(output,/The model sees/);assert.match(output,/Chưa có bước kiểm tra/);
  app.render(null);output=app.text(app.target);assert.match(output,/JSON pack gốc vẫn dùng được/);
});

test('report labels are bilingual with matching placeholders and HTML wires an explicit report export',()=>{
  for(const match of code.matchAll(/\bt\(['"]([^'"]+)['"]/g)){
    const key=match[1];assert.ok(messages[key]?.vi,key);assert.ok(messages[key]?.en,key);
    const slots=text=>[...text.matchAll(/\{(\w+)\}/g)].map(value=>value[1]).sort();
    assert.deepEqual(slots(messages[key].vi),slots(messages[key].en),key);
  }
  const html=fs.readFileSync(path.join(base,'index.html'),'utf8');
  assert.match(html,/data-tab="overview"/);assert.match(html,/id="export-report-json"/);assert.match(html,/src="\/report.js"/);
});

test('waiting overview preserves unverified findings and has a distinct bilingual status without fetching',()=>{
  const value=report();value.execution={status:'waiting_for_user',stop_reason:'waiting_for_user'};
  const app=boot();app.render(value);assert.match(app.text(app.target),/Đang chờ câu trả lời/);
  assert.match(app.text(app.target),/Cần xác minh thêm/);
  app.language('en');app.render(value);assert.match(app.text(app.target),/Waiting for user input/);
  assert.match(app.text(app.target),/Cause unverified/);
});

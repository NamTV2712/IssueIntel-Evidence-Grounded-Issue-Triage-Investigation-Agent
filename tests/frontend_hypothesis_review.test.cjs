const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const base=path.join(__dirname,'../src/issueintel/interfaces/static');
const code=fs.readFileSync(path.join(base,'hypothesis-review.js'),'utf8');
const appCode=fs.readFileSync(path.join(base,'app.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(base,'locales.js'),'utf8'),locale);
const messages=locale.window.IssueIntelMessages,hash=letter=>letter.repeat(64),job=letter=>letter.repeat(24);
const clone=value=>JSON.parse(JSON.stringify(value));
const sorted=value=>Array.isArray(value)?value.map(sorted):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,sorted(value[key])])):value;

function fixture(){
  const source={job_id:job('a'),run_id:'synthetic-run',pack_sha256:hash('b'),pack_hash_kind:'serialized_pack_bytes',query_sha256:hash('c'),snapshot_sha256:hash('d'),commit_sha:'1'.repeat(40)};
  const issue={case_id:'synthetic:comparison',repository:'synthetic/fixture',issue_url:null};
  const evidence=[['I1','issue'],['E1','code'],['E2','code'],['C1','repository_context']].map(([id,kind])=>({id,kind,locator:kind==='code'?'src/parser.py:1-2':id,
    revision:kind==='code'?source.commit_sha:null,sha256:hash('e'),excerpt_sha256:hash('e'),text:'Saved source',text_truncated:false,url:null}));
  const specifications=[['H1',['E2','E1'],['C1'],'supported'],['H2',['E1','E2','C1'],['I1'],'contradicted'],
    ['H3',['I1'],[],'uncertain'],['H4',['C1'],[],null],['H5',['E1'],[],'unsupported']];
  const hypotheses=specifications.map(([id,support_ids,counter_ids,assessment])=>({id,statement:'Original mechanism '+id,
    support_ids,counter_ids,counter_status:counter_ids.length?'found':'not_found',falsification_test:'Inspect original input '+id,
    verification:{citation_integrity:'valid',semantic_support:'unverified',model_assessment:assessment,
      model_reason:assessment?'Automatic review '+id:null,model_evidence_ids:assessment?[support_ids[0]]:[],independent_human:false}}));
  const checks=[{id:'K1',text:'Compare parser and caller behavior',hypothesis_ids:['H1','H2'],evidence_ids:['E2','E1','C1','I1']},
    ...hypotheses.slice(2).map(row=>({id:'K'+row.id.slice(1),text:row.falsification_test,hypothesis_ids:[row.id],evidence_ids:[...row.support_ids]}))]
    .map(row=>({...row,kind:'proposed_check',execution_status:'not_executed_by_issueintel'}));
  const report={schema:'investigation_report_v1',source,issue,hypotheses,evidence,next_checks:checks,sha256:hash('f')};
  const projected=hypotheses.map(row=>{const review=row.verification,gap_codes=[];
    if(row.counter_status==='not_found')gap_codes.push('missing_counter_in_retrieved_scope');
    if(!row.support_ids.some(id=>evidence.find(item=>item.id===id).kind==='code'))gap_codes.push('no_code_support_citation');
    if(review.model_assessment===null)gap_codes.push('model_review_not_recorded');
    else if(review.model_assessment!=='supported')gap_codes.push('model_review_'+review.model_assessment);
    return {id:row.id,statement:row.statement,support_ids:row.support_ids,counter_ids:row.counter_ids,counter_status:row.counter_status,
      citation_integrity:'valid',semantic_support:'unverified',model_review:{assessment:review.model_assessment,reason:review.model_reason,evidence_ids:review.model_evidence_ids,independent_human:false},
      check_ids:checks.filter(check=>check.hypothesis_ids.includes(row.id)).map(check=>check.id),gap_codes};});
  const pairs=[];
  for(let left=0;left<projected.length;left++)for(let right=left+1;right<projected.length;right++){
    const a=projected[left],b=projected[right],intersection=(xs,ys,inventory=evidence)=>inventory.filter(row=>xs.includes(row.id)&&ys.includes(row.id)).map(row=>row.id);
    pairs.push({left_hypothesis_id:a.id,right_hypothesis_id:b.id,shared_support_ids:intersection(a.support_ids,b.support_ids),shared_counter_ids:intersection(a.counter_ids,b.counter_ids),
      left_support_right_counter_ids:intersection(a.support_ids,b.counter_ids),left_counter_right_support_ids:intersection(a.counter_ids,b.support_ids),shared_check_ids:intersection(a.check_ids,b.check_ids,checks)});
  }
  const view={schema:'hypothesis_review_v1',source:{...source,report_sha256:report.sha256},issue:clone(issue),hypotheses:projected,pairs,checks:clone(checks),evidence:clone(evidence),
    correctness:'unverified',independent_human:false,evaluation_performed:false,model_calls:0,limitations:['Saved citation roles do not establish truth.'],sha256:hash('9')};
  return {view,report,expected:source};
}

function boot(){
  const events=new Map(),dispatched=[];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.attributes={};}
    append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
    setAttribute(name,value){this.attributes[name]=value;}
    set innerHTML(_){throw Error('Unsafe HTML');}
  }
  const document={documentElement:{lang:'vi'},createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>dispatched.push(event)};
  const context={document,window:{IssueIntelUI:{t:(key,values={})=>{
    assert.ok(messages[key]?.[document.documentElement.lang],'Missing translation '+key);
    return messages[key][document.documentElement.lang].replace(/\{(\w+)\}/g,(_,name)=>values[name]);
  }}},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},fetch(){throw Error('Rendering must not fetch');}};
  vm.runInNewContext(code,context);
  const target=new Element(),all=item=>[item,...item.children.flatMap(all)],text=item=>item.textContent+' '+item.children.map(text).join(' ');
  const renderer=context.window.IssueIntelHypothesisReview;
  return {target,all,text,events,dispatched,renderer,render:(value=fixture(),options={})=>{
    target.replaceChildren();renderer.render(target,value.view,job('a'),value.report,value.expected,options);},language:value=>document.documentElement.lang=value};
}

test('comparison surfaces all recorded gap states and distinguishes model roles from confirmed truth',()=>{
  const app=boot();app.render();const output=app.text(app.target);
  for(const key of ['Thiếu nguồn phản bác trong phạm vi đã tra','Chưa có citation hỗ trợ từ code','Chưa ghi nhận nhận xét model',
    'Model chưa thấy nguồn hỗ trợ đủ','Model đánh giá có nguồn phản bác','Model còn chưa chắc chắn'])assert.match(output,new RegExp(key));
  assert.match(output,/không xếp hạng hoặc chọn giả thuyết thắng/);assert.match(output,/Chưa có kiểm người độc lập/);
  assert.match(output,/chưa chứng minh đúng/);assert.match(output,/chưa chạy code hoặc test/);assert.doesNotMatch(output,/\d+%/);
  const rows=app.all(app.target).filter(row=>row.tag==='tbody')[0].children;
  assert.equal(rows.length,5);assert.deepEqual(rows.map(row=>row.children[0].children[0].textContent),['H1','H2','H3','H4','H5']);
});

test('source inventory order and sorted HTTP object keys are accepted without reordering hypothesis citations',()=>{
  const app=boot(),value=sorted(fixture());assert.deepEqual(value.view.pairs[0].shared_support_ids,['E1','E2']);
  assert.deepEqual(value.view.hypotheses[0].support_ids,['E2','E1']);app.render(value);
  assert.match(app.text(app.target),/Original mechanism H1/);assert.doesNotMatch(app.text(app.target),/Chưa đọc được bản đối chiếu/);
  const details=app.all(app.target).find(row=>row.className==='hypothesis-review-details');
  const support=details.children[0].children.find(row=>row.className==='hypothesis-review-role');
  assert.deepEqual(app.all(support).filter(row=>row.tag==='button').map(row=>row.textContent),['E2','E1']);
});

test('pair selectors expose exact shared IDs and correctly reverse asymmetric citation roles',()=>{
  const app=boot();app.render();const selectors=app.all(app.target).filter(row=>row.tag==='select');
  assert.equal(selectors.length,2);assert.equal(selectors[0].value,'H1');assert.equal(selectors[1].value,'H2');
  const overlap=()=>app.all(app.target).find(row=>row.className==='hypothesis-review-overlap');
  assert.match(app.text(overlap()),/H1 phản bác · H2 hỗ trợ/);assert.match(app.text(overlap()),/C1/);assert.match(app.text(overlap()),/K1/);
  selectors[0].value='H2';selectors[0].onchange();assert.equal(selectors[1].value,'H1');
  const roles=app.all(overlap()).filter(row=>row.className==='hypothesis-review-role'),cross=roles.find(row=>app.text(row).includes('H2 hỗ trợ · H1 phản bác'));
  assert.deepEqual(app.all(cross).filter(row=>row.tag==='button').map(row=>row.textContent),['C1']);
  assert.match(app.text(overlap()),/không suy luận tương đương hay mâu thuẫn ngữ nghĩa/);
});

test('keyboard-accessible native controls retain pair focus across cached language changes',()=>{
  const app=boot(),value=fixture();app.render(value);const selectors=app.all(app.target).filter(row=>row.tag==='select');
  const labels=app.all(app.target).filter(row=>row.tag==='label');assert.equal(labels[0].htmlFor,selectors[0].id);
  const region=app.all(app.target).find(row=>row.className==='hypothesis-review-table-wrap');assert.equal(region.tabIndex,0);assert.equal(region.attributes.role,'region');
  selectors[1].value='H5';selectors[1].onchange();app.language('en');app.render(value);
  assert.deepEqual(app.all(app.target).filter(row=>row.tag==='select').map(row=>row.value),['H1','H5']);
  assert.match(app.text(app.target),/Compare hypotheses/);assert.match(app.text(app.target),/Saved proposed checks/);
  assert.match(app.text(app.target),/Original mechanism H5/);assert.match(app.text(app.target),/not establish semantic contradiction/);
});

test('citations and exports bind to the saved view and stale controls become inert',()=>{
  const app=boot(),exports=[];app.render(fixture(),{export:format=>exports.push(format)});
  const buttons=app.all(app.target).filter(row=>row.tag==='button'),citation=buttons.find(row=>row.textContent==='E2'),download=buttons.find(row=>row.textContent==='Xuất đối chiếu JSON');
  citation.onclick();download.onclick();assert.equal(app.dispatched.length,1);assert.equal(app.dispatched[0].detail.job_id,job('a'));
  assert.equal(app.dispatched[0].detail.evidence_id,'E2');assert.equal(citation.attributes['aria-label'],'Xem bằng chứng E2');assert.deepEqual(exports,['json']);
  app.events.get('issueintel:packchange')({detail:{job_id:job('a')}});citation.onclick();download.onclick();assert.equal(app.dispatched.length,1);assert.deepEqual(exports,['json']);
  app.render();const fresh=app.all(app.target).find(row=>row.tag==='button'&&row.textContent==='E2');app.render({view:null,report:null});fresh.onclick();assert.equal(app.dispatched.length,1);
});

test('malformed, cross-source and invented-role projections fail closed with explicit read-only retry',()=>{
  const mutations=[value=>value.view.schema='other',value=>value.view.source.job_id=job('b'),value=>value.view.source.report_sha256=hash('0'),
    value=>value.view.source.query_sha256=hash('0'),value=>value.view.source.pack_sha256=hash('0'),value=>value.view.source.snapshot_sha256=hash('0'),
    value=>value.view.source.commit_sha='0'.repeat(40),value=>value.expected.pack_sha256=hash('0'),value=>value.view.correctness='verified',
    value=>value.view.independent_human=true,value=>value.view.evaluation_performed=true,value=>value.view.model_calls=1,
    value=>value.view.hypotheses[0].support_ids=['missing'],value=>value.view.hypotheses[0].counter_ids=[],
    value=>value.view.hypotheses[0].statement='Invented replacement',value=>value.view.hypotheses[0].model_review.independent_human=true,
    value=>value.view.hypotheses[0].gap_codes=['model_review_uncertain'],value=>value.view.hypotheses[0].check_ids=[],
    value=>value.view.pairs[0].shared_support_ids.reverse(),value=>value.view.pairs[0].left_support_right_counter_ids=['E1'],
    value=>value.view.checks[0].execution_status='executed',value=>value.view.evidence[0].id='invented',
    value=>value.view.evidence[0].text='Replaced excerpt',value=>value.view.hypotheses[0]=null,value=>value.report.next_checks[0]=null,
    value=>value.view.source=null,value=>value.view.issue.repository='other/repository'];
  for(const mutate of mutations){const app=boot(),value=clone(fixture());mutate(value);let retries=0;app.render(value,{reload:()=>retries++});
    assert.match(app.text(app.target),/Chưa đọc được bản đối chiếu/);const buttons=app.all(app.target).filter(row=>row.tag==='button');
    assert.equal(buttons.length,1);assert.equal(buttons[0].textContent,'Tải lại bản đối chiếu');buttons[0].onclick();assert.equal(retries,1);}
});

test('source text and long statements remain literal and are fully available in progressive detail',()=>{
  const app=boot(),value=fixture(),statement='<img src=x onerror=alert(1)> '+('long/path/'.repeat(90));
  value.view.hypotheses[0].statement=value.report.hypotheses[0].statement=statement;
  value.view.hypotheses[0].model_review.reason=value.report.hypotheses[0].verification.model_reason='<script>critic</script>';
  value.view.checks[0].text=value.report.next_checks[0].text='<script>check</script>';app.render(value);
  const output=app.text(app.target);assert.ok(output.includes(statement));assert.match(output,/<script>critic<\/script>/);assert.match(output,/<script>check<\/script>/);
  assert.equal(app.all(app.target).filter(row=>['img','script','iframe'].includes(row.tag)).length,0);
  assert.ok(app.all(app.target).filter(row=>row.tag==='details').length>=3);
});

test('evidence-only reports present a truthful empty state and do not invent pairs or calls',()=>{
  const value=fixture();value.report.hypotheses=[];value.report.next_checks=[];
  Object.assign(value.view,{hypotheses:[],checks:[],pairs:[],evidence:[]});const app=boot();app.render(value);
  assert.match(app.text(app.target),/chưa sinh giả thuyết/);assert.equal(app.all(app.target).filter(row=>row.tag==='select').length,0);
  assert.equal(app.all(app.target).filter(row=>row.className==='citation').length,0);
});

test('a single saved hypothesis retains details and checks without an artificial comparison selector',()=>{
  const value=fixture();value.report.hypotheses=value.report.hypotheses.slice(2,3);value.report.next_checks=value.report.next_checks.filter(row=>row.id==='K3');
  value.view.hypotheses=value.view.hypotheses.slice(2,3);value.view.checks=clone(value.report.next_checks);value.view.pairs=[];
  value.view.evidence=value.view.evidence.filter(row=>row.id==='I1');const app=boot();app.render(value);
  assert.match(app.text(app.target),/Đọc giả thuyết và bước kiểm tra/);assert.equal(app.all(app.target).filter(row=>row.tag==='select').length,0);
  assert.match(app.text(app.target),/Inspect original input H3/);
});

function ioBoot({value=fixture(),responseHeader=hash('f'),responseBody=value.view,format='json',pending=false}={}){
  const downloads=[],messagesSeen=[],requests=[],urls=[],state={value};let resolveResponse;
  const response={ok:true,headers:{get:name=>name==='X-IssueIntel-Report-SHA256'?responseHeader:null},json:async()=>clone(responseBody),blob:async()=>({saved:'markdown'})};
  const context={window:{IssueIntelHypothesisReview:boot().renderer},currentJob:job('a'),viewGeneration:1,reportView:value.report,hypothesisReview:value.view,
    intake:{source_pack_sha256:value.expected.pack_sha256,query_sha256:value.expected.query_sha256},pack:{snapshot:{commit_sha:value.expected.commit_sha,snapshot_sha256:value.expected.snapshot_sha256}},
    token:'synthetic-session',t:key=>key,message:(...args)=>messagesSeen.push(args),Blob:class{constructor(parts,options){this.parts=parts;this.options=options;}},
    URL:{createObjectURL:body=>{urls.push(body);return 'blob:fixture';},revokeObjectURL(){}},setTimeout(){},node:()=>({click(){downloads.push(this);}}),
    fetch:async(url,options)=>{requests.push({url,options});if(pending)return new Promise(resolve=>{resolveResponse=()=>resolve(response);});return response;}};
  const source=appCode.slice(appCode.indexOf('function hypothesisReviewSource()'),appCode.indexOf("$('mode').onchange="));
  vm.runInNewContext(source,context);
  return {context,downloads,messagesSeen,requests,urls,read:()=>context.readHypothesisReview(job('a')),export:()=>context.exportHypothesisReview(format),resolve:()=>resolveResponse(),state};
}

test('JSON and Markdown exports check report response binding before creating a download',async()=>{
  for(const format of ['json','md']){const io=ioBoot({format});await io.export();assert.equal(io.downloads.length,1);
    assert.match(io.downloads[0].download,new RegExp('hypothesis-review\\.'+format+'$'));assert.equal(io.requests.length,1);
    assert.equal(io.requests[0].options.headers['X-IssueIntel-Token'],'synthetic-session');}
  for(const responseHeader of [null,hash('0')]){const io=ioBoot({responseHeader});await io.export();assert.equal(io.downloads.length,0);assert.equal(io.messagesSeen.length,1);}
  const value=fixture(),changed=clone(value.view);changed.sha256=hash('1');const io=ioBoot({value,responseBody:changed});await io.export();assert.equal(io.downloads.length,0);
});

test('switching source during export cancels download and does not show a stale error',async()=>{
  for(const format of ['json','md']){const io=ioBoot({format,pending:true}),operation=io.export();io.context.currentJob=job('b');io.context.viewGeneration++;
    io.resolve();await operation;assert.equal(io.downloads.length,0);assert.equal(io.urls.length,0);assert.equal(io.messagesSeen.length,0);}
});

test('initial and explicit-retry reads require the report hash response header',async()=>{
  const valid=ioBoot();assert.equal((await valid.read()).source.report_sha256,hash('f'));
  for(const responseHeader of [null,hash('0')]){const io=ioBoot({responseHeader});await assert.rejects(io.read(),/Không xuất được báo cáo/);}
  assert.match(appCode,/Promise\.allSettled\(\[[\s\S]*?readHypothesisReview\(id\)/);
  assert.match(appCode,/async function reloadHypothesisReview[\s\S]*?readHypothesisReview\(selected\)/);
});

test('retry ignores a response after the current job or report changes',async()=>{
  const io=ioBoot({pending:true});let renders=0;
  io.context.renderTab=()=>renders++;io.context.api=async()=>{throw Error('Cached sources must be reused');};
  const operation=io.context.reloadHypothesisReview();io.context.currentJob=job('b');io.context.viewGeneration++;io.resolve();await operation;
  assert.equal(renders,0);assert.equal(io.messagesSeen.length,0);
});

test('hypothesis view is loaded once per saved job and language rerender does not refetch it',()=>{
  const language=appCode.slice(appCode.indexOf("document.addEventListener('issueintel:languagechange'"));
  assert.doesNotMatch(language,/readHypothesisReview|reloadHypothesisReview|fetch\(/);
  assert.match(appCode,/IssueIntelHypothesisReview\?\.render\(content,hypothesisReview,currentJob,reportView,hypothesisReviewSource\(\)/);
  assert.match(fs.readFileSync(path.join(base,'index.html'),'utf8'),/src="\/hypothesis-review\.js"/);
});

test('all authored comparison labels are bilingual and small screens keep separate readable panels',()=>{
  for(const match of code.matchAll(/\bt\(['"]([^'"]+)['"]/g)){assert.ok(messages[match[1]]?.vi,match[1]);assert.ok(messages[match[1]]?.en,match[1]);}
  const style=fs.readFileSync(path.join(base,'style.css'),'utf8');assert.match(style,/@media\(max-width:640px\)\{\.hypothesis-review-selectors,\.hypothesis-review-details,\.hypothesis-review-overlap-grid\{grid-template-columns:1fr/);
  assert.match(style,/\.hypothesis-review-source\{[^}]*white-space:pre-wrap;overflow-wrap:anywhere/);assert.match(style,/\.hypothesis-review-table-wrap\{[^}]*overflow-x:auto/);
});

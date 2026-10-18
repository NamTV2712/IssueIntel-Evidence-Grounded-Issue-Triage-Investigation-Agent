const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const base=path.join(__dirname,'../src/issueintel/interfaces/static');
const code=fs.readFileSync(path.join(base,'check-journal.js'),'utf8'),appCode=fs.readFileSync(path.join(base,'app.js'),'utf8');
const messages=JSON.parse(fs.readFileSync(path.join(base,'locales.js'),'utf8').replace(/^window.IssueIntelMessages\s*=\s*/,'').replace(/;\s*$/,''));
const hash=c=>c.repeat(64),job='a'.repeat(24),clone=value=>JSON.parse(JSON.stringify(value));
const flags={provenance:'user_report_unverified',executed_by_issueintel:false,causal_verified:false,fix_verified:false,independent_human:false,evaluation_performed:false};
function fixture(){
  const source={job_id:job,run_id:'b'.repeat(24),pack_sha256:hash('b'),query_sha256:hash('c'),snapshot_sha256:hash('d'),commit_sha:'e'.repeat(40),
    pack_hash_kind:'serialized_pack_bytes',protocol:'benchmark_base',strict_issue_time_verified:false};
  const issue={case_id:'synthetic:journal',repository:'synthetic/example',issue_url:null};
  const checks=[{id:'check_1',text:'Compare the saved parser branches',hypothesis_ids:['h0','h2'],evidence_ids:['E1'],kind:'proposed_check',execution_status:'not_executed_by_issueintel'},
    {id:'check_2',text:'Inspect the caller contract',hypothesis_ids:['h1'],evidence_ids:['E2'],kind:'proposed_check',execution_status:'not_executed_by_issueintel'}];
  const report={schema:'investigation_report_v1',source,issue,next_checks:checks,sha256:hash('f')};
  const journal={schema:'check_journal_v1',source:{...source,report_sha256:report.sha256},issue:clone(issue),checks:clone(checks),revision:0,parent_sha256:null,events:[],...flags,sha256:hash('1')};
  return {journal,report,expected:source};
}
function observation(overrides={}){return {check_id:'check_1',outcome:'reported_difference',conditions:'Input=None',expected_result:'[]',observed_result:'TypeError',interpretation:'Needs investigation',tested_commit_sha:null,supersedes:null,...overrides};}
function add(value,obs=observation()){
  const j=value.journal,check=j.checks.find(row=>row.id===obs.check_id),revision=j.revision+1;
  const event={schema:'manual_check_observation_v1',source:clone(j.source),revision,parent_sha256:j.sha256,observation_id:'observation_'+revision,
    check_binding:{id:check.id,text_sha256:hash('2'),hypothesis_ids:clone(check.hypothesis_ids),evidence_ids:clone(check.evidence_ids)},
    observation:obs,recorded_at:'2026-10-18T08:00:00+00:00',...flags,sha256:hash('3')};
  j.revision=revision;j.parent_sha256=j.sha256;j.sha256=hash('4');j.events.push(event);return value;
}
function boot(){
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.textContent='';this.value='';this.disabled=false;}
    append(...values){this.children.push(...values);}prepend(...values){this.children.unshift(...values);}replaceChildren(...values){this.children=values;}
    setAttribute(name,value){this.attributes[name]=value;}set innerHTML(_){throw Error('Unsafe HTML');}
  }
  const events=new Map(),dispatched=[],language={value:'vi'};
  const document={createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>dispatched.push(event)};
  const context={window:{IssueIntelUI:{t:(key,values={})=>{assert.ok(messages[key]?.[language.value],'Missing translation '+key);return messages[key][language.value].replace(/\{(\w+)\}/g,(_,name)=>values[name]);}}},
    document,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},fetch(){throw Error('Render must not fetch');}};
  vm.runInNewContext(code,context);const renderer=context.window.IssueIntelCheckJournal,target=new Element('div');
  const all=item=>[item,...item.children.flatMap(all)],text=item=>item.textContent+' '+item.children.map(text).join(' ');
  let current=fixture(),options={available:true};
  const render=(value=current,extra=options)=>{current=value;options=extra;target.replaceChildren();renderer.render(target,value.journal,job,value.report,value.expected,extra);};
  const field=name=>all(target).find(row=>row.id==='journal-'+name),button=label=>all(target).find(row=>row.tag==='button'&&row.textContent===label);
  return {renderer,target,all,text,field,button,events,dispatched,language,render,rerender:()=>render(current,options)};
}

test('journal presents real report checks, unverified flags and tested commit stays blank',()=>{
  const app=boot();app.render();const output=app.text(app.target);
  assert.match(output,/không chạy code\/test/);assert.match(output,/không tạo nhãn benchmark/);
  assert.match(output,/h0 · h2/);assert.equal(app.field('tested_commit_sha').value,'');
  assert.equal(app.field('outcome').value,'not_run');assert.equal(app.field('observed_result').disabled,true);
  assert.equal(app.field('interpretation').required,true);assert.match(output,/Chưa có quan sát thủ công/);
  app.field('check_id').value='check_2';app.field('check_id').onchange();assert.match(app.text(app.target),/h1/);
});

test('reported checks require structured observations; not-run never sends a fake tested commit',async()=>{
  const app=boot(),saved=[];app.render(fixture(),{available:true,save:async value=>saved.push(value)});
  app.field('outcome').value='reported_difference';app.field('outcome').onchange();
  for(const [name,value] of Object.entries({conditions:'Input=None',expected_result:'[]',observed_result:'TypeError',tested_commit_sha:'9'.repeat(40)})){app.field(name).value=value;app.field(name).oninput();}
  assert.equal(app.field('conditions').required,true);assert.equal(app.field('tested_commit_sha').disabled,false);
  app.field('outcome').value='not_run';app.field('outcome').onchange();app.field('interpretation').value='Need an environment';app.field('interpretation').oninput();
  await app.all(app.target).find(row=>row.tag==='form').onsubmit({preventDefault(){}});
  assert.equal(saved.length,1);assert.equal(saved[0].observation.tested_commit_sha,null);assert.equal(saved[0].observation.observed_result,'');
});

test('drafts survive cached language rerender and a failed save without a new model call',async()=>{
  const app=boot(),value=fixture();app.render(value,{available:true,save:async()=>{throw Error('stale version');}});
  app.field('interpretation').value='Keep this draft';app.field('interpretation').oninput();
  await app.all(app.target).find(row=>row.tag==='form').onsubmit({preventDefault(){}});
  assert.match(app.text(app.target),/Chưa lưu được/);assert.equal(app.button('Lưu quan sát mới').disabled,false);
  app.language.value='en';app.rerender();assert.equal(app.field('interpretation').value,'Keep this draft');
  assert.match(app.text(app.target),/Hypothesis check journal/);assert.equal(app.field('tested_commit_sha').value,'');
});

test('correction appends against an active same-check record and original history remains visible',()=>{
  const value=add(fixture()),app=boot();app.render(value,{available:true,rerender:()=>app.rerender()});
  app.button('Đính chính quan sát').onclick();assert.match(app.text(app.target),/Đang đính chính observation_1/);
  assert.equal(app.field('check_id').disabled,true);assert.equal(app.field('observed_result').value,'TypeError');
  app.button('Hủy đính chính').onclick();assert.equal(app.field('check_id').disabled,false);
  add(value,observation({supersedes:'observation_1',observed_result:'Corrected result'}));app.render(value);
  assert.match(app.text(app.target),/Đã được đính chính/);assert.match(app.text(app.target),/TypeError/);assert.match(app.text(app.target),/Corrected result/);
  assert.equal(app.all(app.target).filter(row=>row.textContent==='Đính chính quan sát').length,1);
});

test('cross-source, malformed and promoted projections fail closed',()=>{
  const mutations=[v=>v.journal.source.job_id='b'.repeat(24),v=>v.journal.source.report_sha256=hash('0'),v=>v.journal.source.query_sha256=hash('0'),
    v=>v.journal.source.protocol='issue_time',v=>v.journal.source.strict_issue_time_verified=0,v=>v.journal.fix_verified=true,v=>v.journal.independent_human=true,
    v=>v.journal.revision=true,v=>v.journal.checks[0].text='Invented check',v=>v.journal.events[0].check_binding.evidence_ids=['different'],
    v=>v.journal.events[0].observation.tested_commit_sha='HEAD',v=>v.journal.events[0].executed_by_issueintel=true,
    v=>v.journal.events[0].observation.supersedes='observation_1',v=>v.journal.events[0].observation.outcome='verified',v=>v.journal.events[0]=null];
  for(const mutate of mutations){const value=add(fixture()),app=boot();mutate(value);app.render(value);
    assert.match(app.text(app.target),/Chưa đọc được nhật ký/);assert.equal(app.all(app.target).filter(row=>row.tag==='form').length,0);}
  const app=boot();app.render(fixture(),{available:false});assert.match(app.text(app.target),/ngoài benchmark/);
});

test('literal source/observation text cannot become markup; stale controls cannot write or open another run',()=>{
  const value=add(fixture(),observation({observed_result:'<img src=x onerror=alert(1)>\n'+('path/'.repeat(100))})),app=boot(),exports=[];
  app.render(value,{available:true,export:format=>exports.push(format)});
  assert.match(app.text(app.target),/<img src=x/);assert.equal(app.all(app.target).filter(row=>row.tag==='img').length,0);
  const citation=app.all(app.target).find(row=>row.tag==='button'&&row.textContent==='E1'),download=app.button('Xuất nhật ký JSON');
  citation.onclick();assert.equal(app.dispatched[0].detail.job_id,job);download.onclick();assert.deepEqual(exports,['json']);
  app.events.get('issueintel:packchange')();citation.onclick();download.onclick();assert.equal(app.dispatched.length,1);assert.deepEqual(exports,['json']);
});

test('all controls have labels, pending writes block duplicate submissions and completion clears the draft',async()=>{
  const app=boot(),value=fixture();let release,calls=0;
  app.render(value,{available:true,save:()=>{calls++;return new Promise(resolve=>{release=resolve;});}});
  for(const label of app.all(app.target).filter(row=>row.tag==='label'))assert.ok(app.all(app.target).some(row=>row.id===label.htmlFor));
  app.field('interpretation').value='Need input';app.field('interpretation').oninput();const form=app.all(app.target).find(row=>row.tag==='form');
  const promise=form.onsubmit({preventDefault(){}});await form.onsubmit({preventDefault(){}});assert.equal(calls,1);
  app.language.value='en';app.rerender();assert.equal(app.button('Saving observation…').disabled,true);
  app.renderer.completed(job,value.report);release();await promise;app.rerender();assert.equal(app.field('interpretation').value,'');
});

function ioBoot({format='json',headerReport=hash('f'),headerJournal=hash('1'),delayed=false}={}){
  const value=fixture(),downloads=[],errors=[];let release;
  const response={ok:true,headers:{get:name=>name==='X-IssueIntel-Report-SHA256'?headerReport:headerJournal},json:async()=>clone(value.journal),blob:async()=>({markdown:true})};
  const context={window:{IssueIntelCheckJournal:boot().renderer},currentJob:job,viewGeneration:1,reportView:value.report,journalData:value.journal,
    hypothesisReviewSource:()=>value.expected,token:'synthetic-session',t:key=>key,message:(...args)=>errors.push(args),
    node:()=>({click(){downloads.push(this);}}),URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},Blob:class{},setTimeout(){},
    fetch:async()=>delayed?new Promise(resolve=>{release=()=>resolve(response);}):response};
  const source=appCode.slice(appCode.indexOf('function renderPlanPreservingDrafts()'),appCode.indexOf("$('mode').onchange="));vm.runInNewContext(source,context);
  return {context,value,downloads,errors,export:()=>context.exportCheckJournal(format),read:()=>context.readCheckJournal(job),release:()=>release()};
}

test('JSON/Markdown downloads check both report and exact journal revision headers',async()=>{
  for(const format of ['json','md']){const io=ioBoot({format});await io.export();assert.equal(io.downloads.length,1);}
  for(const options of [{headerReport:null},{headerJournal:null},{headerJournal:hash('0')},{headerReport:hash('0')}]){
    const io=ioBoot(options);await io.export();assert.equal(io.downloads.length,0);assert.equal(io.errors.length,1);await assert.rejects(io.read());}
});

test('switching jobs while download is pending cannot deliver the old run',async()=>{
  for(const format of ['json','md']){const io=ioBoot({format,delayed:true}),promise=io.export();io.context.currentJob='9'.repeat(24);io.context.viewGeneration++;io.release();await promise;
    assert.equal(io.downloads.length,0);assert.equal(io.errors.length,0);}
});

test('saving and reloading preserve cached source identity, drafts and monotonic versions',async()=>{
  const io=ioBoot(),saved=add(clone(io.value)),request={expected_sha256:io.value.journal.sha256,observation:observation()};
  io.context.activeTab='plan';io.context.planDrafts=()=>[];io.context.restorePlanDrafts=()=>{};let renders=0;io.context.renderTab=()=>renders++;
  io.context.api=async()=>saved.journal;await io.context.saveCheckObservation(request);assert.equal(io.context.journalData.revision,1);assert.equal(renders,1);
  io.context.intake={check_journal_available:true,source_pack_sha256:io.value.expected.pack_sha256,query_sha256:io.value.expected.query_sha256};await io.context.reloadCheckJournal();assert.equal(io.context.journalData.revision,1);assert.equal(renders,1);
});

test('missing initial metadata is recoverable and is not presented as a benchmark denial',async()=>{
  const app=boot(),value=fixture();value.journal=null;
  app.render(value,{reload(){},available:undefined});assert.match(app.text(app.target),/Chưa đọc được nhật ký/);
  assert.ok(app.button('Tải lại nhật ký'));assert.doesNotMatch(app.text(app.target),/dành cho điều tra sản phẩm/);
  const io=ioBoot();io.context.reportView=null;io.context.intake=null;io.context.journalData=null;
  io.context.hypothesisReviewSource=()=>({...io.value.expected,pack_sha256:undefined,query_sha256:undefined});
  io.context.api=async route=>route.endsWith('/intake')?{source_pack_sha256:io.value.expected.pack_sha256,query_sha256:io.value.expected.query_sha256,check_journal_available:true}:io.value.report;
  let renders=0;io.context.renderTab=()=>renders++;io.context.planDrafts=()=>[];io.context.restorePlanDrafts=()=>{};
  await io.context.reloadCheckJournal();assert.equal(renders,1);assert.equal(io.context.journalData.revision,0);assert.equal(io.context.intake.check_journal_available,true);
});

test('a failed write re-enables a freshly rendered editor and stale failure never changes another job',async()=>{
  for(const stale of [false,true]){
    const io=ioBoot();let renders=0;io.context.activeTab='plan';io.context.renderTab=()=>renders++;io.context.planDrafts=()=>[];io.context.restorePlanDrafts=()=>{};
    io.context.api=async()=>{if(stale){io.context.currentJob='9'.repeat(24);io.context.viewGeneration++;}throw Error('conflicting revision');};
    await assert.rejects(io.context.saveCheckObservation({expected_sha256:io.value.journal.sha256,observation:observation()}));
    assert.equal(renders,stale?0:1);assert.equal(io.errors.length,stale?0:1);
  }
});

test('bilingual integration keeps legacy plan separate, never exports observations into inference automatically',()=>{
  assert.match(appCode,/available:intake\?\.check_journal_available/);assert.match(appCode,/checklist\.append\(form\)/);
  const language=appCode.slice(appCode.indexOf("document.addEventListener('issueintel:languagechange'"));assert.doesNotMatch(language,/readCheckJournal|fetch\(/);
  assert.match(fs.readFileSync(path.join(base,'index.html'),'utf8'),/src="\/check-journal\.js"/);
  const css=fs.readFileSync(path.join(base,'style.css'),'utf8');assert.match(css,/\.journal-source\{white-space:pre-wrap;overflow-wrap:anywhere/);
  for(const match of code.matchAll(/\bt\(['"]([^'"]+)['"]/g)){assert.ok(messages[match[1]]?.vi,match[1]);assert.ok(messages[match[1]]?.en,match[1]);}
});

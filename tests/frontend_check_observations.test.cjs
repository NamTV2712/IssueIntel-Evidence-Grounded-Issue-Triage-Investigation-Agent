const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const base=path.join(__dirname,'../src/issueintel/interfaces/static'),code=fs.readFileSync(path.join(base,'check-observations.js'),'utf8');
const messages=JSON.parse(fs.readFileSync(path.join(base,'locales.js'),'utf8').replace(/^window.IssueIntelMessages\s*=\s*/,'').replace(/;\s*$/,''));
const hash=c=>c.repeat(64),job=c=>c.repeat(24),clone=value=>JSON.parse(JSON.stringify(value));
const flags={provenance:'user_report_unverified',executed_by_issueintel:false,causal_verified:false,fix_verified:false,independent_human:false,evaluation_performed:false};
function fixture(){
  const report={schema:'investigation_report_v1',sha256:hash('e'),source:{job_id:job('a'),pack_sha256:hash('b'),query_sha256:hash('c'),snapshot_sha256:hash('d'),commit_sha:'1'.repeat(40),protocol:'benchmark_base',strict_issue_time_verified:false},issue:{repository:'synthetic/example',case_id:'synthetic:observations'}};
  const checks=[{id:'check_1',procedure:'Bypass the cache and compare.',assumptions:'Keep input and revision identical.',outcome_a:'The error remains.',outcome_b:'The error disappears.',effort:'low',evidence_ids:['E1'],predictions:[{hypothesis_id:'H1',outcome:'A',reason:'Independent parser branch.'},{hypothesis_id:'H2',outcome:'B',reason:'Cache is bypassed.'}]}];
  const plan={schema:'product_check_plan_v1',sha256:hash('9'),source:{source_job_id:job('a'),source_pack_sha256:hash('b'),report_sha256:hash('e'),query_sha256:hash('c'),snapshot_sha256:hash('d'),commit_sha:'1'.repeat(40),annotation_sha256:hash('6'),sha256:hash('5')},checks};
  const journal={schema:'supplemental_check_observation_journal_v1',source:{plan_job_id:job('f'),plan_sha256:hash('9'),source_job_id:job('a'),source_pack_sha256:hash('b'),report_sha256:hash('e'),query_sha256:hash('c'),snapshot_sha256:hash('d'),commit_sha:'1'.repeat(40),annotation_sha256:hash('6'),source_projection_sha256:hash('5')},checks:clone(checks),revision:0,parent_sha256:null,events:[],...flags,sha256:hash('1')};
  return {report,plan,journal,planId:job('f'),sourceId:job('a')};
}
const observation=extra=>({check_id:'check_1',outcome:'A',conditions:'Same input, cache disabled.',observed_result:'The error remains.',interpretation:'Reported observation only.',tested_commit_sha:null,supersedes:null,...extra});
function add(value,obs=observation()){
  const journal=value.journal,revision=journal.revision+1,event={schema:'manual_check_plan_observation_v1',source:clone(journal.source),revision,parent_sha256:journal.sha256,observation_id:'observation_'+revision,check_binding:{id:obs.check_id,sha256:hash('2')},observation:clone(obs),recorded_at:'2026-10-18T09:00:00+00:00',...flags,sha256:hash('3')};
  journal.revision=revision;journal.parent_sha256=journal.sha256;journal.sha256=hash(['a','b','c','d'][revision%4]);journal.events.push(event);return value;
}
function boot(value=fixture(),handler,download){
  const events=new Map(),calls=[],dispatched=[],downloads=[],language={value:'vi'};
  const all=item=>[item,...item.children.flatMap(all)],text=item=>item.textContent+' '+item.children.map(text).join(' ');
  class Element{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.value='';this.attributes={};this.disabled=false;this.checked=false;}append(...values){this.children.push(...values);}prepend(...values){this.children.unshift(...values);}replaceChildren(...values){this.children=values;}setAttribute(key,value){this.attributes[key]=value;}click(){downloads.push(this.download);}set innerHTML(_){throw Error('Unsafe HTML');}}
  const target=new Element('div'),run=new Element('button'),document={createElement:tag=>new Element(tag),getElementById:()=>run,querySelector:()=>({content:'synthetic-token'}),addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>dispatched.push(event)};
  const window={IssueIntelUI:{t:(key,values={})=>{assert.ok(messages[key]?.[language.value],'Missing translation '+key);return messages[key][language.value].replace(/\{(\w+)\}/g,(_,name)=>values[name]);}}};
  vm.runInNewContext(code,{window,document,api:async(route,body)=>{calls.push([route,body]);return handler?handler(route,body):clone(value.journal);},fetch:async(...args)=>{calls.push(args);if(!download)throw Error('Unexpected download');return download(...args);},Blob,URL:{createObjectURL:()=> 'blob:synthetic',revokeObjectURL(){}},setTimeout:()=>1,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}});
  const module=window.IssueIntelCheckObservations;
  const render=next=>{value=next||value;target.replaceChildren();return module.render(target,value.plan,value.planId,value.report,value.sourceId);};
  const field=name=>all(target).find(row=>row.id==='check-observation-'+name),button=caption=>{const found=all(target).find(row=>row.tag==='button'&&row.textContent===caption);assert.ok(found,caption);return found;};
  return {target,module,calls,dispatched,downloads,run,render,all,text,field,language,button,click:caption=>button(caption).onclick(),events,
    toggle:caption=>{const label=all(target).find(row=>row.tag==='label'&&text(row).includes(caption));assert.ok(label,caption);const input=all(label).find(row=>row.tag==='input');input.checked=!input.checked;return input.onchange();},
    submit:()=>all(target).find(row=>row.tag==='form').onsubmit({preventDefault(){}}),change:detail=>events.get('issueintel:packchange')({detail})};
}

test('bound journal loads once and renders exact plan controls without starting a model or child',async()=>{
  const app=boot();await app.render();await app.render();assert.equal(app.calls.length,1);assert.match(app.calls[0][0],/check-observations.json$/);assert.equal(app.calls[0][1],undefined);
  for(const value of ['Quan sát kiểm tra phân biệt','Bypass the cache','Keep input','The error remains','The error disappears','không chạy code/test'])assert.ok(app.text(app.target).includes(value),value);
  assert.equal(app.field('tested_commit_sha').value,'');assert.equal(app.field('outcome').value,'not_run');assert.equal(app.field('observed_result').disabled,true);assert.equal(app.button('Tạo phiên từ quan sát đã chọn').disabled,true);
});
test('not-run reason saves with exact optimistic version and does not fabricate execution',async()=>{
  const value=fixture(),app=boot(value,(route,body)=>{if(!body)return clone(value.journal);add(value,body.observation);return clone(value.journal);});await app.render();
  app.field('interpretation').value='Need a controlled environment';app.field('interpretation').oninput();await app.submit();
  const request=app.calls[1];assert.match(request[0],/\/check-observations$/);assert.equal(request[1].expected_sha256,hash('1'));assert.equal(request[1].observation.tested_commit_sha,null);assert.equal(request[1].observation.observed_result,'');
  assert.equal(app.calls.length,2);assert.match(app.text(app.target),/observation_1/);assert.equal(app.button('Tạo phiên từ quan sát đã chọn').disabled,true);
});
test('failed saves preserve draft across language/rerender and allow explicit reload',async()=>{
  const app=boot(fixture(),(route,body)=>{if(body)throw Error('Version changed');return fixture().journal;});await app.render();app.field('interpretation').value='Keep my reason';app.field('interpretation').oninput();await app.submit();
  assert.match(app.text(app.target),/Chưa lưu được/);app.language.value='en';app.events.get('issueintel:languagechange')();assert.equal(app.field('interpretation').value,'Keep my reason');assert.match(app.text(app.target),/Discriminating check observations/);
  assert.equal(app.calls.length,2);await app.click('Reload check observations');assert.equal(app.field('interpretation').value,'Keep my reason');assert.equal(app.calls.length,3);
});
test('corrections preserve old history, lock the same check and never select superseded or not-run records',async()=>{
  const value=add(add(fixture()),observation({outcome:'not_run',conditions:'',observed_result:'',interpretation:'Correction: never ran',supersedes:'observation_1'})),app=boot(value);await app.render();
  assert.match(app.text(app.target),/observation_1.*Đã được đính chính/);assert.equal(app.button('Tạo phiên từ quan sát đã chọn').disabled,true);
  await app.click('Đính chính quan sát');assert.equal(app.field('check_id').disabled,true);assert.match(app.text(app.target),/Đang đính chính observation_2/);await app.click('Hủy đính chính');assert.equal(app.field('check_id').disabled,false);
});
test('only explicitly selected active results form a child; Groq remains off by default and duplicate click is locked',async()=>{
  const value=add(add(fixture()),observation({outcome:'B',supersedes:'observation_1'})),app=boot(value,(route,body)=>body?{id:job('8')}:value.journal);await app.render();app.toggle('observation_2 · check_1');await app.click('Tạo phiên từ quan sát đã chọn');
  const request=app.calls[1];assert.equal(request[0],'/api/check-followups');assert.deepEqual(JSON.parse(JSON.stringify(request[1])),{plan_job_id:job('f'),plan_sha256:hash('9'),journal_sha256:value.journal.sha256,observation_ids:['observation_2'],provider:'none',depth:'quick'});
  assert.equal(app.dispatched[0].type,'issueintel:checkfollowupcreated');assert.equal(app.dispatched[0].detail.job_id,job('8'));await app.click('Tạo phiên từ quan sát đã chọn');assert.equal(app.calls.length,2);
});
test('Groq is explicit, cleared on language/source changes, and active background run prevents submission',async()=>{
  const value=add(fixture()),app=boot(value,(route,body)=>body?{id:job('8')}:value.journal);await app.render();app.toggle('observation_1 · check_1');app.toggle('Tôi đồng ý gửi nguồn');app.language.value='en';app.events.get('issueintel:languagechange')();
  app.run.disabled=true;await app.click('Create a run from selected observations');assert.equal(app.calls.length,1);app.run.disabled=false;await app.click('Create a run from selected observations');assert.equal(app.calls[1][1].provider,'none');
  const paid=boot(value,(route,body)=>body?{id:job('8')}:value.journal);await paid.render();paid.toggle('observation_1 · check_1');paid.toggle('Tôi đồng ý gửi nguồn');await paid.click('Tạo phiên từ quan sát đã chọn');assert.equal(paid.calls[1][1].provider,'groq');
});
test('source changes discard late journal responses and detached form/citation actions',async()=>{
  let complete;const app=boot(fixture(),()=>new Promise(resolve=>complete=resolve)),promise=app.render();app.change({job_id:job('7'),intake:{source_pack_sha256:hash('4')}});complete(fixture().journal);await promise;assert.equal(app.field('conditions'),undefined);
  const ready=boot();await ready.render();const citation=ready.all(ready.target).find(row=>row.tag==='button'&&row.textContent==='E1'),form=ready.all(ready.target).find(row=>row.tag==='form');ready.module.cancel();citation.onclick();await form.onsubmit({preventDefault(){}});assert.equal(ready.calls.length,1);assert.equal(ready.dispatched.length,0);
});
test('validation rejects source/check/outcome/truth tampering and malformed correction history',async()=>{
  for(const mutate of [v=>v.journal.source.plan_sha256=hash('0'),v=>v.journal.source.source_projection_sha256=hash('0'),v=>v.journal.checks[0].procedure='Changed',v=>v.journal.causal_verified=true,v=>v.journal.events[0].check_binding.id='missing',v=>v.journal.events[0].observation.outcome='verified',v=>v.journal.events[0].observation.supersedes='observation_1']){
    const value=add(fixture());mutate(value);const app=boot(value);await app.render();assert.match(app.text(app.target),/Chưa đọc được quan sát/);assert.equal(app.field('conditions'),undefined);
  }
});
test('export verifies report, plan and observation hash headers; stale exports never download',async()=>{
  const value=add(fixture()),headers={'X-IssueIntel-Report-SHA256':value.report.sha256,'X-IssueIntel-Check-Plan-SHA256':value.plan.sha256,'X-IssueIntel-Check-Observations-SHA256':value.journal.sha256};
  const app=boot(value,null,()=>({ok:true,headers:{get:name=>headers[name]},json:async()=>clone(value.journal)}));await app.render();await app.click('Xuất quan sát kiểm tra JSON');assert.equal(app.downloads.length,1);
  headers['X-IssueIntel-Check-Observations-SHA256']=hash('0');await app.click('Xuất quan sát kiểm tra JSON');assert.equal(app.downloads.length,1);assert.match(app.text(app.target),/Chưa xuất được quan sát/);
});
test('untrusted observation and plan text stay inert; invalid result form causes no request',async()=>{
  const value=add(fixture(),observation({observed_result:'<img src=x onerror=alert(1)>'})),app=boot(value);await app.render();assert.match(app.text(app.target),/<img src=x/);
  app.field('outcome').value='A';app.field('outcome').onchange();await app.submit();assert.equal(app.calls.length,1);assert.match(app.text(app.target),/Điền điều kiện/);
});
test('follow-up selection enforces three active observations while allowing deselection',async()=>{
  const value=add(add(add(add(fixture())))),app=boot(value);await app.render();
  for(const id of [1,2,3])app.toggle('observation_'+id+' · check_1');
  const checkbox=id=>app.all(app.target).find(row=>row.id==='check-observation-select-observation_'+id);
  assert.equal(checkbox(4).disabled,true);assert.equal(checkbox(1).disabled,false);assert.match(app.text(app.target),/tối đa 3 quan sát/);
  app.toggle('observation_4 · check_1');app.toggle('observation_1 · check_1');assert.equal(checkbox(4).disabled,false);assert.equal(checkbox(4).checked,false);
});
test('a child created after detachment can be opened later without resubmitting',async()=>{
  const value=add(fixture());let finish;const app=boot(value,(route,body)=>body?new Promise(resolve=>finish=resolve):value.journal);await app.render();app.toggle('observation_1 · check_1');
  const pending=app.click('Tạo phiên từ quan sát đã chọn');app.module.cancel();finish({id:job('8')});await pending;assert.equal(app.dispatched.length,0);
  await app.render();await app.click('Mở phiên đã tạo');assert.equal(app.dispatched[0].detail.job_id,job('8'));assert.equal(app.calls.length,2);
});

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const dir=path.join(__dirname,'../src/issueintel/interfaces/static'),code=fs.readFileSync(path.join(dir,'claim-review.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(dir,'locales.js'),'utf8'),locale);const messages=locale.window.IssueIntelMessages;
const sha=c=>c.repeat(64),job=c=>c.repeat(24),selected=job('a'),reviewJob=job('b'),clone=v=>JSON.parse(JSON.stringify(v));
const report={sha256:sha('1'),source:{job_id:selected,pack_sha256:sha('2'),query_sha256:sha('3'),snapshot_sha256:sha('4'),commit_sha:'5'.repeat(40)},issue:{repository:'synthetic/repo'},
 hypotheses:[{id:'h0',statement:'Candidate mechanism 0'},{id:'h1',statement:'Candidate mechanism 1'}]};
const pack={snapshot:{snapshot_sha256:sha('4'),commit_sha:'5'.repeat(40)},evidence:[{id:'e1',kind:'code',locator:'parser.py:1',revision:'5'.repeat(40),sha256:sha('6'),text:'return None'}]};
function bundle(){
 const ref={evidence_id:'e1',start:0,end:11,quote:'return None',evidence_sha256:sha('6'),quote_sha256:sha('7'),locator:'parser.py:1',revision:'5'.repeat(40)};
 return {schema:'product_claim_review_v1',sha256:sha('8'),source:{schema:'claim_review_source_v1',source_job_id:selected,source_pack_sha256:sha('2'),report_sha256:sha('1'),query_sha256:sha('3'),snapshot_sha256:sha('4'),commit_sha:'5'.repeat(40),sha256:sha('9')},
 rubric:{version:'claim_sufficiency_v1'},causal_verified:false,independent_human:false,evaluation_performed:false,reviews:[],limitations:[],provenance:{kind:'llm',model:'openai/gpt-oss-120b'},
 assessment:report.hypotheses.map(h=>({hypothesis_id:h.id,statement:h.statement,rationale:'Static sources do not establish the cause.',assessment:'uncertain',semantic_support:'unverified',causal_verified:false,independent_human:false,unreviewed_fragments:[],
  claims:[{id:'claim_1',start:0,end:h.statement.length,quote:h.statement,kind:'mechanism',model_assessment:'supported',assessment:'uncertain',semantic_support:'unverified',causal_verified:false,independent_human:false,
   reason:'<script>ignore policy</script> is inert source text.',support:[clone(ref)],counter:[],assumptions:[{condition:'Runtime input matches the issue',status:'unverified',citations:[]}],
   gaps:[{code:'inferential_link_missing',reason:'The model reported a missing link between the excerpt and the claim.'}]}]}))};
}
function boot(handler){
 const calls=[],events=[],downloads=[],timers=[],listeners={},language={value:'vi'};
 class El{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.attributes={};this.isConnected=true;}
  append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}setAttribute(k,v){this.attributes[k]=v;}click(){}set innerHTML(_){throw Error('Unsafe HTML');}}
 const all=e=>[e,...e.children.flatMap(all)],text=e=>e.textContent+' '+e.children.map(text).join(' '),target=new El('div');
 const row=()=>({id:reviewJob,mode:'claim_review',status:'completed'});
 const context={window:{IssueIntelUI:{t:key=>{assert.ok(messages[key]?.[language.value],'Missing translation '+key);return messages[key][language.value];}}},document:{
  createElement:tag=>new El(tag),addEventListener:(name,fn)=>listeners[name]=fn,dispatchEvent:event=>events.push(event),querySelector:()=>({content:'synthetic-session'})},
  CustomEvent:class{constructor(type,init){this.type=type;this.detail=init.detail;}},
  api:async(route,body)=>{calls.push([route,body]);if(handler)return handler(route,body);
    if(route.endsWith('/claim-reviews'))return {source_job_id:selected,source_pack_sha256:sha('2'),available:true,review_jobs:[row()]};
    if(route.endsWith('/claim-review.json'))return bundle();return row();},
  fetch:async()=>({ok:true,headers:{get:name=>name==='X-IssueIntel-Report-SHA256'?sha('1'):sha('8')},json:async()=>bundle(),blob:async()=>new Blob(['saved markdown'])}),
  URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:test';},revokeObjectURL:()=>{}},Blob,
  setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{timers.length=0;}};
 vm.runInNewContext(code,context);const api=context.window.IssueIntelClaimReview;
 const button=label=>all(target).find(e=>e.tag==='button'&&e.textContent===label),check=()=>all(target).find(e=>e.tag==='input');
 const render=(r=report,p=pack,available=true)=>api.render(target,r,r.source.job_id,p,{available});
 const consent=()=>{check().checked=true;check().onchange();};
 const saved=async()=>{await button('Mở các bản kiểm nhận định đã lưu').onclick();await button('Mở bản kiểm nhận định').onclick();};
 return {target,all,text,calls,events,downloads,timers,listeners,language,context,api,render,button,check,consent,saved};
}
test('rendering or opening history cannot start a model; explicit consent is required',async()=>{
 const a=boot();a.render();assert.equal(a.calls.length,0);assert.equal(a.button('Kiểm nhận định bằng Groq').disabled,true);
 await a.button('Kiểm nhận định bằng Groq').onclick();assert.equal(a.calls.length,0);
 await a.button('Mở các bản kiểm nhận định đã lưu').onclick();assert.equal(a.calls.length,1);assert.equal(a.calls[0][1],undefined);
});
test('saved review shows exact quotes, separate model/gated result, gaps and unverified truth',async()=>{
 const a=boot();a.render();await a.saved();const text=a.text(a.target);
 assert.match(text,/Sau cổng mức đủ bằng chứng/);assert.match(text,/return None/);assert.match(text,/inferential_link_missing/);assert.match(text,/thiếu mắt xích/);
 assert.match(text,/<script>ignore policy<\/script>/);assert.equal(a.calls.filter(([,body])=>body).length,0);
 a.button('Mở bằng chứng nguồn').onclick();assert.equal(a.events[0].detail.job_id,selected);assert.equal(a.events[0].detail.evidence_id,'e1');
});
test('explicit generation binds the viewed pack and duplicate click cannot create two posts',async()=>{
 let finish;const a=boot((route,body)=>{if(body)return new Promise(resolve=>finish=resolve);if(route.endsWith('/claim-review.json'))return bundle();return {id:reviewJob,mode:'claim_review',status:'completed'};});
 a.render();a.consent();const button=a.button('Kiểm nhận định bằng Groq'),pending=button.onclick();await button.onclick();assert.equal(a.calls.length,1);
 assert.equal(a.calls[0][1].confirmation,'generate_claim_review_with_api_cost');assert.equal(a.calls[0][1].source_pack_sha256,report.source.pack_sha256);
 finish({id:reviewJob,mode:'claim_review',status:'completed'});await pending;assert.equal(a.calls.filter(([,body])=>body).length,1);
});
test('benchmark availability, mismatched snapshots and source or truth changes cannot be reviewed',async()=>{
 const a=boot();a.render(report,pack,false);assert.equal(a.button('Kiểm nhận định bằng Groq').disabled,true);
 const wrong=clone(pack);wrong.snapshot.commit_sha='9'.repeat(40);const b=boot();b.render(report,wrong);assert.equal(b.target.children.length,0);
 for(const change of [v=>v.source.source_pack_sha256=sha('a'),v=>v.source.report_sha256=sha('a'),v=>v.causal_verified=true,v=>v.assessment[0].claims[0].quote='invented',v=>v.assessment[0].claims[0].support[0].start=2]){
  const value=bundle();change(value);const a=boot(route=>route.endsWith('/claim-reviews')?{source_job_id:selected,source_pack_sha256:sha('2'),available:true,review_jobs:[{id:reviewJob,mode:'claim_review',status:'completed'}]}:route.endsWith('/claim-review.json')?value:{id:reviewJob,mode:'claim_review',status:'completed'});
  a.render();await a.saved();assert.match(a.text(a.target),/Chưa đọc được bản kiểm/);assert.equal(a.button('Xuất kiểm nhận định JSON'),undefined);
 }
});
test('navigation during an outstanding saved read cannot repaint another investigation',async()=>{
 let finish;const a=boot(()=>new Promise(resolve=>finish=resolve));a.render();const pending=a.button('Mở các bản kiểm nhận định đã lưu').onclick();a.api.cancel();a.target.replaceChildren();finish({source_job_id:selected,source_pack_sha256:sha('2'),available:true,review_jobs:[]});await pending;assert.equal(a.target.children.length,0);
});
test('reattaching while a job read is pending keeps one poll and completes on the new view',async()=>{
 let finish,reads=0;const a=boot((route,body)=>{
  if(body)return {id:reviewJob,mode:'claim_review',status:'queued'};
  if(route.endsWith('/claim-review.json'))return bundle();
  if(++reads===1)return new Promise(resolve=>finish=resolve);
  return {id:reviewJob,mode:'claim_review',status:'completed'};
 });
 a.render();a.consent();const pending=a.button('Kiểm nhận định bằng Groq').onclick();for(let i=0;i<6;i++)await Promise.resolve();a.render();
 assert.equal(reads,1);finish({id:reviewJob,mode:'claim_review',status:'running'});await pending;
 assert.equal(a.timers.length,1);await a.timers.shift()();assert.equal(reads,2);assert.ok(a.button('Xuất kiểm nhận định JSON'));
});
test('language change revokes unsent consent without a paid call and translates gaps',async()=>{
 const a=boot();a.render();a.consent();a.language.value='en';a.listeners['issueintel:languagechange']();assert.equal(a.check().checked,false);assert.equal(a.calls.length,0);
 a.language.value='vi';a.listeners['issueintel:languagechange']();await a.saved();a.language.value='en';a.listeners['issueintel:languagechange']();
 assert.match(a.text(a.target),/After sufficiency gates/);assert.match(a.text(a.target),/missing link between/);
});
test('read failure never automatically posts a replacement paid review',async()=>{
 const a=boot(()=>{throw Error('synthetic read failure');});a.render();await a.button('Mở các bản kiểm nhận định đã lưu').onclick();assert.equal(a.calls.length,1);assert.equal(a.calls[0][1],undefined);assert.match(a.text(a.target),/Không có lời gọi model mới/);
});
test('export checks source and review headers and suppresses downloads after navigation',async()=>{
 const a=boot();a.render();await a.saved();await a.button('Xuất kiểm nhận định JSON').onclick();assert.equal(a.downloads.length,1);
 a.context.fetch=async()=>({ok:true,headers:{get:()=>sha('a')}});await a.button('Xuất kiểm nhận định Markdown').onclick();assert.equal(a.downloads.length,1);
 let finish;a.context.fetch=()=>new Promise(resolve=>finish=resolve);const pending=a.button('Xuất kiểm nhận định Markdown').onclick();a.api.cancel();finish({ok:true,headers:{get:name=>name==='X-IssueIntel-Report-SHA256'?sha('1'):sha('8')},blob:async()=>new Blob(['saved'])});await pending;assert.equal(a.downloads.length,1);
});

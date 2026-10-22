const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const dir=path.join(__dirname,'../src/issueintel/interfaces/static'),code=fs.readFileSync(path.join(dir,'issue-relations.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(dir,'locales.js'),'utf8'),locale);const messages=locale.window.IssueIntelMessages;
const hash=c=>c.repeat(64),job=c=>c.repeat(24),sourceId=job('a'),candidateId=job('b'),relationId=job('c'),clone=x=>JSON.parse(JSON.stringify(x));
const pack={case:{case_id:'local:left',problem_statement:'Parser failure'},snapshot:{commit_sha:'1'.repeat(40),snapshot_sha256:hash('2')}};
const report={source:{job_id:sourceId,pack_sha256:hash('3'),...pack.snapshot},hypotheses:[]};
function source(){
 const side=(which,id,checksum)=>({schema:'issue_relation_side_v1',kind:'investigation',repository:'synthetic/repo',issue_key:'local:'+which,
  reference:{kind:'investigation',job_id:id,pack_sha256:checksum},commit_sha:which==='left'?pack.snapshot.commit_sha:'4'.repeat(40),snapshot_sha256:which==='left'?pack.snapshot.snapshot_sha256:hash('5'),
  issue_source_id:'issue',sources:[{id:'issue',kind:'issue',text:which==='left'?'🧪 v1 parser throws ValueError on blank input.':'🧪 v2 parser throws ValueError on missing input.',sha256:hash('6'),locator:'issue:'+which,revision:null},
   {id:'code',kind:'code',text:which==='left'?'return parse(input)':'return parse(value)',sha256:hash(which==='left'?'7':'8'),locator:'parser.py:1',revision:which==='left'?pack.snapshot.commit_sha:'4'.repeat(40)}],sha256:hash(which==='left'?'9':'a')});
 return {schema:'issue_relation_source_v1',left:side('left',sourceId,report.source.pack_sha256),right:side('right',candidateId,hash('b')),sha256:hash('c')};
}
const candidate=()=>({kind:'investigation',job_id:candidateId,pack_sha256:hash('b')});
function preview(){return {schema:'issue_relation_preview_v1',source_job_id:sourceId,source_pack_sha256:report.source.pack_sha256,candidate:candidate(),comparison_source_sha256:source().sha256,policy_sha256:hash('d'),
 source:source(),available:true,max_provider_calls:2,model_calls:0,confirmation:'compare_issue_pair_with_api_cost'};}
function bundle(){
 const s=source(),ref=side=>({side,source_id:'code',start:7,end:side==='left'?19:19,quote:side==='left'?'parse(input)':'parse(value)'}),refs=['left','right'].map(ref);
 const comparison={classification:'possible_duplicate',reason:'The cited static parser mechanism is similar; affected versions still need checking.',
  dimensions:['symptoms','mechanism','version','differences'].map((name,i)=>({name,judgment:['similar','similar','unknown','non_material'][i],explanation:'Source statements <script> remain inert text.',support:clone(refs),counter:[],contextual_fit:'demonstrated',inferential_link:'demonstrated',counter_resolution:'not_supplied'})),
  mechanism_basis:{left:'code',right:'code'},versions:{left:{status:'unknown',value:null,citations:[]},right:{status:'unknown',value:null,citations:[]}},assumptions:[],unknowns:['Affected runtime versions were not verified.']};
 const assessment=clone(comparison);for(const d of assessment.dimensions)d.support=d.support.map(r=>({...r,source_sha256:s[r.side].sources[1].sha256,quote_sha256:hash('e'),locator:s[r.side].sources[1].locator,revision:s[r.side].sources[1].revision,kind:'code'}));
 Object.assign(assessment,{model_classification:comparison.classification,semantic_relation:'unverified',duplicate_verified:false,causal_verified:false,independent_human:false,
  gaps:[{code:'left_affected_version_unknown',reason:'The affected version is unknown; snapshot commit alone cannot resolve it.'}]});
 return {schema:'product_issue_relation_v1',source:s,comparison,assessment,rubric:{version:'issue_relation_sufficiency_v1'},provenance:{provider:'groq',model:'openai/gpt-oss-120b'},limitations:[],
  duplicate_verified:false,causal_verified:false,independent_human:false,evaluation_performed:false,sha256:hash('f')};
}
function boot(handler){
 const calls=[],timers=[],listeners={},downloads=[],language={value:'vi'},focus={node:null};
 class El{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.attributes={};this.isConnected=true;}
  append(...items){this.children.push(...items);}replaceChildren(...items){const contains=e=>e===focus.node||e.children.some(contains);if(this.children.some(contains))focus.node=null;this.children=items;}setAttribute(k,v){this.attributes[k]=v;}focus(){this.focused=true;focus.node=this;}click(){}set innerHTML(_){throw Error('Unsafe HTML');}}
 const all=e=>[e,...e.children.flatMap(all)],text=e=>e.textContent+' '+e.children.map(text).join(' '),target=new El('div');
 const row=(status='completed')=>({id:relationId,mode:'issue_relation',provider:'groq',depth:'relation',status});
 const history=()=>({schema:'issue_relation_history_v1',source_job_id:sourceId,source_pack_sha256:report.source.pack_sha256,available:true,reason:null,relation_jobs:[row()]});
 const candidates=()=>({schema:'issue_relation_candidates_v1',source_job_id:sourceId,source_pack_sha256:report.source.pack_sha256,model_calls:0,warnings:[],candidates:[{selector:candidate(),kind:'investigation',repository:'synthetic/repo',issue_key:'local:right',title:'Parser candidate',relationship:'candidate_unverified'}]});
 const defaultApi=(route,body)=>route==='/api/issue-relation-candidates'?candidates():route==='/api/issue-relations/preview'?preview():route.endsWith('/issue-relations')&&body===undefined?history():route.endsWith('/issue-relation.json')?bundle():row();
 const context={window:{IssueIntelUI:{t:(key,values={})=>{assert.ok(messages[key]?.[language.value],'Missing translation '+key);return messages[key][language.value].replace(/\{(\w+)\}/g,(_,k)=>values[k]);}}},
  document:{createElement:tag=>new El(tag),addEventListener:(event,fn)=>listeners[event]=fn,querySelector:()=>({content:'synthetic-session'})},
  api:async(route,body)=>{calls.push([route,body]);return handler?handler(route,body,defaultApi):defaultApi(route,body);},
  fetch:async()=>({ok:true,headers:{get:name=>name==='X-IssueIntel-Source-Pack-SHA256'?report.source.pack_sha256:bundle().sha256},json:async()=>bundle(),blob:async()=>new Blob(['saved comparison'])}),Blob,
  URL:{createObjectURL:value=>{downloads.push(value);return 'blob:synthetic';},revokeObjectURL(){}},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>timers.splice(0)};
 vm.runInNewContext(code,context);const renderer=context.window.IssueIntelIssueRelations;
 const button=label=>all(target).find(e=>e.tag==='button'&&e.textContent===label),checkbox=()=>all(target).find(e=>e.tag==='input'&&e.type==='checkbox');
 const render=(id=sourceId,available=true,r=report,p=pack)=>renderer.render(target,r,id,p,{available});
 const choose=async()=>{await button('Tìm ứng viên để đối chiếu').onclick();await button('Xem cặp nguồn').onclick();};
 const consent=()=>{checkbox().checked=true;checkbox().onchange();};
 const saved=async()=>{await button('Mở các bản đối chiếu đã lưu').onclick();await button('Mở bản đối chiếu').onclick();};
 return {target,all,text,calls,timers,listeners,downloads,language,focus,context,renderer,render,button,checkbox,choose,consent,saved,row,history,candidates};
}

test('offline product without hypotheses can preview both sides but generation needs exact consent',async()=>{
 const a=boot();a.render();assert.equal(a.calls.length,0);await a.choose();assert.equal(a.calls.length,2);assert.ok(a.text(a.target).includes('local:left'));assert.ok(a.text(a.target).includes('local:right'));
 const submit=a.button('Đối chiếu cặp issue bằng Groq');assert.equal(submit.disabled,true);await submit.onclick();assert.equal(a.calls.length,2);assert.equal(a.checkbox().checked,false);
 a.consent();await a.button('Đối chiếu cặp issue bằng Groq').onclick();const request=a.calls.find(([r])=>r==='/api/issue-relations')[1];
 assert.equal(request.source_job_id,sourceId);assert.equal(request.source_pack_sha256,report.source.pack_sha256);assert.equal(request.comparison_source_sha256,source().sha256);assert.equal(request.policy_sha256,preview().policy_sha256);
 assert.equal(request.confirmation,'compare_issue_pair_with_api_cost');assert.ok(a.button('Xuất đối chiếu issue JSON'));
});
test('missing or mismatched report metadata has an explicit read-only error state',()=>{
 const a=boot();a.render(sourceId,true,null);assert.match(a.text(a.target),/metadata nguồn khớp/);assert.equal(a.button('Tìm ứng viên để đối chiếu'),undefined);assert.equal(a.calls.length,0);
 const wrong=clone(report);wrong.source.snapshot_sha256=hash('0');a.render(sourceId,true,wrong);assert.match(a.text(a.target),/không có lời gọi model mới/);assert.equal(a.calls.length,0);
});
test('consent keeps keyboard focus in the selected pair and updates the next action without a model call',async()=>{
 const a=boot();a.render();await a.choose();a.checkbox().focus();a.checkbox().checked=true;a.checkbox().onchange();
 assert.equal(a.focus.node,a.checkbox());assert.equal(a.button('Đối chiếu cặp issue bằng Groq').disabled,false);
 a.checkbox().checked=false;a.checkbox().onchange();assert.equal(a.focus.node,a.checkbox());assert.equal(a.button('Đối chiếu cặp issue bằng Groq').disabled,true);assert.equal(a.calls.length,2);
});
test('rendered result keeps version unknowns beside possible duplicate and never confirms it',async()=>{
 const a=boot();a.render();await a.saved();const output=a.text(a.target);for(const value of ['Ứng viên có thể trùng','chưa xác nhận duplicate hoặc nguyên nhân','left_affected_version_unknown','Chưa biết','Triệu chứng','Cơ chế','Phiên bản','Điểm khác biệt','Issue đang xem','Issue ứng viên'])assert.ok(output.includes(value),value);
 assert.match(output,/<script> remain inert text/);assert.equal(a.calls.filter(([r])=>r==='/api/issue-relations').length,0);
});
test('synced issue candidates retain receipt selection and visibly lack a code snapshot',async()=>{
 const selection={kind:'synced_issue',key:'issue:12',record_sha256:hash('a'),receipt_sha256:hash('b')},right={...source().right,kind:'synced_issue',reference:selection,commit_sha:null,snapshot_sha256:null};right.sources=[right.sources[0]];
 const paired=()=>({...source(),right:clone(right)}),p={...preview(),candidate:selection,source:paired()};
 const result=()=>{const value=bundle();value.source=paired();value.assessment.classification='related';value.assessment.mechanism_basis.right='reported_mechanism';value.comparison.mechanism_basis.right='reported_mechanism';
  for(const row of value.comparison.dimensions)row.support[1]={side:'right',source_id:'issue',start:0,end:2,quote:'🧪 '};
  for(const row of value.assessment.dimensions)row.support[1]={...value.comparison.dimensions[0].support[1],source_sha256:right.sources[0].sha256,quote_sha256:hash('e'),locator:right.sources[0].locator,revision:null,kind:'issue'};return value;};
 const a=boot((route,body,fallback)=>{if(route==='/api/issue-relation-candidates'){const value=fallback(route,body);value.candidates[0]={...value.candidates[0],kind:'synced_issue',selector:selection};return value;}if(route.endsWith('/preview'))return p;if(route.endsWith('/issue-relation.json'))return result();return fallback(route,body);});
 a.render();await a.choose();assert.match(a.text(a.target),/Không có snapshot code ở nguồn này/);assert.equal(a.checkbox().checked,false);a.consent();await a.button('Đối chiếu cặp issue bằng Groq').onclick();assert.ok(a.button('Xuất đối chiếu issue JSON'));assert.match(a.text(a.target),/Cơ chế được nguồn mô tả/);assert.equal(a.calls.find(([r])=>r==='/api/issue-relations')[1].candidate.key,'issue:12');
});
test('side-scoped Unicode citations, source identities and truth flags reject confused saved output',()=>{
 const a=boot();a.render();const b={id:sourceId,pack,packSha:report.source.pack_sha256};assert.equal(a.renderer.valid(bundle(),b),true);
 for(const mutate of [v=>v.source.left.reference.pack_sha256=hash('0'),v=>v.source.right.repository='other/repo',v=>v.source.right.issue_key=v.source.left.issue_key,
  v=>v.assessment.dimensions[0].support[0].side='right',v=>v.assessment.dimensions[0].support[0].start=8,v=>v.assessment.dimensions[0].support[0].quote='<img>',v=>v.duplicate_verified=true,v=>v.assessment.semantic_relation='verified']){
  const value=bundle();mutate(value);assert.equal(a.renderer.valid(value,b),false);
 }
 const value=bundle(),text=value.source.left.sources[0].text,chars=Array.from(text);value.assessment.dimensions[0].support[0]={side:'left',source_id:'issue',start:0,end:2,quote:chars.slice(0,2).join(''),source_sha256:value.source.left.sources[0].sha256,quote_sha256:hash('e'),locator:'issue:left',revision:null,kind:'issue'};
 assert.equal(a.renderer.valid(value,b),true);value.assessment.dimensions[0].support[0].quote=text.slice(0,2);assert.equal(a.renderer.valid(value,b),false);
});
test('search and preview are zero-model and source or policy changes cannot be submitted',async()=>{
 for(const mutate of [v=>v.source_job_id=job('f'),v=>v.source_pack_sha256=hash('a'),v=>v.policy_sha256='bad',v=>v.comparison_source_sha256=hash('a'),v=>v.source.left.commit_sha='9'.repeat(40)]){
  const a=boot((route,body,fallback)=>{const value=fallback(route,body);if(route.endsWith('/preview'))mutate(value);return value;});a.render();await a.choose();assert.equal(a.button('Đối chiếu cặp issue bằng Groq'),undefined);assert.equal(a.calls.length,2);
 }
 const a=boot();a.render(sourceId,false);assert.equal(a.calls.length,0);assert.equal(a.button('Tìm ứng viên để đối chiếu'),undefined);
});
test('pending submit is single and lost acknowledgements never generate replacement jobs',async()=>{
 let finish;const a=boot((route,body,fallback)=>route==='/api/issue-relations'?new Promise(resolve=>finish=resolve):fallback(route,body));a.render();await a.choose();a.consent();const submit=a.button('Đối chiếu cặp issue bằng Groq'),pending=submit.onclick();await submit.onclick();assert.equal(a.calls.filter(([r])=>r==='/api/issue-relations').length,1);
 finish(a.row());await pending;assert.ok(a.button('Xuất đối chiếu issue JSON'));
 const b=boot((route,body,fallback)=>{if(route==='/api/issue-relations')throw Error('lost acknowledgement');return fallback(route,body);});b.render();await b.choose();b.consent();await b.button('Đối chiếu cặp issue bằng Groq').onclick();assert.equal(b.button('Đối chiếu cặp issue bằng Groq').disabled,true);assert.match(b.text(b.target),/không tự tạo lượt API thay thế/);
});
test('failed, interrupted and cancelled jobs retain their intent without a replacement action',async()=>{
 for(const status of ['failed','interrupted','cancelled']){const a=boot((route,body,fallback)=>{if(route==='/api/issue-relations'||route==='/api/jobs/'+relationId)return {id:relationId,mode:'issue_relation',status};return fallback(route,body);});a.render();await a.choose();a.consent();await a.button('Đối chiếu cặp issue bằng Groq').onclick();assert.equal(a.button('Đối chiếu cặp issue bằng Groq').disabled,true);assert.match(a.text(a.target),/receipt được giữ/);assert.equal(a.calls.filter(([r])=>r==='/api/issue-relations').length,1);}
});
test('navigation drops late search and preview responses without repainting another run',async()=>{
 for(const endpoint of ['/api/issue-relation-candidates','/api/issue-relations/preview']){
  let finish;const a=boot((route,body,fallback)=>route===endpoint?new Promise(resolve=>finish=()=>resolve(fallback(route,body))):fallback(route,body));a.render();let pending;
  if(endpoint.endsWith('candidates'))pending=a.button('Tìm ứng viên để đối chiếu').onclick();else{await a.button('Tìm ứng viên để đối chiếu').onclick();pending=a.button('Xem cặp nguồn').onclick();}
  a.renderer.cancel();a.target.replaceChildren();finish();await pending;assert.equal(a.target.children.length,0);
 }
});
test('stale candidate controls cannot retarget a newer search',async()=>{
 const a=boot();a.render();await a.button('Tìm ứng viên để đối chiếu').onclick();const stale=a.button('Xem cặp nguồn');await a.button('Tìm ứng viên để đối chiếu').onclick();const count=a.calls.length;await stale.onclick();assert.equal(a.calls.length,count);
});
test('new search hides old candidates while pending and reports incomplete synced scope',async()=>{
 let finish,count=0;const a=boot((route,body,fallback)=>{if(route==='/api/issue-relation-candidates'&&++count===2)return new Promise(resolve=>finish=()=>{const value=fallback(route,body);value.warnings=['synced_issue_index_unavailable'];resolve(value);});return fallback(route,body);});
 a.render();await a.button('Tìm ứng viên để đối chiếu').onclick();const old=a.button('Xem cặp nguồn'),pending=a.button('Tìm ứng viên để đối chiếu').onclick();assert.equal(a.button('Xem cặp nguồn'),undefined);await old.onclick();assert.equal(a.calls.length,2);finish();await pending;assert.match(a.text(a.target),/có thể thiếu nguồn đã đồng bộ/);assert.equal(a.button('Tìm ứng viên để đối chiếu').disabled,false);
});
test('opening a saved result wins over a late candidate preview without a paid call',async()=>{
 let finish;const a=boot((route,body,fallback)=>route.endsWith('/preview')?new Promise(resolve=>finish=()=>resolve(fallback(route,body))):fallback(route,body));a.render();await a.button('Mở các bản đối chiếu đã lưu').onclick();await a.button('Tìm ứng viên để đối chiếu').onclick();const pending=a.button('Xem cặp nguồn').onclick();await a.button('Mở bản đối chiếu').onclick();assert.ok(a.button('Xuất đối chiếu issue JSON'));finish();await pending;assert.ok(a.button('Xuất đối chiếu issue JSON'));assert.equal(a.checkbox(),undefined);assert.equal(a.calls.filter(([r])=>r==='/api/issue-relations').length,0);
});
test('a running saved comparison resumes one read poll after reattaching, with no paid retry',async()=>{
 let finish,reads=0;const a=boot((route,body,fallback)=>{if(route==='/api/jobs/'+relationId&&++reads===1)return new Promise(resolve=>finish=resolve);return fallback(route,body);});a.render();await a.button('Mở các bản đối chiếu đã lưu').onclick();const pending=a.button('Mở bản đối chiếu').onclick();a.render();assert.equal(reads,1);finish(a.row('running'));await pending;assert.equal(a.timers.length,1);await a.timers.shift()();assert.equal(reads,2);assert.ok(a.button('Xuất đối chiếu issue JSON'));assert.equal(a.calls.filter(([r])=>r==='/api/issue-relations').length,0);
});
test('old-pair cancellation replies stay on their own pair and do not repaint a new selection',async()=>{
 let finish;const other=job('e'),second=bundle();second.source.right.issue_key='local:third';second.source.right.reference.job_id=job('d');second.source.sha256=hash('a');second.sha256=hash('b');
 const a=boot((route,body,fallback)=>{
  if(route.endsWith('/issue-relations')&&body===undefined){const value=fallback(route,body);value.relation_jobs.push({id:other,mode:'issue_relation',status:'completed'});return value;}
  if(route==='/api/jobs/'+relationId)return {id:relationId,mode:'issue_relation',status:'running'};
  if(route==='/api/jobs/'+relationId+'/cancel')return new Promise(resolve=>finish=resolve);
  if(route==='/api/jobs/'+other)return {id:other,mode:'issue_relation',status:'completed'};
  if(route==='/api/jobs/'+other+'/issue-relation.json')return second;
  return fallback(route,body);
 });
 a.render();await a.button('Mở các bản đối chiếu đã lưu').onclick();await a.button('Mở bản đối chiếu').onclick();const pending=a.button('Hủy đối chiếu issue').onclick();
 await a.all(a.target).filter(e=>e.tag==='button'&&e.textContent==='Mở bản đối chiếu')[1].onclick();const current=a.target.children[0];assert.match(a.text(a.target),/local:third/);finish({});await pending;
 assert.equal(a.target.children[0],current);assert.doesNotMatch(a.text(a.target),/Đã yêu cầu hủy/);assert.equal(a.calls.filter(([r])=>r==='/api/issue-relations').length,0);
});
test('late old-pair poll success and failure cannot repaint or show a read error on another pair',async()=>{
 for(const fail of [false,true]){let settle;const other=job('e'),second=bundle();second.source.right.issue_key='local:third';second.source.right.reference.job_id=job('d');second.source.sha256=hash('a');second.sha256=hash('b');
  const a=boot((route,body,fallback)=>{
   if(route.endsWith('/issue-relations')&&body===undefined){const value=fallback(route,body);value.relation_jobs.push({id:other,mode:'issue_relation',status:'completed'});return value;}
   if(route==='/api/jobs/'+relationId)return new Promise((resolve,reject)=>settle=()=>fail?reject(Error('old pair read failure')):resolve({id:relationId,mode:'issue_relation',status:'completed'}));
   if(route==='/api/jobs/'+other)return {id:other,mode:'issue_relation',status:'completed'};
   if(route==='/api/jobs/'+other+'/issue-relation.json')return second;
   return fallback(route,body);
  });
  a.render();await a.button('Mở các bản đối chiếu đã lưu').onclick();const pending=a.button('Mở bản đối chiếu').onclick();await a.all(a.target).filter(e=>e.tag==='button'&&e.textContent==='Mở bản đối chiếu')[1].onclick();const current=a.target.children[0];settle();await pending;
  assert.equal(a.target.children[0],current);assert.match(a.text(a.target),/local:third/);assert.doesNotMatch(a.text(a.target),/Chưa đọc được đối chiếu/);
 }
});
test('old-pair export errors remain private after another saved pair is selected',async()=>{
 let rejectExport;const other=job('e'),second=bundle();second.source.right.issue_key='local:third';second.source.right.reference.job_id=job('d');second.source.sha256=hash('a');second.sha256=hash('b');
 const a=boot((route,body,fallback)=>{
  if(route.endsWith('/issue-relations')&&body===undefined){const value=fallback(route,body);value.relation_jobs.push({id:other,mode:'issue_relation',status:'completed'});return value;}
  if(route==='/api/jobs/'+other)return {id:other,mode:'issue_relation',status:'completed'};
  if(route==='/api/jobs/'+other+'/issue-relation.json')return second;
  return fallback(route,body);
 });
 a.render();await a.saved();a.context.fetch=()=>new Promise((resolve,reject)=>rejectExport=reject);const pending=a.button('Xuất đối chiếu issue Markdown').onclick();await a.all(a.target).filter(e=>e.tag==='button'&&e.textContent==='Mở bản đối chiếu')[1].onclick();const current=a.target.children[0];rejectExport(Error('old export failed'));await pending;
 assert.equal(a.target.children[0],current);assert.doesNotMatch(a.text(a.target),/Chưa xuất được đối chiếu/);assert.equal(a.downloads.length,0);
});
test('VI/EN switch revokes consent, preserves query draft and displays source text verbatim',async()=>{
 const a=boot();a.render();const input=a.all(a.target).find(e=>e.type==='search');input.value='parser blank';input.oninput();await a.choose();a.consent();a.language.value='en';a.listeners['issueintel:languagechange']();assert.equal(a.checkbox().checked,false);assert.equal(a.calls.length,2);assert.match(a.text(a.target),/Selected source pair/);
 assert.equal(a.all(a.target).find(e=>e.type==='search').value,'parser blank');a.language.value='vi';a.listeners['issueintel:languagechange']();await a.saved();a.language.value='en';a.listeners['issueintel:languagechange']();assert.match(a.text(a.target),/affected version is unknown/);assert.match(a.text(a.target),/🧪 v1 parser/);
});
test('exports bind both result and source headers, suppressing downloads after navigation',async()=>{
 const a=boot();a.render();await a.saved();await a.button('Xuất đối chiếu issue JSON').onclick();assert.equal(a.downloads.length,1);
 a.context.fetch=async()=>({ok:true,headers:{get:()=>hash('0')}});await a.button('Xuất đối chiếu issue Markdown').onclick();assert.equal(a.downloads.length,1);
 let finish;a.context.fetch=()=>new Promise(resolve=>finish=resolve);const pending=a.button('Xuất đối chiếu issue Markdown').onclick();a.renderer.cancel();finish({ok:true,headers:{get:name=>name==='X-IssueIntel-Source-Pack-SHA256'?report.source.pack_sha256:bundle().sha256},blob:async()=>new Blob(['saved'])});await pending;assert.equal(a.downloads.length,1);
});
test('app integration cancels detached pair controls and loads token-based responsive assets',()=>{
 const app=fs.readFileSync(path.join(dir,'app.js'),'utf8'),html=fs.readFileSync(path.join(dir,'index.html'),'utf8'),css=fs.readFileSync(path.join(dir,'issue-relations.css'),'utf8');
 assert.ok(app.includes("if(activeTab==='related')window.IssueIntelIssueRelations?.render"));assert.ok((app.match(/IssueIntelIssueRelations\?\.cancel/g)||[]).length>=3);assert.match(html,/issue-relations\.js/);assert.match(html,/issue-relations\.css/);assert.match(css,/grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);assert.match(css,/@media\(max-width:720px\)/);assert.match(css,/var\(--focus\)/);assert.doesNotMatch(css,/#(?:fff|000|[a-f\d]{6})\b/i);
});

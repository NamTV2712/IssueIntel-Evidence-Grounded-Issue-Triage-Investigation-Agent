const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const here=path.join(__dirname,'../src/issueintel/interfaces/static'),code=fs.readFileSync(path.join(here,'dossier.js'),'utf8');
const catalog={};vm.runInNewContext(fs.readFileSync(path.join(here,'locales.js'),'utf8'),{window:catalog});const messages=catalog.IssueIntelMessages;
const job=c=>c.repeat(24),sha=c=>c.repeat(64),clone=v=>JSON.parse(JSON.stringify(v));
const selected=job('b'),parent=job('a'),expected={available:true,pack_sha256:sha('2'),report_sha256:sha('4')};
function data(){
 const report=(id,hash)=>({sha256:hash,hypotheses:[{id:'h0',statement:'<img src=x onerror=alert(1)> Unverified mechanism',support_ids:['e1']}],evidence:[{id:'e1',locator:'parser.py:1-2',text:'return None'}]});
 return {schema:'investigation_dossier_v1',sha256:sha('9'),source:{selected_job_id:selected,selected_pack_sha256:sha('2'),identity_sha256:sha('5')},
  issue:{repository:'synthetic/example',issue_url:null},model_calls:0,evaluation_performed:false,warnings:[],
  runs:[{job_id:parent,status:'completed',mode:'example',depth:'quick',provider:'groq',binding:{pack_sha256:sha('1')},report:report(parent,sha('3'))},
        {job_id:selected,parent_job_id:parent,status:'completed',mode:'check_followup',depth:'quick',provider:'groq',binding:{pack_sha256:sha('2')},report:report(selected,sha('4'))}],
  timeline:[{kind:'investigation',job_id:parent,occurred_at:'2026-10-19T01:00:00Z'},{kind:'investigation',job_id:selected,occurred_at:'2026-10-19T02:00:00Z'}],
  hypothesis_links:{sha256:sha('6'),events:[],causal_verified:false,independent_human:false},
  transitions:[{parent_job_id:parent,child_job_id:selected,reason:'selected_reported_observations',comparison:{evidence:[]},observation_source:{journal_sha256:sha('7')},
   prediction_delta:[{observation_id:'observation_1',check_id:'check_1',hypothesis_id:'h0',predicted_outcome:'A',reported_outcome:'A',comparison:'matches_prediction',revision_applicability:'unknown_commit',superseded_later:false}]}]};
}
function boot(handler){
 const calls=[],dispatched=[],downloads=[],handoffs=[],language={value:'vi'};
 class Element{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.attributes={};this.isConnected=true;this.value='';this.checked=false;this.disabled=false;}
 append(...values){this.children.push(...values);}replaceChildren(...values){this.children=values;}setAttribute(key,value){this.attributes[key]=value;}click(){downloads.push(this.download||'clicked');}set innerHTML(_){throw Error('Unsafe HTML');}}
 const all=e=>[e,...e.children.flatMap(all)],text=e=>e.textContent+' '+e.children.map(text).join(' ');
 const target=new Element('div');let context={window:{IssueIntelUI:{t:(key,vars={})=>{assert.ok(messages[key]?.[language.value],'Missing translation '+key);return messages[key][language.value].replace(/\{(\w+)\}/g,(_,key)=>vars[key]);}}},
  document:{createElement:tag=>new Element(tag),dispatchEvent:event=>dispatched.push(event)},
  CustomEvent:class{constructor(type,init){this.type=type;this.detail=init.detail;}},api:async(route,body)=>{calls.push([route,body]);return handler?handler(route,body):data();},
  downloadHandoff:async(...args)=>{handoffs.push(args);return true;},
  URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:test';},revokeObjectURL:()=>{}},Blob:class{constructor(parts){this.parts=parts;}},setTimeout:fn=>fn()};
 vm.runInNewContext(code,context);
 const render=(id=selected,bound=expected)=>context.window.IssueIntelDossier.render(target,id,bound);
 const button=label=>all(target).find(e=>e.tag==='button'&&e.textContent===label),form=()=>all(target).find(e=>e.tag==='form');
 const fill=()=>{const elements=all(form()),why=elements.find(e=>e.tag==='textarea'),ref=elements.find(e=>e.tag==='input');why.value='Observed label aligns with the saved proposal; still unverified.';why.oninput();ref.checked=true;ref.onchange();};
 return {target,context,all,text,language,calls,dispatched,downloads,handoffs,render,button,form,fill,cancel:()=>context.window.IssueIntelDossier.cancel(),flush:async()=>{for(let i=0;i<5;i++)await Promise.resolve();}};
}

function previewData(){return {schema:'issueintel_handoff_preview_v1',sha256:sha('a'),archive_sha256:sha('b'),archive_bytes:1200,uncompressed_bytes:9000,
 source:{selected_job_id:selected,selected_pack_sha256:expected.pack_sha256,dossier_sha256:sha('9')},model_calls:0,contains_private_content:true,entries:[],automatic_ancestor_job_ids:[parent]};}

test('handoff requires explicit preview and export bound to selected dossier and source',async()=>{
 const app=boot(route=>route.endsWith('/handoff-preview')?previewData():data());app.render();await app.flush();
 assert.equal(app.button('Xác nhận xuất gói riêng tư đã chọn'),undefined);
 await app.button('Xem trước nội dung và dung lượng').onclick();
 assert.match(app.text(app.target),/1[,.]200 bytes/);assert.equal(app.calls.length,2);
 assert.deepEqual(JSON.parse(JSON.stringify(app.calls[1][1])),{source_pack_sha256:expected.pack_sha256,dossier_sha256:sha('9'),job_ids:[selected]});
 await app.button('Xác nhận xuất gói riêng tư đã chọn').onclick();assert.equal(app.handoffs.length,1);
 assert.equal(app.handoffs[0][1].confirmation,'export_selected_private_handoff');assert.equal(app.handoffs[0][1].preview_sha256,sha('a'));
 assert.equal(app.handoffs[0][3](),true);app.cancel();assert.equal(app.handoffs[0][3](),false);
});
test('changing selected runs invalidates handoff preview and never submits export',async()=>{
 const app=boot(route=>route.endsWith('/handoff-preview')?previewData():data());app.render();await app.flush();await app.button('Xem trước nội dung và dung lượng').onclick();
 const control=app.all(app.target).find(e=>e.tag==='input'&&e.type==='checkbox'&&!e.disabled);control.checked=true;control.onchange();
 assert.equal(app.button('Xác nhận xuất gói riêng tư đã chọn'),undefined);assert.equal(app.handoffs.length,0);
});
test('late or mismatched handoff preview cannot export another selected source',async()=>{
 for(const change of [p=>p.source.selected_job_id=parent,p=>p.source.dossier_sha256=sha('1'),p=>p.model_calls=1]){
  const app=boot(route=>{if(route.endsWith('/handoff-preview')){const p=previewData();change(p);return p;}return data();});
  app.render();await app.flush();await app.button('Xem trước nội dung và dung lượng').onclick();assert.equal(app.button('Xác nhận xuất gói riêng tư đã chọn'),undefined);
 }
 let finish;const app=boot(route=>route.endsWith('/handoff-preview')?new Promise(resolve=>finish=resolve):data());app.render();await app.flush();
 const pending=app.button('Xem trước nội dung và dung lượng').onclick();app.cancel();app.target.replaceChildren();finish(previewData());await pending;assert.equal(app.target.children.length,0);
});

test('dossier reads once, renders prediction limits and uses inert source text',async()=>{
 const app=boot();app.render();await app.flush();assert.equal(app.calls.length,1);assert.equal(app.calls[0][0],'/api/jobs/'+selected+'/dossier');
 const content=app.text(app.target);assert.match(content,/Khớp nhãn dự đoán/);assert.match(content,/Chưa biết commit đã thử/);assert.match(content,/chưa xác minh nguyên nhân/);assert.match(content,/<img src=x onerror/);
 app.render();await app.flush();assert.equal(app.calls.length,1);
});
test('benchmark or unavailable sources do not fetch or expose write forms',async()=>{
 const app=boot();app.render(selected,{...expected,available:false});await app.flush();assert.equal(app.calls.length,0);assert.equal(app.form(),undefined);
});
test('mismatched selected job, pack, report and truth flags never expose sources',async()=>{
 for(const change of [v=>v.source.selected_job_id=job('c'),v=>v.source.selected_pack_sha256=sha('a'),v=>v.runs[1].report.sha256=sha('a'),v=>v.model_calls=1,v=>v.hypothesis_links.causal_verified=true]){
  const value=data();change(value);const app=boot(()=>value);app.render();await app.flush();assert.equal(app.form(),undefined);assert.match(app.text(app.target),/Chưa đọc được hồ sơ/);
 }
});
test('detached async reads do not change another selected dossier',async()=>{
 let finish;const app=boot(()=>new Promise(resolve=>finish=resolve));app.render();app.cancel();app.target.replaceChildren();finish(data());await app.flush();assert.equal(app.target.children.length,0);
});
test('reattaching the same pending source and changing language finishes without a second read',async()=>{
 let finish;const app=boot(()=>new Promise(resolve=>finish=resolve));app.render();app.cancel();app.language.value='en';app.render();finish(data());await app.flush();
 assert.equal(app.calls.length,1);assert.match(app.text(app.target),/Issue investigation dossier/);assert.ok(app.form());
});
test('manual link binds exact reports and explicit source references; draft survives redraw',async()=>{
 const app=boot((route,body)=>{if(!body)return data();const value=data();value.hypothesis_links.sha256=sha('8');return value;});
 app.render();await app.flush();app.fill();app.render();assert.match(app.all(app.form()).find(e=>e.tag==='textarea').value,/Observed label/);
 await app.form().onsubmit({preventDefault(){}});assert.equal(app.calls.length,2);
 const [route,request]=app.calls[1];assert.equal(route,'/api/jobs/'+selected+'/dossier-links');assert.equal(request.expected_sha256,sha('6'));assert.equal(request.source_pack_sha256,sha('2'));
 assert.deepEqual(JSON.parse(JSON.stringify(request.relation.evidence_refs)),[{job_id:parent,evidence_id:'e1'}]);assert.equal(request.relation.parent_hypothesis_id,'h0');assert.equal(request.relation.child_hypothesis_id,'h0');
});
test('missing rationale or evidence does not submit and preserves draft',async()=>{
 const app=boot();app.render();await app.flush();const why=app.all(app.form()).find(e=>e.tag==='textarea');why.value='Keep this draft';why.oninput();
 await app.form().onsubmit({preventDefault(){}});assert.equal(app.calls.length,1);assert.match(app.text(app.target),/chọn từ 1 đến 12/);assert.equal(app.all(app.form()).find(e=>e.tag==='textarea').value,'Keep this draft');
});
test('double submit and detached submit cannot create two link writes',async()=>{
 let finish;const app=boot((route,body)=>body?new Promise(resolve=>finish=resolve):data());app.render();await app.flush();app.fill();const form=app.form();const pending=form.onsubmit({preventDefault(){}});
 await form.onsubmit({preventDefault(){}});assert.equal(app.calls.length,2);app.cancel();finish(data());await pending;await form.onsubmit({preventDefault(){}});assert.equal(app.calls.length,2);
});
test('failed write retains draft and requires deliberate reload; no automatic retry',async()=>{
 const app=boot((route,body)=>{if(body)throw Error('ambiguous response');return data();});app.render();await app.flush();app.fill();await app.form().onsubmit({preventDefault(){}});
 assert.equal(app.calls.length,2);assert.match(app.text(app.target),/Tải lại hồ sơ để kiểm tra/);assert.match(app.all(app.form()).find(e=>e.tag==='textarea').value,/Observed label/);
 await app.button('Tải lại hồ sơ').onclick();await app.flush();assert.equal(app.calls.length,3);assert.equal(app.calls[2][1],undefined);
});
test('exports cached validated dossier and source navigation dispatches source IDs only',async()=>{
 const app=boot();app.render();await app.flush();app.button('Xuất hồ sơ JSON').onclick();assert.equal(app.calls.length,1);assert.match(app.downloads[0].parts[0],/investigation_dossier_v1/);
 app.button('Mở phiên cha').onclick();assert.equal(app.dispatched[0].type,'issueintel:dossieropen');assert.equal(app.dispatched[0].detail.job_id,parent);
 const button=app.button('Mở phiên con');app.cancel();button.onclick();assert.equal(app.dispatched.length,1);
});
test('changed report projection invalidates a cache even if pack bytes are the same',async()=>{
 const app=boot();app.render();await app.flush();app.render(selected,{...expected,report_sha256:sha('8')});await app.flush();assert.equal(app.calls.length,2);assert.equal(app.form(),undefined);
});
test('all dossier labels render in English without changing source text or requesting a model',async()=>{
 const app=boot();app.language.value='en';app.render();await app.flush();assert.match(app.text(app.target),/Matches the predicted label/);assert.match(app.text(app.target),/Tested commit unknown/);
 assert.match(app.text(app.target),/<img src=x onerror/);assert.equal(app.calls.length,1);assert.ok(app.calls.every(([route])=>!route.includes('investigations')));
});

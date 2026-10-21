const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const here=path.join(__dirname,'../src/issueintel/interfaces/static'),code=fs.readFileSync(path.join(here,'finalization.js'),'utf8');
const messages=JSON.parse(fs.readFileSync(path.join(here,'locales.js'),'utf8').replace(/^window.IssueIntelMessages\s*=\s*/,'').replace(/;\s*$/,''));
const hash=c=>c.repeat(64),job=c=>c.repeat(24),clone=value=>JSON.parse(JSON.stringify(value));
const row={id:job('a'),mode:'github',provider:'groq',depth:'quick',status:'failed',stage_checkpoints:{verification_input:{sha256:hash('1')},verification:{sha256:hash('2')}}};
const preview={available:true,source_job_id:row.id,verification_input_sha256:hash('1'),verification_sha256:hash('2'),model:'saved-model',commit_sha:'b'.repeat(40),model_calls:0,no_api_required:true};
const created={id:job('c'),mode:'finalization',provider:'none',depth:'export',status:'queued'};
function boot(handler){
  const controls=new Map(),events=new Map(),calls=[],dispatched=[],language={value:'vi'};
  class Element{constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.disabled=false;this.attributes={};}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(key,value){this.attributes[key]=value;}set innerHTML(_){throw Error('Unsafe HTML');}}
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const all=e=>[e,...e.children.flatMap(all)],text=e=>e.textContent+' '+e.children.map(text).join(' ');
  const context={window:{IssueIntelUI:{t:(key,values={})=>{assert.ok(messages[key]?.[language.value],'Missing translation '+key);return messages[key][language.value].replace(/\{(\w+)\}/g,(_,k)=>values[k]);}}},
    document:{getElementById:get,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>dispatched.push(event)},
    api:async(route,body)=>{calls.push([route,body]);return handler?handler(route,body):clone(body?created:preview);},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}};
  vm.runInNewContext(code,context);
  const button=caption=>{const result=all(get('finalization-content')).find(e=>e.tag==='button'&&e.textContent===caption);assert.ok(result,caption);return result;};
  return {get,all,text,calls,events,dispatched,language,show:context.window.IssueIntelFinalization.show,button,click:caption=>button(caption).onclick(),check:()=>button(language.value==='vi'?'Kiểm tra checkpoint để kết xuất':'Check checkpoints for finalization').onclick(),submit:()=>button(language.value==='vi'?'Kết xuất hồ sơ từ checkpoint đã lưu':'Finalize report from saved checkpoints').onclick()};
}
test('only failed/interrupted/cancelled original product Quick/Deep Groq with both checkpoint hashes offer finalization',()=>{
  const app=boot();for(const status of ['failed','interrupted','cancelled']){app.show({...row,status});assert.equal(app.get('finalization-panel').hidden,false);}
  for(const patch of [{status:'queued'},{status:'running'},{status:'completed'},{mode:'main_dev'},{mode:'resume'},{provider:'none'},{depth:'planned'},{stage_checkpoints:{verification_input:{sha256:hash('1')}}},{stage_checkpoints:{verification_input:{sha256:hash('1')},verification:{sha256:'bad'}}}]){app.show({...row,...patch});assert.equal(app.get('finalization-panel').hidden,true);}
  app.show({...row,mode:'local',depth:'deep'});assert.equal(app.get('finalization-panel').hidden,false);assert.equal(app.calls.length,0);
});
test('opening and language redraws do not fetch checkpoints or start a job',()=>{
  const app=boot();app.show(row);app.language.value='en';app.events.get('issueintel:languagechange')();assert.equal(app.calls.length,0);assert.match(app.text(app.get('finalization-content')),/without an API call/);assert.equal(app.all(app.get('finalization-content')).some(e=>e.tag==='input'),false);
});
test('explicit preview preserves exact source and states zero new calls separately from historical costs',async()=>{
  const app=boot();app.show(row);await app.check();assert.equal(app.calls.length,1);assert.equal(app.calls[0][0],'/api/jobs/'+row.id+'/finalization');assert.equal(app.calls[0][1],undefined);
  const text=app.text(app.get('finalization-content'));assert.match(text,/0 lời gọi model/);assert.match(text,/Không chạy verifier mới/);assert.match(text,/chi phí lịch sử/);assert.match(text,/saved-model/);assert.match(text,new RegExp(hash('1')));assert.match(text,new RegExp(hash('2')));assert.equal(app.all(app.get('finalization-content')).some(e=>e.tag==='input'),false);
  app.language.value='en';app.events.get('issueintel:languagechange')();assert.match(app.text(app.get('finalization-content')),/Historical calls and costs remain/);assert.equal(app.calls.length,1);
});
test('continue binds both checkpoint hashes and explicit zero-call confirmation then dispatches a normal child',async()=>{
  const app=boot();app.show(row);await app.check();await app.submit();assert.equal(app.calls.length,2);assert.deepEqual(JSON.parse(JSON.stringify(app.calls[1])),['/api/jobs/'+row.id+'/finalization',{verification_input_sha256:hash('1'),verification_sha256:hash('2'),confirmation:'finalize_saved_checkpoints_without_api'}]);
  assert.equal(app.dispatched[0].type,'issueintel:finalizationcreated');assert.equal(app.dispatched[0].detail.job_id,created.id);await app.click('Mở phiên kết xuất đã tạo');assert.equal(app.calls.length,2);assert.equal(app.dispatched.length,2);
});
test('unavailable, failed, mismatched or non-zero-call previews never expose continuation',async()=>{
  for(const response of [null,{available:false},{...preview,source_job_id:job('d')},{...preview,verification_input_sha256:hash('3')},{...preview,verification_sha256:hash('4')},{...preview,model_calls:1},{...preview,no_api_required:false},{...preview,commit_sha:'main'}]){
    const app=boot(()=>response);app.show(row);await app.check();assert.equal(app.all(app.get('finalization-content')).some(e=>e.textContent==='Kết xuất hồ sơ từ checkpoint đã lưu'),false);assert.equal(app.calls.length,1);assert.equal(app.dispatched.length,0);
  }
  const app=boot(()=>{throw Error('Offline');});app.show(row);await app.check();assert.match(app.text(app.get('finalization-content')),/Chưa kiểm tra được/);assert.equal(app.dispatched.length,0);
});
test('source or checkpoint changes invalidate late responses and detached continue buttons',async()=>{
  let finish;const app=boot(()=>new Promise(resolve=>finish=resolve));app.show(row);const pending=app.check();app.show({...row,id:job('d')});finish(preview);await pending;assert.equal(app.all(app.get('finalization-content')).some(e=>e.textContent==='Kết xuất hồ sơ từ checkpoint đã lưu'),false);
  const ready=boot();ready.show(row);await ready.check();const detached=ready.button('Kết xuất hồ sơ từ checkpoint đã lưu');ready.show({...row,stage_checkpoints:{verification_input:{sha256:hash('3')},verification:{sha256:hash('2')}}});await detached.onclick();assert.equal(ready.calls.length,1);
});
test('pending preview and child creation cannot be double-clicked, and active app run blocks submission',async()=>{
  let finish;const app=boot(()=>new Promise(resolve=>finish=resolve));app.show(row);const detached=app.button('Kiểm tra checkpoint để kết xuất'),pending=detached.onclick();await detached.onclick();assert.equal(app.calls.length,1);finish(preview);await pending;
  const ready=boot((route,body)=>body?new Promise(resolve=>finish=resolve):preview);ready.show(row);await ready.check();ready.get('run-button').disabled=true;await ready.submit();assert.equal(ready.calls.length,1);ready.get('run-button').disabled=false;const submit=ready.button('Kết xuất hồ sơ từ checkpoint đã lưu'),inflight=submit.onclick();await submit.onclick();assert.equal(ready.calls.length,2);finish(created);await inflight;assert.equal(ready.dispatched.length,1);
});
test('late child creation is retained across source switches and reopened without a new submission',async()=>{
  let finish;const app=boot((route,body)=>body?new Promise(resolve=>finish=resolve):preview);app.show(row);await app.check();const pending=app.submit();app.show({...row,id:job('d')});finish(created);await pending;assert.equal(app.dispatched.length,0);app.show(row);await app.click('Mở phiên kết xuất đã tạo');assert.equal(app.dispatched[0].detail.job_id,created.id);assert.equal(app.calls.length,2);
});
test('failed POST or malformed paid-job response makes the uncertainty explicit without dispatch or auto retry',async()=>{
  for(const response of [null,{...created,provider:'groq'},{...created,mode:'resume'},{...created,id:'bad'},{...created,id:row.id}]){const app=boot((route,body)=>body?response:preview);app.show(row);await app.check();await app.submit();assert.match(app.text(app.get('finalization-content')),/Kiểm tra lịch sử/);assert.equal(app.dispatched.length,0);assert.equal(app.calls.length,2);app.events.get('issueintel:languagechange')();assert.equal(app.calls.length,2);}
});
test('source, commit and model text remain inert; completion and unavailable checkpoints clear the old panel',async()=>{
  const app=boot(()=>({...preview,model:'<img src=x onerror=alert(1)>'}));app.show(row);await app.check();assert.match(app.text(app.get('finalization-content')),/<img src=x/);app.show({...row,status:'completed'});assert.equal(app.get('finalization-panel').hidden,true);assert.equal(app.get('finalization-content').children.length,0);assert.equal(app.calls.length,1);
});
test('clearing selection at async app navigation invalidates detached preview and submit actions',async()=>{
  const app=boot();app.show(row);await app.check();const submit=app.button('Kết xuất hồ sơ từ checkpoint đã lưu'),check=app.button('Kiểm tra checkpoint để kết xuất');app.show(null);
  await submit.onclick();await check.onclick();assert.equal(app.calls.length,1);assert.equal(app.dispatched.length,0);assert.equal(app.get('finalization-panel').hidden,true);app.show(row);assert.equal(app.all(app.get('finalization-content')).some(e=>e.textContent==='Kết xuất hồ sơ từ checkpoint đã lưu'),false);
});
test('all finalization translations have nonempty VI/EN text and identical named placeholders',()=>{
  const names=value=>[...value.matchAll(/\{(\w+)\}/g)].map(item=>item[1]).sort();for(const [key,row] of Object.entries(messages)){assert.equal(typeof row.vi,'string',key);assert.equal(typeof row.en,'string',key);assert.ok(row.vi.trim(),key);assert.ok(row.en.trim(),key);assert.deepEqual(names(row.vi),names(row.en),key);}
});
test('following a zero-call child describes checkpoint finalization throughout queued and running states',async()=>{
  const app=fs.readFileSync(path.join(here,'app.js'),'utf8'),start=app.indexOf('async function runJob('),end=app.indexOf('async function openJob(',start);
  assert.ok(start>=0&&end>start);
  for(const language of ['vi','en']){
    const controls=new Map(),logs=[],calls=[],opened=[],cleared=[],states=[{status:'queued',stage:'prepare_snapshot'},{status:'running',stage:'export'},{status:'completed'}];
    const get=id=>{if(!controls.has(id))controls.set(id,{disabled:false,hidden:false});return controls.get(id);};
    const context={viewGeneration:0,window:{IssueIntelFinalization:{show:value=>cleared.push(value)}},$:get,
      t:key=>{assert.ok(messages[key]?.[language],key);return messages[key][language];},message:text=>logs.push(text),
      api:async(route,body)=>{calls.push([route,body]);return states.shift();},showUsage(){},showStageArtifacts(){throw Error('Unexpected failure');},
      openJob:async id=>opened.push(id),history:async()=>{},setTimeout:fn=>fn(),stageView:null,activeJob:null};
    vm.createContext(context);vm.runInContext(app.slice(start,end),context);await context.runJob({mode:'finalization'},{id:created.id});
    const expected=messages['Đang kiểm checkpoint và kết xuất hồ sơ đã lưu…'][language];assert.deepEqual(logs.slice(0,3),[expected,expected,expected]);
    assert.equal(calls.length,3);assert.ok(calls.every(([route,body])=>route==='/api/jobs/'+created.id&&body===undefined));assert.deepEqual(opened,[created.id]);assert.deepEqual(cleared,[null]);assert.equal(get('run-button').disabled,false);
  }
});
test('saved finalization children have a bilingual history label and keep an available issue identity',()=>{
  const app=fs.readFileSync(path.join(here,'app.js'),'utf8'),start=app.indexOf('function renderHistory()'),end=app.indexOf('function comparisonChoices(',start);assert.ok(start>=0&&end>start);
  for(const language of ['vi','en']){
    class Element{constructor(tag,text){this.tag=tag;this.textContent=text||'';this.children=[];}append(...values){this.children.push(...values);}replaceChildren(...values){this.children=values;}}
    const target=new Element('div'),text=value=>value.textContent+' '+value.children.map(text).join(' '),context={jobRows:[{...created,created_at:'2026-10-19T01:00:00Z'},{...created,id:job('d'),case_id:'synthetic:checkpoint',created_at:'2026-10-19T01:00:00Z'}],$:()=>target,
      window:{IssueIntelUI:{locale:()=>language==='vi'?'vi-VN':'en-US'}},t:key=>messages[key][language],node:(tag,value)=>new Element(tag,value),comparisonChoices(){}};
    vm.createContext(context);vm.runInContext(app.slice(start,end),context);context.renderHistory();assert.match(text(target.children[0]),new RegExp(messages['Kết xuất từ checkpoint'][language]));assert.match(text(target.children[1]),/synthetic:checkpoint/);
  }
});

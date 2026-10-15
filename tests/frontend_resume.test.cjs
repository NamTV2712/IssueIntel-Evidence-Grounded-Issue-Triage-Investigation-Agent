const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function boot(handler){
  const controls=new Map(),events=new Map(),calls=[],dispatched=[];
  class Element{constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.disabled=false;this.checked=false;}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}set innerHTML(_){throw Error('Unsafe HTML');}}
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const context={window:{IssueIntelUI:{t:(key,values={})=>key.replace(/\{(\w+)\}/g,(_,k)=>values[k])}},
    document:{getElementById:get,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>dispatched.push(event)},
    api:async(route,body)=>{calls.push([route,body]);return handler(route,body);},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/resume.js'),'utf8'),context);
  const all=e=>[e,...e.children.flatMap(all)],text=e=>e.textContent+' '+e.children.map(text).join(' ');
  return {get,all,text,calls,events,dispatched,show:context.window.IssueIntelResume.show};
}
const row={id:'source',mode:'github',status:'failed',stage_checkpoints:{verification_input:{sha256:'hash'}}};
const preview={available:true,checkpoint_sha256:'checkpoint',hypotheses:3,commit_sha:'a'.repeat(40),model:'model',max_output_tokens:512};

test('opening failed job or changing language never dispatches a resume or API preview',()=>{
  const app=boot();app.show(row);app.events.get('issueintel:languagechange')();
  assert.equal(app.calls.length,0);assert.equal(app.get('resume-panel').hidden,false);
  app.show({...row,status:'completed'});assert.equal(app.get('resume-panel').hidden,true);
  app.show({...row,stage_checkpoints:{...row.stage_checkpoints,verification:{sha256:'done'}}});assert.equal(app.get('resume-panel').hidden,true);
});

test('only explicit approval submits the exact source checkpoint',async()=>{
  const app=boot((route,body)=>body?{id:'b'.repeat(24)}:preview);app.show(row);
  await app.all(app.get('resume-content')).find(e=>e.tag==='button').onclick();
  const check=app.all(app.get('resume-content')).find(e=>e.tag==='input');
  const submit=app.all(app.get('resume-content')).find(e=>e.textContent==='Tiếp tục verifier');
  assert.equal(submit.disabled,true);await submit.onclick();assert.equal(app.calls.length,1);
  check.checked=true;check.onchange();await submit.onclick();
  assert.equal(app.calls[1][0],'/api/jobs/source/resume');assert.equal(app.calls[1][1].checkpoint_sha256,'checkpoint');
  assert.equal(app.calls[1][1].confirmation,'resume_verification_with_api_cost');
  assert.equal(app.dispatched[0].detail.job_id,'b'.repeat(24));
});

test('late preview and prior approval are discarded when selecting another job',async()=>{
  let release;const app=boot(()=>new Promise(resolve=>{release=resolve;}));app.show(row);
  const pending=app.all(app.get('resume-content')).find(e=>e.tag==='button').onclick();
  app.show({...row,id:'second'});release(preview);await pending;
  assert.ok(!app.all(app.get('resume-content')).some(e=>e.tag==='input'));
  assert.match(app.text(app.get('resume-content')),/second/);
});

test('unavailable checkpoint shows a factual stop and no paid action',async()=>{
  const app=boot(()=>({available:false}));app.show(row);
  await app.all(app.get('resume-content')).find(e=>e.tag==='button').onclick();
  assert.match(app.text(app.get('resume-content')),/Chưa có checkpoint/);
  assert.ok(!app.all(app.get('resume-content')).some(e=>e.tag==='input'));
});

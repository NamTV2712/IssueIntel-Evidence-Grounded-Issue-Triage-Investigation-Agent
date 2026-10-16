const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const base=path.join(__dirname,'../src/issueintel/interfaces/static');
const source=fs.readFileSync(path.join(base,'clarifications.js'),'utf8');
const localeContext={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(base,'locales.js'),'utf8'),localeContext);
const messages=localeContext.window.IssueIntelMessages;
const hash=letter=>letter.repeat(64),job=letter=>letter.repeat(24);
function intake(sourceJob=job('a'),fields=['observed_behavior','environment']){
  return {job_id:sourceJob,source_pack_sha256:hash('b'),query_sha256:hash('c'),clarifications:{
    schema:'issue_clarifications_v1',query_sha256:hash('c'),sha256:hash('d'),questions:fields.map(field=>({
      id:'missing:'+field,field,reason:'cue_not_found',prompts:{vi:'Câu hỏi '+field,en:'Question '+field}
    }))}};
}
function boot(handler=()=>{throw Error('Unexpected request');}){
  const controls=new Map(),events=new Map(),calls=[],dispatched=[];
  const all=item=>[item,...item.children.flatMap(all)];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.disabled=false;this.checked=false;this.hidden=false;this.attributes={};}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    setAttribute(name,value){this.attributes[name]=value;}
    querySelectorAll(selector){assert.equal(selector,'textarea');return all(this).filter(item=>item.tag==='textarea');}
    set innerHTML(_){throw Error('Unsafe HTML');}
  }
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const document={documentElement:{lang:'vi'},getElementById:get,createElement:tag=>new Element(tag),
    addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>dispatched.push(event)};
  vm.runInNewContext(source,{document,window:{IssueIntelUI:{t:(key,values={})=>(messages[key]?.[document.documentElement.lang]||key).replace(/\{(\w+)\}/g,(_,name)=>values[name])}},
    api:async(route,body)=>{calls.push([route,body]);return handler(route,body);},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}});
  const inputs=()=>get('clarifications-questions').querySelectorAll('textarea');
  const text=item=>item.textContent+' '+item.children.map(text).join(' ');
  return {get,calls,dispatched,events,inputs,all,text,
    show:(value=intake(),available=true)=>events.get('issueintel:packchange')({detail:{job_id:value?.job_id||job('a'),clarifications_available:available,intake:value}}),
    language:value=>{document.documentElement.lang=value;events.get('issueintel:languagechange')();},
    answer:(index,value)=>{const input=inputs()[index];input.value=value;input.oninput();},
    submit:()=>get('clarifications-form').onsubmit({preventDefault(){}})};
}

test('opening a source or changing language uses saved intake only; restricted packs stay hidden',async()=>{
  const app=boot();app.show();app.language('en');
  assert.equal(app.calls.length,0);assert.equal(app.inputs().length,2);
  assert.match(app.text(app.get('clarifications-questions')),/Question environment/);
  assert.equal(app.get('clarifications-submit').disabled,true);
  app.show(intake(),false);await app.submit();
  assert.equal(app.get('clarifications-panel').hidden,true);assert.equal(app.inputs().length,0);assert.equal(app.calls.length,0);
});

test('empty questions do not imply completeness and leave freeform available',async()=>{
  const app=boot();app.show(intake(job('a'),[]));await app.submit();
  assert.equal(app.get('clarifications-empty').hidden,false);assert.equal(app.get('clarifications-controls').hidden,true);
  assert.equal(app.calls.length,0);
  const html=fs.readFileSync(path.join(base,'index.html'),'utf8');
  assert.match(html,/Không có câu hỏi từ bộ dấu hiệu hiện tại; độ đầy đủ của issue vẫn chưa được xác minh/);
  assert.match(html,/href="#additional-context"/);assert.match(html,/id="followup-form"/);
});

test('a partial answer posts the reviewed source and bundle hashes with explicit language, without enabling Groq',async()=>{
  const app=boot(()=>({id:job('e')}));app.show();await app.submit();assert.equal(app.calls.length,0);
  app.answer(1,'  chưa biết <script>example</script>  ');app.get('clarifications-depth').value='deep';
  app.get('use-groq').checked=true;await app.submit();
  assert.equal(JSON.stringify(app.calls),JSON.stringify([['/api/investigations',{
    mode:'clarification',parent_job_id:job('a'),parent_pack_sha256:hash('b'),questions_sha256:hash('d'),language:'vi',
    answers:[{question_id:'missing:environment',answer:'  chưa biết <script>example</script>  '}],provider:'none',depth:'deep'
  }]]));
  assert.equal(app.dispatched[0].type,'issueintel:clarificationcreated');assert.equal(app.dispatched[0].detail.job_id,job('e'));
  await app.submit();assert.equal(app.calls.length,1);assert.equal(app.get('clarifications-submit').disabled,true);
});

test('language switches preserve answers but require renewed provider consent; editing an answer also clears consent',async()=>{
  const app=boot(()=>({id:job('e')}));app.show();app.answer(0,'Actual output');app.get('clarifications-groq').checked=true;
  // app.renderPack emits this first while translating the rest of the page.
  app.show(intake());assert.equal(app.inputs()[0].value,'Actual output');
  app.language('en');assert.equal(app.inputs()[0].value,'Actual output');assert.equal(app.get('clarifications-groq').checked,false);
  assert.match(app.get('clarifications-count').textContent,/Answered 1\/2/);
  app.get('clarifications-groq').checked=true;app.answer(0,'Changed actual output');assert.equal(app.get('clarifications-groq').checked,false);
  app.get('clarifications-groq').checked=true;await app.submit();
  assert.equal(app.calls[0][1].provider,'groq');assert.equal(app.calls[0][1].language,'en');assert.equal(app.calls.length,1);
});

test('pending submission is single-flight, freezes input, and keeps the sent language even if UI language changes',async()=>{
  let finish;const app=boot(()=>new Promise(resolve=>{finish=resolve;}));app.show();app.answer(0,'Output A');
  const pending=app.submit();await app.submit();assert.equal(app.calls.length,1);
  assert.equal(app.inputs()[0].disabled,true);assert.equal(app.get('clarifications-groq').disabled,true);
  app.language('en');assert.equal(app.inputs()[0].value,'Output A');assert.equal(app.inputs()[0].disabled,true);
  assert.equal(app.calls[0][1].language,'vi');finish({id:job('e')});await pending;assert.equal(app.dispatched.length,1);
});

test('source changes clear answers and consent; late success cannot open the previous run',async()=>{
  let finish;const app=boot(()=>new Promise(resolve=>{finish=resolve;}));app.show();app.answer(0,'Prior output');
  app.get('clarifications-groq').checked=true;const pending=app.submit();app.show(intake(job('f')));
  assert.equal(app.inputs()[0].value,'');assert.equal(app.get('clarifications-groq').checked,false);
  finish({id:job('e')});await pending;assert.equal(app.dispatched.length,0);
  assert.match(app.get('clarifications-parent').textContent,new RegExp(job('f')));
  assert.equal(app.get('clarifications-submit').disabled,true);
});

test('same source rerenders preserve answers; changed source or question hashes require answering anew',()=>{
  const app=boot(),value=intake();app.show(value);app.answer(0,'Saved form input');app.get('clarifications-groq').checked=true;
  app.show(intake());assert.equal(app.inputs()[0].value,'Saved form input');
  const changed=intake();changed.clarifications.sha256=hash('e');app.show(changed);
  assert.equal(app.inputs()[0].value,'');assert.equal(app.get('clarifications-groq').checked,false);
  app.answer(1,'New form input');changed.source_pack_sha256=hash('f');app.show(changed);assert.equal(app.inputs()[1].value,'');
});

test('failed or stale response preserves answers, clears consent and never retries automatically',async()=>{
  for(const handler of [()=>{throw Error('<script>server error</script>');},()=>({id:'invalid'})]){
    const app=boot(handler);app.show();app.answer(0,'Keep this answer');app.get('clarifications-groq').checked=true;
    await app.submit();assert.equal(app.calls.length,1);assert.equal(app.dispatched.length,0);
    assert.equal(app.inputs()[0].value,'Keep this answer');assert.equal(app.get('clarifications-groq').checked,false);
    assert.match(app.get('clarifications-message').textContent,/Câu trả lời được giữ lại/);
    assert.doesNotMatch(app.get('clarifications-message').textContent,/<script>/);
    app.language('en');assert.equal(app.calls.length,1);assert.match(app.get('clarifications-message').textContent,/answers are preserved/);
  }
});

test('malformed source, query or bundle bindings and unexpected questions fail closed',async()=>{
  for(const mutate of [value=>value.job_id='bad',value=>delete value.source_pack_sha256,value=>value.clarifications.query_sha256=hash('e'),
    value=>delete value.clarifications.sha256,value=>value.clarifications.questions.push(value.clarifications.questions[0]),
    value=>value.clarifications.questions[0].id='other',value=>value.clarifications.questions[0].field='__proto__',
    value=>delete value.clarifications.questions[0].prompts.vi]){
    const value=intake();mutate(value);const app=boot();app.show(value);await app.submit();
    assert.equal(app.calls.length,0);assert.equal(app.inputs().length,0);assert.equal(app.get('clarifications-submit').disabled,true);
    assert.match(app.get('clarifications-message').textContent,/Chưa đọc được bộ câu hỏi/);
  }
});

test('prompts remain inert, textareas have linked labels and bounds, and a busy workbench cannot create a run',async()=>{
  const value=intake();value.clarifications.questions[0].prompts.vi='<img src=x onerror=alert(1)>';
  const app=boot();app.show(value);assert.match(app.text(app.get('clarifications-questions')),/<img src=x onerror=alert\(1\)>/);
  const label=app.all(app.get('clarifications-questions')).find(item=>item.tag==='label');
  assert.equal(label.htmlFor,app.inputs()[0].id);assert.equal(app.inputs()[0].maxLength,4000);
  assert.equal(app.inputs()[0].attributes['aria-describedby'],'clarifications-answer-note');
  app.answer(0,'x'.repeat(4001));await app.submit();assert.equal(app.calls.length,0);
  app.answer(0,'x\0x');await app.submit();assert.equal(app.calls.length,0);
  app.answer(0,'Valid answer');app.get('run-button').disabled=true;await app.submit();assert.equal(app.calls.length,0);
  assert.match(app.get('clarifications-message').textContent,/Đang theo dõi tác vụ khác/);
});

test('clarification messages have Vietnamese and English translations with matching placeholders',()=>{
  for(const match of source.matchAll(/\bt\(['"]([^'"]+)['"]/g)){
    const key=match[1];assert.ok(messages[key]?.vi,key);assert.ok(messages[key]?.en,key);
    const slots=text=>[...text.matchAll(/\{(\w+)\}/g)].map(value=>value[1]).sort();
    assert.deepEqual(slots(messages[key].vi),slots(messages[key].en),key);
  }
});

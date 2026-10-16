const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const base=path.join(__dirname,'../src/issueintel/interfaces/static');
const code=fs.readFileSync(path.join(base,'adaptive-questions.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(base,'locales.js'),'utf8'),locale);
const messages=locale.window.IssueIntelMessages,hash=letter=>letter.repeat(64),job=letter=>letter.repeat(24);
const source=()=>({job_id:job('a'),adaptive_available:true,intake:{source_pack_sha256:hash('b'),query_sha256:hash('c')},
  pack:{snapshot:{snapshot_sha256:hash('d'),commit_sha:'1'.repeat(40)},annotation:{hypotheses:[{id:'H1',statement:'Cache state is retained'},{id:'H2',statement:'The parser uses the wrong path'}]},evidence:[{id:'E1'},{id:'E2'}]}});
const bundle=()=>({schema:'adaptive_clarifications_v1',source:{source_job_id:job('a'),source_pack_sha256:hash('b'),query_sha256:hash('c'),snapshot_sha256:hash('d'),commit_sha:'1'.repeat(40)},sha256:hash('e'),
  questions:[{id:'probe:q1',prompts:{vi:'Tắt cache có còn lỗi không?',en:'Does it fail with caching disabled?'},purpose:{vi:'Kiểm tra trạng thái cache',en:'Check cache state'},answer_use:{vi:'Nếu vẫn lỗi thì H1 yếu hơn H2.',en:'A persistent error weakens H1 compared with H2.'},hypothesis_ids:['H1','H2'],evidence_ids:['E1']},
    {id:'probe:q2',prompts:{vi:'Lỗi có xảy ra với đường dẫn mới không?',en:'Does it fail with a fresh path?'},purpose:{vi:'Kiểm tra nhánh parser',en:'Check the parser branch'},answer_use:{vi:'Đường dẫn mới giúp phân biệt H1 và H2.',en:'A fresh path helps distinguish H1 from H2.'},hypothesis_ids:['H1','H2'],evidence_ids:['E2']}]});
function boot(handler=()=>{throw Error('Unexpected API call');}){
  const controls=new Map(),events=new Map(),calls=[],dispatched=[],timers=new Map(),downloads=[];let nextTimer=0;
  const all=item=>[item,...item.children.flatMap(all)];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.attributes={};}
    append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
    setAttribute(name,value){this.attributes[name]=value;}
    querySelectorAll(selector){assert.equal(selector,'textarea');return all(this).filter(item=>item.tag==='textarea');}
    set innerHTML(_){throw Error('Unsafe HTML');}
  }
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const document={documentElement:{lang:'vi'},getElementById:get,createElement:tag=>new Element(tag),addEventListener:(event,fn)=>events.set(event,fn),dispatchEvent:event=>dispatched.push(event)};
  vm.runInNewContext(code,{document,window:{IssueIntelUI:{t:(key,values={})=>(messages[key]?.[document.documentElement.lang]||key).replace(/\{(\w+)\}/g,(_,name)=>values[name])},
    IssueIntelUsage:{render:(target,accounting)=>target.replaceChildren(Object.assign(new Element('p'),{textContent:'receipts '+accounting.job_id}))}},
    api:async(route,body)=>{calls.push([route,body]);return handler(route,body);},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout:fn=>{timers.set(++nextTimer,fn);return nextTimer;},clearTimeout:id=>timers.delete(id),downloadPath:(route,name)=>downloads.push([route,name])});
  const inputs=()=>get('adaptive-questions').querySelectorAll('textarea'),text=item=>item.textContent+' '+item.children.map(text).join(' ');
  return {get,calls,dispatched,timers,downloads,all,text,inputs,show:value=>events.get('issueintel:packchange')({detail:value||source()}),
    language:value=>{document.documentElement.lang=value;events.get('issueintel:languagechange')();},
    consent:()=>{get('adaptive-generate-consent').checked=true;get('adaptive-generate-consent').onchange();},generate:()=>get('adaptive-generate').onclick(),
    load:()=>get('adaptive-load').onclick(),open:()=>all(get('adaptive-saved')).find(item=>item.tag==='button').onclick(),
    answer:(index,value)=>{const input=inputs()[index];input.value=value;input.oninput();},submit:()=>get('adaptive-answer-form').onsubmit({preventDefault(){}})};
}
function handler(value=bundle()){
  return (route,body)=>{
    if(route==='/api/jobs/'+job('a')+'/adaptive-questions')return {source_job_id:job('a'),source_pack_sha256:hash('b'),available:true,question_jobs:[{id:job('f'),status:'completed'}]};
    if(route==='/api/adaptive-questions')return {id:job('f')};
    if(route==='/api/jobs/'+job('f'))return {id:job('f'),status:'completed',accounting:{job_id:job('f')}};
    if(route==='/api/jobs/'+job('f')+'/questions.json')return value;
    if(route==='/api/investigations')return {id:job('9')};
    throw Error('Unexpected route '+route);
  };
}
async function opened(value=bundle()){const app=boot(handler(value));app.show();await app.load();await app.open();return app;}

test('viewing packs, opening details and switching language never generate questions or perform automatic reads',async()=>{
  const app=boot();app.show();app.language('en');app.get('adaptive-panel').open=true;
  await app.generate();await app.submit();assert.equal(app.calls.length,0);assert.equal(app.get('adaptive-generate').disabled,true);
  const restricted=source();restricted.adaptive_available=false;app.show(restricted);app.consent();await app.generate();await app.load();
  assert.equal(app.get('adaptive-panel').hidden,true);assert.equal(app.calls.length,0);
});

test('generation requires saved competing hypotheses, valid source and separate explicit cost consent',async()=>{
  const app=boot(handler());const noHypotheses=source();noHypotheses.pack.annotation.hypotheses.pop();noHypotheses.intake.source_pack_sha256=hash('9');app.show(noHypotheses);app.consent();await app.generate();
  assert.equal(app.calls.length,0);assert.equal(app.get('adaptive-requires').hidden,false);
  app.show();await app.generate();assert.equal(app.calls.length,0);app.get('use-groq').checked=true;await app.generate();assert.equal(app.calls.length,0);
  app.consent();await app.generate();assert.equal(app.calls[0][0],'/api/adaptive-questions');
  assert.equal(JSON.stringify(app.calls[0][1]),JSON.stringify({source_job_id:job('a'),source_pack_sha256:hash('b'),confirmation:'generate_questions_with_api_cost'}));
  assert.equal(app.inputs().length,2);assert.equal(app.get('adaptive-generate-consent').checked,false);
  assert.match(app.text(app.get('adaptive-progress')),/receipts f{24}/);assert.match(app.text(app.get('adaptive-progress')),/Tác vụ câu hỏi/);
  app.consent();await app.generate();assert.equal(app.calls.filter(([route])=>route==='/api/adaptive-questions').length,1);
});

test('saved questions reopen using reads only and show exact prompts, reasons, competing hypotheses and evidence links',async()=>{
  const app=await opened();assert.equal(app.calls.length,3);assert.ok(app.calls.every(([,body])=>body===undefined));
  assert.match(app.text(app.get('adaptive-questions')),/Tắt cache/);assert.match(app.text(app.get('adaptive-questions')),/H1 · Cache state/);
  assert.match(app.text(app.get('adaptive-questions')),/H2 · The parser/);assert.match(app.text(app.get('adaptive-questions')),/Nếu vẫn lỗi/);
  const citation=app.all(app.get('adaptive-questions')).find(item=>item.tag==='button'&&item.textContent==='E1');citation.onclick();
  assert.equal(app.dispatched[0].type,'issueintel:showevidence');assert.equal(app.dispatched[0].detail.job_id,job('a'));
  const download=app.all(app.get('adaptive-provenance')).find(item=>item.tag==='button');download.onclick();
  assert.equal(app.downloads[0][0],'/api/jobs/'+job('f')+'/questions.json');assert.equal(app.calls.length,3);
});

test('partial unknown answer keeps exact source, question job, hash and language; follow-up Groq starts off',async()=>{
  const app=await opened();await app.submit();assert.equal(app.calls.length,3);app.answer(1,'  chưa biết  ');app.get('adaptive-answer-depth').value='deep';
  app.get('use-groq').checked=true;await app.submit();const request=app.calls.at(-1);
  assert.equal(JSON.stringify(request),JSON.stringify(['/api/investigations',{mode:'clarification',parent_job_id:job('a'),parent_pack_sha256:hash('b'),question_job_id:job('f'),questions_sha256:hash('e'),language:'vi',answers:[{question_id:'probe:q2',answer:'  chưa biết  '}],provider:'none',depth:'deep'}]));
  assert.equal(app.dispatched[0].type,'issueintel:clarificationcreated');await app.submit();assert.equal(app.calls.length,4);
});

test('language and answer edits preserve answers while clearing generation and follow-up consent',async()=>{
  const app=await opened();app.answer(0,'Actual output');app.get('adaptive-answer-groq').checked=true;app.get('adaptive-generate-consent').checked=true;
  app.language('en');assert.equal(app.inputs()[0].value,'Actual output');assert.equal(app.get('adaptive-answer-groq').checked,false);assert.equal(app.get('adaptive-generate-consent').checked,false);
  assert.match(app.text(app.get('adaptive-questions')),/Does it fail with caching disabled/);assert.match(app.get('adaptive-count').textContent,/Answered 1\/2/);assert.equal(app.calls.length,3);
  app.get('adaptive-answer-groq').checked=true;app.answer(0,'New output');assert.equal(app.get('adaptive-answer-groq').checked,false);
  app.get('adaptive-answer-groq').checked=true;await app.submit();assert.equal(app.calls.at(-1)[1].provider,'groq');assert.equal(app.calls.at(-1)[1].language,'en');
});

test('question jobs poll local status and can be cancelled without retrying a model call',async()=>{
  let status='running';const app=boot((route,body)=>{
    if(route==='/api/adaptive-questions')return {id:job('f')};
    if(route==='/api/jobs/'+job('f'))return {id:job('f'),status};
    if(route.endsWith('/cancel')){status='cancelled';return {id:job('f')};}
    throw Error('Unexpected route');
  });app.show();app.consent();await app.generate();assert.equal(app.timers.size,1);assert.equal(app.get('adaptive-cancel').hidden,false);
  await app.get('adaptive-cancel').onclick();assert.match(app.get('adaptive-message').textContent,/vẫn tính phí/);
  const fn=[...app.timers.values()][0];app.timers.clear();await fn();assert.equal(app.timers.size,0);assert.equal(app.get('adaptive-cancel').hidden,true);
  assert.match(app.text(app.get('adaptive-progress')),/Mỗi báo cáo nguồn chỉ có một lượt/);app.consent();await app.generate();
  assert.equal(app.calls.filter(([route])=>route==='/api/adaptive-questions').length,1);
});

test('source changes discard pending generation response, stop polling and clear question answers',async()=>{
  let finish;const app=boot(()=>new Promise(resolve=>finish=resolve));app.show();app.consent();const pending=app.generate();
  const other=source();other.job_id=job('7');app.show(other);finish({id:job('f')});await pending;assert.equal(app.calls.length,1);assert.equal(app.timers.size,0);assert.equal(app.inputs().length,0);
  assert.equal(app.get('adaptive-generate-consent').checked,false);assert.match(app.get('adaptive-parent').textContent,/7{24}/);
  const saved=await opened();saved.answer(0,'Old answer');saved.show();assert.equal(saved.inputs()[0].value,'Old answer');const changed=source();changed.intake.source_pack_sha256=hash('9');saved.show(changed);assert.equal(saved.inputs().length,0);
});

test('failed generation retains its job and receipt visibility, disables a new attempt, and never calls itself again',async()=>{
  const app=boot(route=>route==='/api/adaptive-questions'?{id:job('f')}:{id:job('f'),status:'failed',error:'<script>secret error</script>',accounting:{job_id:job('f')}});
  app.show();app.consent();await app.generate();assert.equal(app.calls.length,2);assert.equal(app.timers.size,0);app.consent();await app.generate();
  assert.equal(app.calls.length,2);assert.match(app.text(app.get('adaptive-progress')),/receipt được giữ lại/);assert.doesNotMatch(app.text(app.get('adaptive-progress')),/<script>/);
  app.language('en');assert.match(app.text(app.get('adaptive-progress')),/Each source report has one/);
});

test('bad saved list and stale or malformed question references fail closed without inference',async()=>{
  for(const mutate of [value=>value.source.source_job_id=job('9'),value=>value.source.source_pack_sha256=hash('9'),value=>value.source.query_sha256=hash('9'),
    value=>value.source.snapshot_sha256=hash('9'),value=>value.source.commit_sha='2'.repeat(40),value=>delete value.sha256,
    value=>value.questions[0].hypothesis_ids=['H1'],value=>value.questions[0].hypothesis_ids=['H1','Missing'],value=>value.questions[0].evidence_ids=['Missing'],
    value=>value.questions.push(value.questions[0]),value=>value.questions[0].id='missing:environment',value=>delete value.questions[0].prompts.vi]){
    const value=bundle();mutate(value);const app=await opened(value);assert.equal(app.inputs().length,0);assert.equal(app.get('adaptive-answer-submit').disabled,true);
    assert.match(app.get('adaptive-message').textContent,/Chưa đọc được tác vụ/);assert.equal(app.calls.length,3);
  }
  const app=boot(()=>({source_job_id:job('a'),source_pack_sha256:hash('9'),question_jobs:[]}));app.show();await app.load();assert.equal(app.inputs().length,0);assert.match(app.get('adaptive-message').textContent,/Chưa đọc được danh sách/);
});

test('failed answer submission keeps answers and clears consent without automatic retries',async()=>{
  const read=handler(),app=boot((route,body)=>{if(route==='/api/investigations')throw Error('stale');return read(route,body);});app.show();await app.load();await app.open();app.answer(0,'Keep answer');app.get('adaptive-answer-groq').checked=true;
  await app.submit();assert.equal(app.inputs()[0].value,'Keep answer');assert.equal(app.get('adaptive-answer-groq').checked,false);assert.equal(app.calls.length,4);
  app.language('en');assert.equal(app.calls.length,4);assert.match(app.get('adaptive-message').textContent,/answers are preserved/);
});

test('pending answer submission is single-flight and a late success never changes the newly viewed source',async()=>{
  let finish;const read=handler(),app=boot((route,body)=>route==='/api/investigations'?new Promise(resolve=>finish=resolve):read(route,body));app.show();await app.load();await app.open();app.answer(0,'Saved answer');
  const pending=app.submit();await app.submit();assert.equal(app.calls.length,4);assert.equal(app.inputs()[0].disabled,true);app.language('en');assert.equal(app.inputs()[0].disabled,true);
  const other=source();other.job_id=job('7');app.show(other);finish({id:job('9')});await pending;assert.equal(app.dispatched.length,0);assert.equal(app.inputs().length,0);
});

test('prompts stay inert, answer controls are labeled and bounded, and busy workbench blocks new actions',async()=>{
  const value=bundle();value.questions[0].prompts.vi='<img src=x onerror=alert(1)>';const app=await opened(value);
  assert.match(app.text(app.get('adaptive-questions')),/<img src=x/);const label=app.all(app.get('adaptive-questions')).find(item=>item.tag==='label');
  assert.equal(label.htmlFor,app.inputs()[0].id);assert.equal(app.inputs()[0].maxLength,4000);assert.equal(app.inputs()[0].attributes['aria-describedby'],'adaptive-answer-note');
  for(const answer of ['x'.repeat(4001),'x\0x']){app.answer(0,answer);await app.submit();assert.equal(app.calls.length,3);}
  app.answer(0,'Valid answer');app.get('run-button').disabled=true;await app.submit();assert.equal(app.calls.length,3);
  const generation=boot();generation.show();generation.get('run-button').disabled=true;generation.consent();await generation.generate();assert.equal(generation.calls.length,0);
});

test('all adaptive authored messages and markup have matching English and Vietnamese templates',()=>{
  const html=fs.readFileSync(path.join(base,'index.html'),'utf8');
  const keys=[...code.matchAll(/\bt\(['"]([^'"]+)['"]/g),...html.matchAll(/data-i18n(?:-placeholder|-aria)?="([^"]+)"/g)].map(match=>match[1]);
  for(const key of keys){assert.ok(messages[key]?.vi,key);assert.ok(messages[key]?.en,key);const slots=value=>[...value.matchAll(/\{(\w+)\}/g)].map(item=>item[1]).sort();assert.deepEqual(slots(messages[key].vi),slots(messages[key].en),key);}
});

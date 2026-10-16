const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function boot(handler=()=>{throw Error('Unexpected request');}){
  const controls=new Map(),events=new Map(),calls=[],dispatched=[];
  const all=item=>[item,...item.children.flatMap(all)];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.disabled=false;this.checked=false;this.value='';}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    querySelectorAll(selector){assert.equal(selector,'input[type="checkbox"]');return all(this).filter(item=>item.tag==='input'&&item.type==='checkbox');}
    set innerHTML(_){throw Error('Unsafe HTML');}
  }
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const context={window:{IssueIntelUI:{t:(key,values={})=>key.replace(/\{(\w+)\}/g,(_,k)=>values[k])}},URL,
    document:{getElementById:get,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>dispatched.push(event)},
    api:async(route,body)=>{calls.push([route,body]);return handler(route,body);},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/source-context.js'),'utf8'),context);
  const submit=id=>get(id).onsubmit({preventDefault(){}});
  const show=(id='source',available=true)=>events.get('issueintel:packchange')({detail:{job_id:id,available:true,context_available:available}});
  const text=item=>item.textContent+' '+item.children.map(text).join(' ');
  const checkboxes=()=>get('source-context-results').querySelectorAll('input[type="checkbox"]');
  const choose=(index,checked=true)=>{const input=checkboxes()[index];input.checked=checked;input.onchange();};
  return {get,all,calls,events,dispatched,show,text,checkboxes,choose,submit,
    search:async(query='parser error')=>{get('source-context-query').value=query;return submit('source-context-search-form');}};
}
const hash=letter=>letter.repeat(64);
function response(job='source',query='parser error',count=8){return {
  job_id:job,source_pack_sha256:hash('a'),repository:'example/repository',issue_url:'https://github.com/example/repository/issues/1',
  query,commit_sha:'b'.repeat(40),scan_complete:true,pending_updates:false,sha256:hash('c'),
  results:Array.from({length:count},(_,i)=>({key:'issue:'+(i+2),record_sha256:hash('d'),receipt_sha256:hash('e'),kind:'issue',
    title:'Related parser '+i,url:'https://github.com/example/repository/issues/'+(i+2),excerpt:'Saved excerpt '+i}))
};}

test('source panel is gated separately and opening or changing language never calls API',async()=>{
  const app=boot();app.show();app.events.get('issueintel:languagechange')();
  assert.equal(app.get('source-context-panel').hidden,false);assert.equal(app.calls.length,0);
  await app.search('  ');assert.equal(app.calls.length,0);assert.match(app.get('source-context-message').textContent,/Nhập từ khóa/);
  app.show('strict',false);await app.search();assert.equal(app.get('source-context-panel').hidden,true);assert.equal(app.calls.length,0);
});

test('search renders source excerpts inertly with exact source and receipt hashes',async()=>{
  const found=response();found.scan_complete=false;found.pending_updates=true;
  found.results[0].title='<img src=x onerror=alert(1)>';found.results[0].excerpt='<script>attack()</script>';
  found.results[0].url='javascript:alert(1)';
  const app=boot(()=>found);app.show();await app.search();
  assert.equal(app.calls[0][0],'/api/context-search');assert.equal(app.calls[0][1].job_id,'source');
  assert.equal(app.calls[0][1].query,'parser error');assert.equal(app.checkboxes().length,8);
  const rendered=app.text(app.get('source-context-results'));
  assert.match(rendered,/<img src=x onerror=alert\(1\)>/);assert.match(rendered,/<script>attack\(\)<\/script>/);
  assert.ok(rendered.includes('Record SHA256 '+hash('d')));assert.ok(rendered.includes('Receipt SHA256 '+hash('e')));
  assert.ok(rendered.includes('Commit '+'b'.repeat(40)));assert.match(rendered,/chưa quét xong/);assert.match(rendered,/đang chờ/);
  assert.ok(!app.all(app.get('source-context-results')).some(item=>item.href?.startsWith('javascript:')));
});

test('at most six sources can be chosen, and changing the selection clears Groq consent',async()=>{
  const app=boot(()=>response());app.show();await app.search();
  for(let i=0;i<6;i++)app.choose(i);
  assert.equal(app.get('source-context-count').textContent,'Đã chọn 6/6 nguồn');assert.equal(app.checkboxes()[6].disabled,true);
  app.choose(6);assert.equal(app.checkboxes()[6].checked,false);
  app.get('source-context-groq').checked=true;app.choose(0,false);
  assert.equal(app.get('source-context-groq').checked,false);assert.equal(app.checkboxes()[6].disabled,false);
  app.choose(6);assert.equal(app.get('source-context-count').textContent,'Đã chọn 6/6 nguồn');
  app.get('source-context-groq').checked=true;app.events.get('issueintel:languagechange')();
  assert.equal(app.get('source-context-groq').checked,false);assert.equal(app.calls.length,1);
});

test('creating a run submits only chosen sources with source-pack, record and receipt bindings',async()=>{
  const app=boot(route=>route==='/api/context-search'?response():{id:'f'.repeat(24)});app.show();await app.search();
  await app.submit('source-context-form');assert.equal(app.calls.length,1);
  app.choose(2);app.get('source-context-depth').value='deep';
  await app.submit('source-context-form');
  const [route,request]=app.calls[1];assert.equal(route,'/api/investigations');
  assert.equal(JSON.stringify(request),JSON.stringify({mode:'context',parent_job_id:'source',parent_pack_sha256:hash('a'),
    selected_sources:[{key:'issue:4',record_sha256:hash('d'),receipt_sha256:hash('e')}],provider:'none',depth:'deep'}));
  assert.equal(app.dispatched[0].type,'issueintel:contextcreated');assert.equal(app.dispatched[0].detail.job_id,'f'.repeat(24));
  assert.equal(app.get('source-context-groq').checked,false);assert.equal(app.get('source-context-submit').disabled,true);
});

test('only the independent consent checkbox enables Groq, and duplicate posts are blocked',async()=>{
  let finish;const app=boot(route=>route==='/api/context-search'?response():new Promise(resolve=>{finish=resolve;}));
  app.show();await app.search();app.choose(0);app.get('use-groq').checked=true;
  app.get('source-context-groq').checked=true;app.get('source-context-depth').value='quick';
  const pending=app.submit('source-context-form');await app.submit('source-context-form');
  assert.equal(app.calls.length,2);assert.equal(app.calls[1][1].provider,'groq');
  assert.equal(app.get('source-context-groq').disabled,true);assert.equal(app.get('source-context-query').disabled,true);
  app.get('source-context-depth').value='deep';assert.equal(app.calls[1][1].depth,'quick');
  finish({id:'f'.repeat(24)});await pending;assert.equal(app.dispatched.length,1);
});

test('late search and creation results cannot replace another viewed run',async()=>{
  let release;const app=boot(()=>new Promise(resolve=>{release=resolve;}));app.show();
  const pending=app.search();app.show('second');release(response());await pending;
  assert.equal(app.checkboxes().length,0);assert.ok(app.get('source-context-parent').textContent.includes('second'));
  assert.equal(app.get('source-context-groq').checked,false);
  let finish;const submitted=boot(route=>route==='/api/context-search'?response():new Promise(resolve=>{finish=resolve;}));
  submitted.show();await submitted.search();submitted.choose(0);const creating=submitted.submit('source-context-form');
  submitted.show('second');finish({id:'f'.repeat(24)});await creating;
  assert.equal(submitted.dispatched.length,0);assert.equal(submitted.checkboxes().length,0);
});

test('mismatched source, query, missing receipt or duplicate keys fail closed',async()=>{
  for(const mutate of [found=>found.job_id='other',found=>found.query='other',found=>delete found.results[0].receipt_sha256,found=>found.results[1].key=found.results[0].key]){
    const found=response();mutate(found);const app=boot(()=>found);app.show();await app.search();
    assert.equal(app.checkboxes().length,0);assert.equal(app.get('source-context-submit').disabled,true);
    assert.match(app.get('source-context-message').textContent,/Chưa tìm được nguồn/);
  }
});

test('a changed source or full queue leaves an actionable refresh message without automatic retry',async()=>{
  const app=boot(route=>{if(route==='/api/context-search')return response();throw Error('Hash mismatch');});app.show();await app.search();app.choose(0);
  app.get('run-button').disabled=true;await app.submit('source-context-form');assert.equal(app.calls.length,1);
  app.get('run-button').disabled=false;await app.submit('source-context-form');assert.equal(app.calls.length,2);
  assert.match(app.get('source-context-message').textContent,/Nguồn có thể đã thay đổi; tìm lại/);
  assert.equal(app.checkboxes().length,0);assert.equal(app.get('source-context-submit').disabled,true);
  await app.submit('source-context-form');assert.equal(app.calls.length,2);
  assert.equal(app.dispatched.length,0);
});

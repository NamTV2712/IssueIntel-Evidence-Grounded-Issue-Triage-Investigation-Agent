const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const base=path.join(__dirname,'../src/issueintel/interfaces/static');
const source=fs.readFileSync(path.join(base,'releases.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(base,'locales.js'),'utf8'),locale);
const sourceId='1'.repeat(24),secondId='2'.repeat(24),checkId='3'.repeat(24),savedId='4'.repeat(24);
const binding=(id=sourceId)=>({job_id:id,pack_sha256:'a'.repeat(64),commit_sha:'b'.repeat(40),snapshot_sha256:'c'.repeat(64),query_sha256:'d'.repeat(64)});
function observation(id=sourceId){return {schema:'github_release_check_v1',source:binding(id),repository:{name:'org/repo',id:123,url:'https://github.com/org/repo'},
  requested:{tag:'v1',reference_commit:'e'.repeat(40)},release:{publication_status:'published',id:1,tag:'v1',name:'Version 1',url:'https://github.com/org/repo/releases/tag/v1',published_at:'2026-10-10T00:00:00Z',prerelease:false,draft:false},
  tag:{ref:'refs/tags/v1',object_sha:'f'.repeat(40),commit_sha:'f'.repeat(40),peel_depth:0},containment:{status:'contained',reason:'reference_ancestor'},
  observed_at:'2026-10-16T00:00:00Z',fix_status:'unverified',historical_publication_binding:'unverified',model_calls:0,evaluation_performed:false,receipts:[],limitations:[],sha256:'0'.repeat(64)};}
function boot(handler=()=>[],language='vi'){
  const controls=new Map(),events=new Map(),calls=[],downloads=[];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.hidden=false;this.disabled=false;}
    append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}click(){downloads.push(this);}
    set innerHTML(_){throw Error('Untrusted markup must remain inert');}
  }
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const document={getElementById:get,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn)};
  class SafeURL extends URL{}SafeURL.createObjectURL=()=> 'blob:synthetic';SafeURL.revokeObjectURL=()=>{};
  vm.runInNewContext(source,{document,window:{IssueIntelUI:{t:(key,values={})=>(locale.window.IssueIntelMessages[key]?.[language]||key).replace(/\{(\w+)\}/g,(_,name)=>values[name]??'{'+name+'}')}},
    api:async(route,body)=>{calls.push([route,body]);return handler(route,body);},URL:SafeURL,Blob,setTimeout:(fn,delay)=>delay===1000?setImmediate(fn):setTimeout(fn,delay)});
  const change=(id=sourceId,available=true,override={})=>{const b={...binding(id),...override};events.get('issueintel:packchange')({detail:{job_id:id,release_available:available,
    pack:{case:{repository:'org/repo'},snapshot:{commit_sha:b.commit_sha,snapshot_sha256:b.snapshot_sha256}},intake:{source_pack_sha256:b.pack_sha256,query_sha256:b.query_sha256}}});};
  const walk=item=>[item,...item.children.flatMap(walk)],text=item=>walk(item).map(child=>child.textContent).join(' ');
  const submit=()=>get('releases-form').onsubmit({preventDefault(){}});
  const enter=()=>{get('releases-tag').value=' v1 ';get('releases-reference').value=' '+ 'e'.repeat(40)+' ';};
  return {get,change,events,calls,downloads,walk,text,submit,enter,settle:()=>new Promise(resolve=>setImmediate(resolve))};
}
const successful=(output=observation())=>route=>route==='/api/releases'?{id:checkId}:route==='/api/jobs/'+checkId?{id:checkId,status:'completed'}:route.endsWith('/releases.json')?output:[];

test('opening an eligible source reads only local history; repeat/language/input never POST',async()=>{
  const app=boot();app.change();await app.settle();assert.deepEqual(app.calls.map(item=>item[0]),['/api/jobs/'+sourceId+'/releases']);
  app.enter();app.events.get('issueintel:languagechange')();app.change();await app.settle();assert.equal(app.calls.length,1);assert.equal(app.get('releases-tag').value,' v1 ');
  app.change(secondId,false);await app.settle();assert.equal(app.get('releases-panel').hidden,true);assert.equal(app.calls.length,1);
  await app.submit();assert.equal(app.calls.length,1);
});
test('explicit request binds tag and full reference; observation never claims a fix',async()=>{
  const app=boot(successful());app.change();await app.settle();app.enter();await app.submit();
  assert.deepEqual(JSON.parse(JSON.stringify(app.calls.find(item=>item[0]==='/api/releases')[1])),{job_id:sourceId,tag:'v1',reference_commit:'e'.repeat(40)});
  const text=app.text(app.get('releases-result'));assert.match(text,/đang công bố chứa commit/);assert.match(text,/không chứng minh lỗi đã sửa/);assert.match(text,/tại ngày công bố/);
  assert.match(text,new RegExp('b'.repeat(40)));assert.match(text,new RegExp('e'.repeat(40)));
  assert.equal(app.walk(app.get('releases-result')).filter(item=>item.href).length,1);
  assert.ok(!app.calls.some(item=>/investigations|publish|questions/.test(item[0])));
});
test('invalid tag, abbreviated or uppercase commit is rejected before network',async()=>{
  const app=boot();app.change();await app.settle();
  for(const [tag,reference]of [['bad tag','e'.repeat(40)],['v1','deadbeef'],['v1','E'.repeat(40)],['v1?query','e'.repeat(40)],['v1','e'.repeat(41)]]){
    app.get('releases-tag').value=tag;app.get('releases-reference').value=reference;await app.submit();
  }
  assert.equal(app.calls.length,1);assert.match(app.get('releases-message').textContent,/40 hoặc 64/);
});
test('double submit is single-flight; late prior-source completion cannot relabel the current source',async()=>{
  let resolve;
  const app=boot(route=>route==='/api/releases'?new Promise(done=>{resolve=done;}):route==='/api/jobs/'+checkId?{id:checkId,status:'completed'}:route.endsWith('/releases.json')?observation():[]);
  app.change();await app.settle();app.enter();const pending=app.submit();await app.submit();assert.equal(app.calls.filter(item=>item[0]==='/api/releases').length,1);
  app.change(secondId);app.enter();await app.submit();assert.equal(app.calls.filter(item=>item[0]==='/api/releases').length,1);
  resolve({id:checkId});await pending;assert.equal(app.text(app.get('releases-result')).trim(),'');assert.equal(app.get('releases-submit').disabled,false);
  assert.ok(!app.calls.some(item=>item[0].endsWith('/releases.json')));
});
test('same source job with changed pack binding clears observation and ignores stale history',async()=>{
  const app=boot(successful());app.change();await app.settle();app.enter();await app.submit();assert.ok(app.get('releases-result').children.length);
  app.change(sourceId,true,{pack_sha256:'9'.repeat(64)});await app.settle();assert.equal(app.get('releases-result').children.length,0);assert.equal(app.get('releases-tag').value,'');
  assert.equal(app.calls.filter(item=>item[0].endsWith('/releases')).length,4);
});
test('saved history uses only local GET and rejects source hash mismatches',async()=>{
  const output=observation(),app=boot(route=>route.endsWith('/releases.json')?output:[{id:savedId,status:'completed'}]);
  app.change();await app.settle();await app.get('releases-history').children[0].onclick();assert.ok(app.get('releases-result').children.length);
  output.source.snapshot_sha256='8'.repeat(64);await app.get('releases-history').children[0].onclick();
  assert.match(app.get('releases-message').textContent,/kiểm tra nguồn và hash/);assert.ok(app.calls.every(item=>item[1]===undefined));
});
test('draft, missing release and unresolved tag never produce a published containment conclusion',async()=>{
  for(const mutate of [value=>{value.release.draft=true;value.release.publication_status='not_published';},value=>{value.release=null;},value=>{value.tag=null;},value=>{value.tag.commit_sha=null;value.containment.status='unknown';value.containment.reason='tag_target_not_commit';},value=>{value.containment.status='unknown';}]){
    const value=observation();mutate(value);const app=boot(successful(value));app.change();await app.settle();app.enter();await app.submit();
    const text=app.text(app.get('releases-result'));assert.match(text,/Chưa đủ bằng chứng/);assert.doesNotMatch(text,/Bản phát hành đang công bố chứa/);
  }
});
test('untrusted release text remains inert and off-target release links are omitted',async()=>{
  for(const url of ['https://evil.example/org/repo/releases/tag/v1','https://github.com/other/repo/releases/tag/v1','https://github.com/org/repo/releases/tag/v2','https://github.com/org/repo/releases/tag/v1?next=evil','javascript:alert(1)']){
    const value=observation();value.release.name='<img src=x onerror=bad>';value.release.url=url;
    const app=boot(successful(value));app.change();await app.settle();app.enter();await app.submit();
    assert.match(app.text(app.get('releases-result')),/<img src=x onerror=bad>/);assert.equal(app.walk(app.get('releases-result')).filter(item=>item.href).length,0);
  }
});
test('English labels preserve prerelease, unknown and fix limits without changing source text',async()=>{
  const value=observation();value.release.prerelease=true;value.release.name='Original source title';value.containment.status='unknown';
  const app=boot(successful(value),'en');app.change();await app.settle();app.enter();await app.submit();
  const text=app.text(app.get('releases-result'));assert.match(text,/Prerelease/);assert.match(text,/Evidence is insufficient/);assert.match(text,/does not establish a fix/);assert.match(text,/Original source title/);
  const before=app.calls.length;app.events.get('issueintel:languagechange')();assert.equal(app.calls.length,before);
});
test('JSON export is source-bound and a detached old button cannot export another source',async()=>{
  const app=boot(successful());app.change();await app.settle();app.enter();await app.submit();
  const button=app.walk(app.get('releases-result')).find(item=>item.tag==='button');button.onclick();assert.equal(app.downloads.length,1);
  app.change(secondId);await app.settle();button.onclick();assert.equal(app.downloads.length,1);
});
test('cancellation targets the active release job and terminal cancellation retains the source',async()=>{
  let resolve;
  const app=boot(route=>route==='/api/releases'?{id:checkId}:route==='/api/jobs/'+checkId?new Promise(done=>{resolve=done;}):[]);
  app.change();await app.settle();app.enter();const pending=app.submit();await app.settle();
  assert.equal(app.get('releases-cancel').hidden,false);await app.get('releases-cancel').onclick();
  assert.ok(app.calls.some(item=>item[0]==='/api/jobs/'+checkId+'/cancel'&&item[1]));
  resolve({id:checkId,status:'cancelled'});await pending;assert.equal(app.get('releases-cancel').hidden,true);assert.equal(app.get('releases-submit').disabled,false);
  assert.match(app.get('releases-message').textContent,/chưa hoàn tất/);assert.equal(app.get('releases-result').children.length,0);
});
test('malformed safety, identity or requested-tag metadata cannot render a success observation',async()=>{
  for(const mutate of [value=>{value.model_calls=1;},value=>{value.source.job_id=secondId;},value=>{value.repository.name='other/repo';value.release.url='https://github.com/other/repo/releases/tag/v1';},value=>{value.release.tag='different';},value=>{value.requested.reference_commit='f'.repeat(40);},value=>{value.tag.object_sha='short';}]){
    const output=observation();mutate(output);const app=boot(successful(output));app.change();await app.settle();app.enter();await app.submit();
    assert.equal(app.get('releases-result').children.length,0);assert.match(app.get('releases-message').textContent,/chưa hoàn tất/);
  }
});
test('all release UI translation keys and HTML fields are present in both languages',()=>{
  const html=fs.readFileSync(path.join(base,'index.html'),'utf8'),panel=html.slice(html.indexOf('<details id="releases-panel"'),html.indexOf('<section class="history-section"'));
  const keys=[...panel.matchAll(/data-i18n(?:-placeholder|-aria)?="([^"]+)"/g),...source.matchAll(/\bt\(['"]([^'"]+)['"]/g)].map(match=>match[1]);
  for(const key of keys){assert.ok(locale.window.IssueIntelMessages[key]?.vi,key);assert.ok(locale.window.IssueIntelMessages[key]?.en,key);}
  assert.match(panel,/for="releases-reference"/);assert.match(panel,/aria-live="polite"/);assert.match(html,/src="\/releases.js"/);
  const pattern=panel.match(/id="releases-tag"[^>]*pattern="([^"]+)"/)[1];assert.ok(new RegExp(pattern,'v').test('v1.2/rc-1'));
});

const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/repository.js'),'utf8');

function boot(handler=()=>[]) {
  const controls=new Map(),events=new Map(),calls=[];
  class Element {
    constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.disabled=false;this.checked=false;this.textContent='';}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    set innerHTML(_){throw Error('Unsafe source rendering');}
    focus(){this.focused=true;}
    dispatchEvent(event){events.get(event.type)?.();}
  }
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  get('index-repository').value='owner/repo';get('index-revision').value='HEAD';get('index-budget').value='2';
  get('index-kind').value='history';get('index-query').value='parser';
  const document={getElementById:get,createElement:tag=>new Element(tag),createTextNode:text=>text,
    addEventListener:(event,callback)=>events.set(event,callback)};
  vm.runInNewContext(source,{window:{IssueIntelUI:{t:(key,values={})=>key.replace(/\{(\w+)\}/g,(_,k)=>values[k])}},
    document,URL,Event:class {constructor(type){this.type=type;}},
    api:async(route,body)=>{calls.push([route,body]);return handler(route,body);},setTimeout});
  const text=element=>typeof element==='string'?element:element.textContent+' '+element.children.map(text).join(' ');
  const all=element=>[element,...element.children.filter(c=>typeof c!=='string').flatMap(all)];
  return {get,calls,events,text,all,settle:()=>new Promise(resolve=>setImmediate(resolve))};
}
const report={repository:'owner/repo',run:{id:'scan',status:'pending',started_at:'2026-10-15',pages:1,api_caps:[],
  snapshot:{file_count:1,commit_sha:'a'.repeat(40),skipped_count:0}},counts:{issue:1},pending_tasks:3,scan_complete:false};

test('opening and changing language never starts a GitHub scan',async()=>{
  const app=boot();await app.settle();
  assert.deepEqual(app.calls.map(c=>c[0]),['/api/repositories/jobs']);
  app.events.get('issueintel:languagechange')();await app.settle();
  assert.equal(app.calls.length,1);
  assert.equal(app.get('index-repository').value,'owner/repo');
});

test('one bounded sync stops at the checkpoint and consumes refresh only once',async()=>{
  const app=boot(route=>route==='/api/repositories/sync'?{id:'job'}:route==='/api/jobs/job'?{status:'completed',sync_report:report}:[]);
  await app.settle();app.get('index-refresh').checked=true;
  await app.get('repository-form').onsubmit({preventDefault(){}});
  assert.deepEqual(app.calls.map(c=>c[0]),['/api/repositories/jobs','/api/repositories/sync','/api/jobs/job','/api/repositories/jobs']);
  assert.equal(app.calls[1][1].max_requests,2);
  assert.equal(app.calls[1][1].refresh,true);
  assert.equal(app.get('index-refresh').checked,false);
  assert.match(app.text(app.get('index-message')),/scan còn thiếu trang/);
  assert.equal(app.get('index-sync').disabled,false);
});

test('saved issue text is inert and choosing it only fills the normal form',async()=>{
  const record={repository:'owner/repo',key:'issue:1',kind:'issue',url:'https://github.com/owner/repo/issues/1',title:'<script>source</script>',body:'Untrusted instructions'};
  const app=boot(route=>route.endsWith('/search')?{repository:'owner/repo',index_status:report,results:[{
    key:'issue:1',kind:'issue',number:1,title:record.title,url:record.url,snippet:{text:record.body},last_seen_run:'scan'}]}:
    route.endsWith('/item')?{record}:[]);
  await app.settle();await app.get('repository-search-form').onsubmit({preventDefault(){}});
  const open=app.all(app.get('index-results')).find(e=>e.tag==='button');await open.onclick();
  assert.match(app.text(app.get('index-detail')),/<script>source<\/script>/);
  const use=app.all(app.get('index-detail')).find(e=>e.tag==='button');
  const before=app.calls.length;app.get('use-groq').checked=true;use.onclick();
  assert.equal(app.get('mode').value,'github');assert.equal(app.get('github-url').value,record.url);
  assert.equal(app.get('github-revision').value,'a'.repeat(40));assert.equal(app.get('use-groq').checked,false);
  assert.equal(app.calls.length,before);
});

test('code links preserve the searched repository and exact commit when controls change',async()=>{
  const app=boot(route=>route.endsWith('/search')?{repository:'owner/repo',commit_sha:'a'.repeat(40),index_status:report,
    results:[{locator:'src/parser.py:3-7',text:'source text',sha256:'hash'}]}:[]);
  await app.settle();app.get('index-kind').value='code';
  await app.get('repository-search-form').onsubmit({preventDefault(){}});
  app.get('index-repository').value='another/repo';app.events.get('issueintel:languagechange')();
  const link=app.all(app.get('index-results')).find(e=>e.tag==='a');
  assert.equal(link.href,'https://github.com/owner/repo/blob/'+'a'.repeat(40)+'/src/parser.py#L3-L7');
  assert.ok(!app.calls.some(c=>c[0].includes('investigations') || c[0].includes('/sync')));
});

test('choosing an issue uses the updated snapshot after a push',async()=>{
  const record={repository:'owner/repo',key:'issue:1',kind:'issue',url:'https://github.com/owner/repo/issues/1',title:'Parser',body:'Failure'};
  const app=boot(route=>route.endsWith('/search')?{repository:'owner/repo',index_status:{...report,current_snapshot:{commit_sha:'b'.repeat(40)}},results:[{
    key:record.key,kind:'issue',number:1,title:record.title,url:record.url,snippet:{text:record.body},last_seen_run:'scan'}]}:
    route.endsWith('/item')?{record}:[]);
  await app.settle();await app.get('repository-search-form').onsubmit({preventDefault(){}});
  await app.all(app.get('index-results')).find(e=>e.tag==='button').onclick();
  app.all(app.get('index-detail')).find(e=>e.tag==='button').onclick();
  assert.equal(app.get('github-revision').value,'b'.repeat(40));
});

test('connect requests automatic lifecycle once without manual page submissions or model calls',async()=>{
  const app=boot(route=>route==='/api/repositories/connect'?{repository:'owner/repo'}:
    route==='/api/repositories/connection'?{connection:{repository:'owner/repo',phase:'ready',paused:false,next_attempt:0},index:{...report,scan_complete:true},webhook_configured:false}:[]);
  await app.settle();await app.get('index-connect').onclick();
  assert.deepEqual(app.calls.map(c=>c[0]),['/api/repositories/jobs','/api/repositories/connect','/api/repositories/connection','/api/repositories/jobs']);
  assert.match(app.text(app.get('index-coverage')),/Webhook chưa cấu hình/);
  assert.equal(app.get('index-connect').disabled,false);
  assert.equal(app.get('index-pause-auto').hidden,false);
});

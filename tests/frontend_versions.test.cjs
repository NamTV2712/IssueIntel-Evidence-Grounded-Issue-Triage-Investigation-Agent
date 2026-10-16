const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/versions.js'),'utf8');
const localeContext={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/locales.js'),'utf8'),localeContext);

function boot(handler=()=>[],language='vi'){
  const controls=new Map(), events=new Map(), calls=[];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.hidden=false;}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    set innerHTML(_){throw Error('Source HTML must remain inert');}
  }
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const document={getElementById:get,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn)};
  vm.runInNewContext(source,{document,window:{IssueIntelUI:{t:(key,values={})=>(localeContext.window.IssueIntelMessages[key]?.[language]||key).replace(/\{(\w+)\}/g,(_,k)=>values[k])}},
    api:async(route,body)=>{calls.push([route,body]);return handler(route,body);},setTimeout});
  const change=(id,available=true)=>events.get('issueintel:packchange')({detail:{job_id:id,available}});
  const text=e=>e.textContent+' '+e.children.map(text).join(' ');
  return {get,calls,change,text,events,settle:()=>new Promise(resolve=>setImmediate(resolve))};
}
function report(id){return {source:{job_id:id},before:{commit_sha:'a'.repeat(40)},after:{commit_sha:'b'.repeat(40)},
  counts:{modified:1},before_skipped:0,after_skipped:0,files_omitted:0,sha256:'hash',
  files:[{path:'<script>parser.py</script>',status:'modified',in_source_evidence:true,diff:{text:'<img src=x onerror=bad>'}}]};}

test('opening a product pack reads local history only; benchmark packs remain hidden',async()=>{
  const app=boot();app.change('product');await app.settle();
  assert.deepEqual(app.calls.map(c=>c[0]),['/api/jobs/product/versions']);
  app.events.get('issueintel:languagechange')();app.change('product');await app.settle();
  assert.equal(app.calls.length,1);
  app.change('strict',false);await app.settle();
  assert.equal(app.get('versions-panel').hidden,true);assert.equal(app.calls.length,1);
});

test('explicit comparison binds source and target, renders inert source and makes no investigation call',async()=>{
  const app=boot(route=>route==='/api/versions'?{id:'comparison'}:route==='/api/jobs/comparison'?{status:'completed'}:
    route.endsWith('versions.json')?report('product'):[]);
  app.change('product');await app.settle();app.get('versions-revision').value='v2';
  await app.get('versions-form').onsubmit({preventDefault(){}});
  assert.equal(app.calls.find(c=>c[0]==='/api/versions')[1].job_id,'product');
  assert.equal(app.calls.find(c=>c[0]==='/api/versions')[1].revision,'v2');
  assert.equal('reference_revision' in app.calls.find(c=>c[0]==='/api/versions')[1],false);
  assert.match(app.text(app.get('versions-result')),/<script>parser.py<\/script>/);
  assert.match(app.text(app.get('versions-result')),/<img src=x onerror=bad>/);
  assert.ok(!app.calls.some(c=>c[0].includes('investigations')||c[0].includes('publish')));
  assert.doesNotMatch(app.text(app.get('versions-result')),/Quan hệ lịch sử Git/);
});

test('late results for a prior pack never replace the current pack view',async()=>{
  let release;
  const app=boot(route=>route==='/api/versions'?{id:'comparison'}:route==='/api/jobs/comparison'?new Promise(resolve=>{release=resolve;}):
    route.endsWith('versions.json')?report('first'):[]);
  app.change('first');await app.settle();app.get('versions-revision').value='HEAD';
  const running=app.get('versions-form').onsubmit({preventDefault(){}});await app.settle();
  app.change('second');release({status:'completed'});await running;
  assert.equal(app.text(app.get('versions-result')).trim(),'');
});

test('opening a saved comparison recovers from a failed attempt without rerunning Git',async()=>{
  const app=boot(route=>route==='/api/versions'?{id:'failed'}:route==='/api/jobs/failed'?{status:'failed'}:
    route.endsWith('versions.json')?report('product'):[{id:'saved',status:'completed'}]);
  app.change('product');await app.settle();app.get('versions-revision').value='missing';
  await app.get('versions-form').onsubmit({preventDefault(){}});
  assert.match(app.get('versions-message').textContent,/chưa hoàn tất/);
  await app.get('versions-history').children[0].onclick();
  assert.equal(app.get('versions-message').textContent,'');
  assert.match(app.text(app.get('versions-result')),/Before/);
  assert.equal(app.calls.filter(c=>c[0]==='/api/versions').length,1);
});

test('reference is optional, trimmed and bound with source and target before asynchronous work',async()=>{
  let release;
  const app=boot(route=>route==='/api/versions'?new Promise(resolve=>{release=resolve;}):
    route==='/api/jobs/comparison'?{status:'completed'}:route.endsWith('versions.json')?report('first'):[]);
  app.change('first');await app.settle();
  app.get('versions-revision').value='  v2  ';app.get('versions-reference').value='  fix-candidate  ';
  const pending=app.get('versions-form').onsubmit({preventDefault(){}});
  app.get('versions-revision').value='other-target';app.change('second');
  app.get('versions-reference').value='different-issue-fix';release({id:'comparison'});await pending;
  assert.deepEqual(JSON.parse(JSON.stringify(app.calls.find(c=>c[0]==='/api/versions')[1])),
    {job_id:'first',revision:'v2',reference_revision:'fix-candidate'});
  assert.equal(app.text(app.get('versions-result')).trim(),'');
});

test('reference is cleared for another pack but retained for language and repeated pack events',async()=>{
  const app=boot();app.change('first');await app.settle();
  app.get('versions-reference').value='candidate';
  app.events.get('issueintel:languagechange')();app.change('first');await app.settle();
  assert.equal(app.get('versions-reference').value,'candidate');assert.equal(app.calls.length,1);
  app.change('second');assert.equal(app.get('versions-reference').value,'');await app.settle();
  assert.equal(app.calls.length,2);
});

test('saved Git relationship and exact reference are readable while tag source remains inert',async()=>{
  const output=report('product');output.git_reachability={
    source_to_target:{status:'source_ancestor',reason:'source_reaches_target',ancestry_complete:true},
    reference:{requested_revision:'<script>fix</script>',commit_sha:'c'.repeat(40),status:'contained',reason:'reference_ancestor'},
    tags:{scope:'local_git_refs',scanned:2,total:2,limit:200,truncated:false,unavailable:[],
      containing:[{name:'<img src=x onerror=bad>',commit_sha:'d'.repeat(40)}]}};
  const app=boot(route=>route==='/api/versions'?{id:'comparison'}:route==='/api/jobs/comparison'?{status:'completed'}:
    route.endsWith('versions.json')?output:[]);
  app.change('product');await app.settle();app.get('versions-revision').value='v2';
  await app.get('versions-form').onsubmit({preventDefault(){}});
  const rendered=app.text(app.get('versions-result'));
  assert.match(rendered,/Commit nguồn là tổ tiên/);assert.match(rendered,/Đích chứa commit tham chiếu/);
  assert.match(rendered,/<script>fix<\/script>/);assert.match(rendered,new RegExp('c'.repeat(40)));
  assert.match(rendered,/<img src=x onerror=bad>/);assert.match(rendered,/Đã quét 2\/2 tag local/);
  assert.match(rendered,/chưa xác minh bản phát hành đã công bố/);
  const before=app.calls.length;app.events.get('issueintel:languagechange')();assert.equal(app.calls.length,before);
});

test('shallow unknown results and capped tag scope never claim a missing commit is excluded',async()=>{
  const output=report('product');output.git_reachability={
    source_to_target:{status:'unknown',reason:'shallow_history',ancestry_complete:false},
    reference:{requested_revision:'candidate',commit_sha:null,status:'unknown',reason:'shallow_history'},
    tags:{scope:'local_git_refs',scanned:200,total:240,limit:200,truncated:true,containing:[],unavailable:['tag:broken']}};
  const app=boot(route=>route==='/api/versions'?{id:'comparison'}:route==='/api/jobs/comparison'?{status:'completed'}:
    route.endsWith('versions.json')?output:[]);
  app.change('product');await app.settle();app.get('versions-revision').value='v2';
  await app.get('versions-form').onsubmit({preventDefault(){}});
  const rendered=app.text(app.get('versions-result'));
  assert.match(rendered,/Chưa xác định được quan hệ/);assert.match(rendered,/Lịch sử Git chưa đầy đủ/);
  assert.match(rendered,/Chưa xác định được đích có chứa/);assert.match(rendered,/Chưa resolve được/);
  assert.match(rendered,/Đã quét 200\/240/);assert.match(rendered,/tag chưa quét vẫn chưa biết/);
  assert.match(rendered,/Có tag không kiểm được/);assert.doesNotMatch(rendered,/Đích không chứa/);
});

test('English saved Git results translate relationship, reason and publication limit',async()=>{
  const output=report('product');output.git_reachability={
    source_to_target:{status:'diverged',reason:'no_ancestor_path',ancestry_complete:true},
    reference:{requested_revision:'candidate',commit_sha:'c'.repeat(40),status:'not_contained',reason:'no_ancestor_path'},
    tags:{scope:'local_git_refs',scanned:0,total:0,limit:200,truncated:false,containing:[],unavailable:[]}};
  const app=boot(route=>route==='/api/versions'?{id:'comparison'}:route==='/api/jobs/comparison'?{status:'completed'}:
    route.endsWith('versions.json')?output:[],'en');
  app.change('product');await app.settle();app.get('versions-revision').value='v2';
  await app.get('versions-form').onsubmit({preventDefault(){}});
  const rendered=app.text(app.get('versions-result'));
  assert.match(rendered,/Source and target have diverged histories/);
  assert.match(rendered,/The target does not contain the reference commit/);
  assert.match(rendered,/No ancestry path exists in the fully checked history/);
  assert.match(rendered,/published releases and whether the issue is fixed remain unverified/);
  assert.doesNotMatch(rendered,/Quan hệ lịch sử|no_ancestor_path/);
});

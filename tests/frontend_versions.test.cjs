const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/versions.js'),'utf8');

function boot(handler=()=>[]){
  const controls=new Map(), events=new Map(), calls=[];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.hidden=false;}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    set innerHTML(_){throw Error('Source HTML must remain inert');}
  }
  const get=id=>{if(!controls.has(id))controls.set(id,new Element());return controls.get(id);};
  const document={getElementById:get,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn)};
  vm.runInNewContext(source,{document,window:{IssueIntelUI:{t:(key,values={})=>key.replace(/\{(\w+)\}/g,(_,k)=>values[k])}},
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
  assert.match(app.text(app.get('versions-result')),/<script>parser.py<\/script>/);
  assert.match(app.text(app.get('versions-result')),/<img src=x onerror=bad>/);
  assert.ok(!app.calls.some(c=>c[0].includes('investigations')||c[0].includes('publish')));
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

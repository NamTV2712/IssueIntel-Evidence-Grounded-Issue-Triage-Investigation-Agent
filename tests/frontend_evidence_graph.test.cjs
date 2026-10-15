const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/evidence-graph.js'),'utf8');

function graph(id){return {source:{job_id:id,pack_sha256:'packhash'},commit_sha:'a'.repeat(40),sha256:'graphhash',omitted_nodes:0,omitted_edges:0,
  nodes:[{id:'q',kind:'query',title:'Query',text:'Issue text',evidence_id:'issue'},
    {id:'h',kind:'hypothesis',title:'<script>Hypothesis</script>',text:'<img src=x onerror=bad>',counter_status:'not_found'},
    {id:'e',kind:'code',title:'src/file.py:1-3',text:'return None',sha256:'hash',revision:'a'.repeat(40),evidence_id:'e1'}],
  edges:[{source:'q',target:'e',relation:'retrieved_context'},{source:'h',target:'e',relation:'claimed_support'}]};}
function boot(handler){
  const calls=[],events=[];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.attrs={};this.value='';this.dataset={};}
    append(...children){for(const child of children){child.remove?.();child.parent=this;this.children.push(child);}}
    replaceChildren(...children){this.children=children;}
    setAttribute(key,value){this.attrs[key]=value;}
    getBoundingClientRect(){return {width:0,height:0};}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null;}
    focus(){this.focused=true;}
    showModal(){this.open=true;}
    close(){this.open=false;this.onclose?.();}
    set innerHTML(_){throw Error('Source text cannot become HTML');}
  }
  const document={body:new Element('body'),createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),dispatchEvent:event=>events.push(event)};
  const context={document,window:{IssueIntelUI:{t:(key,values={})=>key.replace(/\{(\w+)\}/g,(_,k)=>values[k])}},
    requestAnimationFrame:()=>1,cancelAnimationFrame:()=>{},api:async route=>{calls.push(route);return handler(route);},setTimeout,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}};
  vm.runInNewContext(source,context);
  const all=e=>[e,...e.children.flatMap(all)],text=e=>e.textContent+' '+e.children.map(text).join(' ');
  return {root:new Element(),body:document.body,calls,events,all,text,render:context.window.IssueIntelEvidenceGraph.render,cancel:context.window.IssueIntelEvidenceGraph.cancel};
}

test('keyboard graph selection keeps source text inert and opens a source-bound citation',async()=>{
  const app=boot(()=>graph('job'));await app.render(app.root,'job');
  const nodes=app.all(app.root).filter(e=>e.tag==='button'&&e.className?.startsWith('eg-node '));
  let prevented=false;nodes.find(e=>e.attrs['aria-label'].includes('Hypothesis')).onkeydown({key:'Enter',preventDefault(){prevented=true;}});
  assert.ok(prevented);assert.match(app.text(app.root),/<img src=x onerror=bad>/);
  const code=nodes.find(e=>e.attrs['aria-label'].includes('src/file.py'));
  code.onclick();assert.equal(app.all(app.root).find(e=>e.className?.startsWith('eg-node ')&&e.attrs['aria-label'].includes('src/file.py')).attrs['aria-pressed'],'true');
  assert.match(app.text(app.root),/claimed_support ←/);
  const open=app.all(app.root).find(e=>e.tag==='button'&&e.textContent==='Mở citation trong báo cáo');open.onclick();
  assert.equal(app.events[0].detail.job_id,'job');assert.equal(app.events[0].detail.evidence_id,'e1');
  await app.render(app.root,'job');assert.deepEqual(app.calls,['/api/jobs/job/evidence-graph']);
});

test('relation filter hides other edges without inventing counter-evidence',async()=>{
  const app=boot(()=>graph('job'));await app.render(app.root,'job');
  const filter=app.all(app.root).find(e=>e.tag==='select');filter.value='claimed_counter';filter.onchange();
  assert.match(app.text(app.root),/Đang xem 1 nút · 0 liên kết/);
  const node=app.all(app.root).find(e=>e.tag==='button'&&e.className?.startsWith('eg-node ')&&e.attrs['aria-label'].includes('Hypothesis'));node.onclick();
  assert.match(app.text(app.root),/not_found/);
});

test('a late graph response never replaces the currently selected job',async()=>{
  let release;const app=boot(route=>route.includes('/first/')?new Promise(resolve=>{release=resolve;}):graph('second'));
  const first=app.render(app.root,'first');await app.render(app.root,'second');release(graph('first'));await first;
  assert.match(app.text(app.root),/packhash/);
  const node=app.all(app.root).find(e=>e.tag==='button'&&e.className?.startsWith('eg-node ')&&e.attrs['aria-label'].includes('src/file.py'));node.onclick();
  app.all(app.root).find(e=>e.tag==='button'&&e.textContent==='Mở citation trong báo cáo').onclick();
  assert.equal(app.events[0].detail.job_id,'second');
});

test('mismatched graph source fails closed and a fresh explicit open can retry',async()=>{
  let count=0;const app=boot(()=>graph(++count===1?'wrong':'job'));
  await app.render(app.root,'job');assert.match(app.text(app.root),/Không đọc được đồ thị/);
  await app.render(app.root,'job');assert.equal(app.calls.length,2);
  assert.ok(app.all(app.root).some(e=>e.tag==='svg'));
});

test('switching to another tab prevents a pending graph from overwriting its content',async()=>{
  let release;const app=boot(()=>new Promise(resolve=>{release=resolve;}));
  const pending=app.render(app.root,'job');app.cancel();app.root.replaceChildren();app.root.textContent='Other tab';
  release(graph('job'));await pending;
  assert.equal(app.text(app.root).trim(),'Other tab');
});

test('expanded view reuses the same selected graph and closes without another API call',async()=>{
  const app=boot(()=>graph('job'));await app.render(app.root,'job');
  const expand=app.all(app.root).find(e=>e.tag==='button'&&e.textContent==='Phóng rộng');expand.onclick();
  const dialog=app.body.children.find(e=>e.tag==='dialog');assert.ok(dialog.open);
  assert.match(app.text(dialog),/Đang xem 2 nút · 1 liên kết/);
  expand.onclick();assert.equal(app.body.children.length,0);assert.ok(expand.focused);
  assert.equal(app.calls.length,1);
});

const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function boot(){
  class Element{constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}set innerHTML(_){throw Error('Accounting text must stay inert');}}
  const context={window:{IssueIntelUI:{t:(key,values={})=>key.replace(/\{(\w+)\}/g,(_,k)=>values[k])}},document:{createElement:tag=>new Element(tag)}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/usage.js'),'utf8'),context);
  const text=e=>e.textContent+' '+e.children.map(text).join(' ');
  return {root:new Element(),render:context.window.IssueIntelUsage.render,text};
}
test('legacy missing receipts stay unknown and are never rendered as zero cost',()=>{
  const app=boot();app.render(app.root,{job_id:'old',tracking_available:false});
  assert.match(app.text(app.root),/chi phí chưa biết/);assert.ok(!app.text(app.root).includes('$0'));
});
test('offline accounting shows verified zero and does not require an API',()=>{
  const app=boot();app.render(app.root,{job_id:'offline',tracking_available:true,reservations:0,tokens_charged_or_reserved:0,usd_charged_or_reserved:'0',retained_bounds:0,receipts:[]});
  assert.match(app.text(app.root),/\$0/);assert.match(app.text(app.root),/offline/);
});
test('retained bounds remain explicit and receipt content is inert',()=>{
  const app=boot();app.render(app.root,{job_id:'failed',tracking_available:true,reservations:1,tokens_charged_or_reserved:4000,usd_charged_or_reserved:'0.002',retained_bounds:1,receipts:[{stage:'verification',status:'failed_bound',model:'<script>model</script>',input_bound:2000,output_bound:2000,price_date:'2026-10-15'}]});
  assert.match(app.text(app.root),/1 lượt còn giữ cận trên/);assert.match(app.text(app.root),/<script>model<\/script>/);
});

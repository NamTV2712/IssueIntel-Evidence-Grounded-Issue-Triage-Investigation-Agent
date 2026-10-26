const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const base = path.join(__dirname, '../src/issueintel/interfaces/static');

function boot({stored=null, blocked=false, dark=false, loadedCatalog=null}={}) {
  const events = new Map(), controls = new Map(), writes = [];
  const label = {dataset:{i18n:'Bằng chứng'},textContent:''};
  const source = {textContent:'Bằng chứng'}; // Source text has no i18n marker.
  const catalogStatus={dataset:{i18n:'Đang đọc dữ liệu cục bộ…'},textContent:'Đang đọc dữ liệu cục bộ…'};
  const acceptedCount={textContent:'—'};
  controls.set('data-status',catalogStatus);controls.set('accepted-count',acceptedCount);
  for(const id of ['language','theme'])controls.set(id,{value:'',addEventListener:(event,fn)=>events.set(id,fn)});
  const media={matches:dark,addEventListener:(event,fn)=>events.set('media',fn)};
  const document={documentElement:{dataset:{}},
    querySelectorAll:selector=>selector==='[data-i18n]'?[label,catalogStatus].filter(el=>el.dataset.i18n):[],
    getElementById:id=>controls.get(id),addEventListener:(event,fn)=>events.set(event,fn),
    dispatchEvent:event=>events.get(event.type)?.()};
  const context=vm.createContext({window:{matchMedia:()=>media},document,navigator:{language:'vi-VN'},
    localStorage:{getItem:()=>{if(blocked)throw Error('disabled');return stored;},
      setItem:(key,value)=>{if(blocked)throw Error('disabled');writes.push([key,value]);}},Event:class {constructor(type){this.type=type;}}});
  for(const file of ['locales.js','preferences.js'])vm.runInContext(fs.readFileSync(path.join(base,file),'utf8'),context);
  if(loadedCatalog){
    const app=fs.readFileSync(path.join(base,'app.js'),'utf8'),start=app.indexOf('function renderCatalog() {');
    context.catalogInfo=loadedCatalog;context.$=id=>controls.get(id);context.t=context.window.IssueIntelUI.t;
    vm.runInContext(app.slice(start,app.indexOf("document.addEventListener('issueintel:languagechange'",start)),context);
    context.renderCatalog();
    document.addEventListener('issueintel:languagechange',context.renderCatalog);
  }
  events.get('DOMContentLoaded')();
  return {context,document,label,source,media,writes,catalogStatus,acceptedCount,
    change:(id,value)=>events.get(id)({target:{value}}),mediaChange:()=>events.get('media')()};
}

test('language updates authored UI only and saves no source data or token',()=>{
  const app=boot();let changes=0;
  app.document.addEventListener('issueintel:languagechange',()=>changes++);
  app.change('language','en');
  assert.equal(app.document.documentElement.lang,'en');
  assert.equal(app.label.textContent,'Evidence');assert.equal(app.source.textContent,'Bằng chứng');
  assert.equal(changes,1);
  assert.deepEqual(JSON.parse(app.writes[0][1]),{language:'en',theme:'system'});
  assert.equal(app.context.window.IssueIntelUI.t('{count} nguồn',{count:12}),'12 sources');
});

test('language bootstrap cannot replace an already loaded catalog status with the loading placeholder',()=>{
  for(const loadedCatalog of [{accepted:0,original_total:0,pending:0},{accepted:384,original_total:400,pending:16}]){
    const app=boot({loadedCatalog});
    const expected=loadedCatalog.original_total?'16 pending · Holdout giữ riêng':'Chưa có dataset cục bộ';
    assert.equal(app.catalogStatus.textContent,expected);
    assert.equal(app.acceptedCount.textContent,loadedCatalog.accepted);
    app.change('language','en');
    assert.equal(app.catalogStatus.textContent,app.context.window.IssueIntelUI.t(
      loadedCatalog.original_total?'{count} pending · Holdout giữ riêng':'Chưa có dataset cục bộ',{count:loadedCatalog.pending}));
  }
});
test('saved explicit theme beats the OS, system theme follows changes',()=>{
  const app=boot({stored:JSON.stringify({language:'en',theme:'light'}),dark:true});
  assert.equal(app.document.documentElement.dataset.theme,'light');
  app.mediaChange();assert.equal(app.document.documentElement.dataset.theme,'light');
  app.change('theme','system');assert.equal(app.document.documentElement.dataset.theme,'dark');
  app.media.matches=false;app.mediaChange();assert.equal(app.document.documentElement.dataset.theme,'light');
});
test('blocked or malformed storage leaves preferences usable',()=>{
  for(const options of [{blocked:true},{stored:'invalid'},{stored:'{"language":"xx","theme":"bad"}'}]){
    const app=boot(options);app.change('theme','dark');app.change('language','en');
    assert.equal(app.document.documentElement.dataset.theme,'dark');assert.equal(app.label.textContent,'Evidence');
    app.change('theme','invalid');assert.equal(app.document.documentElement.dataset.theme,'dark');
  }
});
test('all authored HTML and JS translation keys have complete matching templates',()=>{
  const messages=boot().context.window.IssueIntelMessages;
  const html=fs.readFileSync(path.join(base,'index.html'),'utf8');
  const js=['app.js','repository.js','versions.js','evidence-graph.js','usage.js','resume.js','source-context.js','clarifications.js','adaptive-questions.js','report.js','releases.js','workflow.js','hypothesis-review.js','check-journal.js'].map(file=>fs.readFileSync(path.join(base,file),'utf8')).join('\n');
  const keys=[...html.matchAll(/data-i18n(?:-placeholder|-aria)?="([^"]+)"/g),...js.matchAll(/\bt\(['"]([^'"]+)['"]/g)].map(match=>match[1]);
  for(const key of keys)assert.ok(messages[key],`Missing message: ${key}`);
  for(const [key,value] of Object.entries(messages)){
    assert.ok(value.en&&value.vi,key);
    const slots=text=>[...text.matchAll(/\{([a-z_]+)\}/g)].map(m=>m[1]).sort();
    assert.deepEqual(slots(value.vi),slots(value.en),key);
  }
});

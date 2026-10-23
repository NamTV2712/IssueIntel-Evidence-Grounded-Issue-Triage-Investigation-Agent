const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const code=fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/app.js'),'utf8');
const id=c=>c.repeat(24),hash=c=>c.repeat(64);
function boot(){
  const nodes=new Map(),events=new Map(),requests=[];
  const all=node=>[node,...node.children.flatMap(all)];
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.value='';this.checked=false;this.disabled=false;this.classList={toggle(){}};}
    append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
    querySelector(tag){return all(this).find(item=>item.tag===tag);}
    setAttribute(){}set innerHTML(_){throw Error('Unsafe source HTML');}
  }
  const get=key=>{if(!nodes.has(key))nodes.set(key,new Element());return nodes.get(key);};
  const document={getElementById:get,createElement:tag=>new Element(tag),querySelector:()=>({content:'synthetic-token'}),
    querySelectorAll:query=>query==='[data-plan-step]'?all(get('tab-content')).filter(item=>item.dataset.planStep):[],
    addEventListener:(name,fn)=>events.set(name,fn)};
  const context=vm.createContext({document,window:{IssueIntelUI:{t:key=>key}},requests,input:null,
    node:(tag,text,cls)=>Object.assign(new Element(tag),{textContent:text,className:cls}),recommendedText:text=>text,
    hypothesisReviewSource:()=>({}),evidenceCard:()=>new Element(),runJob:request=>requests.push(request),message(){},
    updateDepthControls(){},filterCases(){},renderCatalog(){},renderHistory(){},renderComparison(){}});
  const initial=code.slice(0,code.indexOf('async function api('));
  const functions=code.slice(code.indexOf('function planDrafts()'),code.indexOf('async function history('));
  vm.runInContext(initial+functions,context);
  context.renderPack=()=>context.renderTab();
  const followup=code.split('\n').find(line=>line.startsWith("$('followup-form').onsubmit="));
  vm.runInContext(followup+code.slice(code.lastIndexOf("document.addEventListener('issueintel:languagechange'")),context);
  const run=text=>vm.runInContext(text,context);
  const select=(job='a',pack='b')=>{context.input={id:id(job),sha:hash(pack)};
    run("currentJob=input.id;reportView={source:{pack_sha256:input.sha}};pack={annotation:null,evidence:[]};planData={revision:0,steps:[{id:'step_1',description:'Inspect branch',status:'todo',note:''}],history:[]};bindFollowupDraft();renderTab();");};
  const tab=name=>{context.input=name;run('activeTab=input;renderTab();');};
  const form=()=>document.querySelectorAll('[data-plan-step]')[0];
  return {context,run,select,tab,events,get,requests,form,
    checklist:()=>all(get('tab-content')).find(item=>item.className==='journal-checklist'),
    note:()=>form().querySelector('textarea'),status:()=>form().querySelector('select'),
    followup:()=>get('followup-form').onsubmit({preventDefault(){}})};
}

test('unsaved checklist status and note survive tabs, language rerenders and exact-source return',()=>{
  const app=boot();app.select();app.tab('plan');app.note().value='A unsaved note';app.status().value='blocked';
  app.tab('evidence');app.tab('plan');assert.equal(app.note().value,'A unsaved note');assert.equal(app.status().value,'blocked');
  app.events.get('issueintel:languagechange')();assert.equal(app.note().value,'A unsaved note');
  app.select('c','d');assert.equal(app.note().value,'');app.note().value='B unsaved note';
  app.select();assert.equal(app.note().value,'A unsaved note');app.select('c','d');assert.equal(app.note().value,'B unsaved note');
});

test('checklist draft is not stashed under a newly selected source and a changed pack gets no old draft',()=>{
  const app=boot();app.select();app.tab('plan');app.note().value='Only A pack b';
  app.select('a','e');assert.equal(app.note().value,'');app.select();assert.equal(app.note().value,'Only A pack b');
  app.run("planData.steps[0].note='Saved server value';renderTab('step_1');");assert.equal(app.note().value,'Saved server value');
  app.tab('evidence');app.tab('plan');assert.equal(app.note().value,'Saved server value');
});

test('recovering an unsaved checklist draft opens its editor while a clean different source stays collapsed',()=>{
  const app=boot();app.select();app.tab('plan');assert.equal(app.checklist().open,false);
  app.checklist().open=true;app.note().value='Keep the recovered editor visible';
  app.tab('evidence');app.tab('plan');assert.equal(app.checklist().open,true);assert.equal(app.note().value,'Keep the recovered editor visible');
  app.events.get('issueintel:languagechange')();assert.equal(app.checklist().open,true);
  app.select('c','d');assert.equal(app.checklist().open,false);
  app.select();assert.equal(app.checklist().open,true);assert.equal(app.note().value,'Keep the recovered editor visible');
});

test('followup text, depth and explicit Groq choice stay with exact source job and pack',()=>{
  const app=boot();app.select();app.get('additional-context').value='Only A context';app.get('followup-depth').value='deep';app.get('followup-groq').checked=true;
  app.select('c','d');assert.equal(app.get('additional-context').value,'');assert.equal(app.get('followup-depth').value,'quick');assert.equal(app.get('followup-groq').checked,false);
  app.get('additional-context').value='Only B context';app.followup();assert.equal(app.requests.at(-1).parent_job_id,id('c'));assert.equal(app.requests.at(-1).provider,'none');
  app.select();assert.equal(app.get('additional-context').value,'Only A context');assert.equal(app.get('followup-depth').value,'deep');assert.equal(app.get('followup-groq').checked,true);
  app.followup();assert.equal(app.requests.at(-1).parent_job_id,id('a'));assert.equal(app.requests.at(-1).additional_context,'Only A context');assert.equal(app.requests.at(-1).provider,'groq');
  app.select('a','e');assert.equal(app.get('additional-context').value,'');assert.equal(app.get('followup-groq').checked,false);
});

test('completed followup clears only the submitted unchanged draft of its own source',()=>{
  const app=boot();app.select();app.get('additional-context').value='Sent A';app.followup();
  app.select('c','d');app.get('additional-context').value='B still editing';
  app.run("completeFollowupDraft('"+id('a')+':'+hash('b')+"',{additional_context:'Sent A'});");
  assert.equal(app.get('additional-context').value,'B still editing');app.select();assert.equal(app.get('additional-context').value,'');
  app.get('additional-context').value='Newer A edit';app.run("completeFollowupDraft('"+id('a')+':'+hash('b')+"',{additional_context:'Sent A'});");
  assert.equal(app.get('additional-context').value,'Newer A edit');
});

test('missing report can use exact intake pack binding, while missing all pack identity cannot submit or share drafts',()=>{
  const app=boot();app.select();app.get('additional-context').value='Only original A';
  app.run("reportView=null;intake={source_pack_sha256:'"+hash('b')+"'};bindFollowupDraft();");
  assert.equal(app.get('additional-context').value,'Only original A');app.followup();assert.equal(app.requests.length,1);
  app.run("currentJob='"+id('c')+"';intake=null;bindFollowupDraft();");assert.equal(app.get('additional-context').value,'');
  app.get('additional-context').value='Unknown pack';app.followup();assert.equal(app.requests.length,1);
  app.run("currentJob='"+id('d')+"';bindFollowupDraft();");assert.equal(app.get('additional-context').value,'');
});

test('an old running task cannot overwrite a new view with progress, failure, error or history errors',async()=>{
  const start=code.indexOf('async function runJob('),end=code.indexOf('async function openJob(',start);
  for(const failure of ['terminal','throw']){
    const logs=[],controls=new Map(),get=key=>{if(!controls.has(key))controls.set(key,{disabled:false,hidden:false});return controls.get(key);};
    let polls=0;const context=vm.createContext({window:{},$:get,viewGeneration:0,message:value=>logs.push(value),t:key=>key,stageView:null,activeJob:null,
      api:async()=>{polls++;context.viewGeneration=1;if(failure==='throw')throw Error('Old network error');return polls===1?{status:'running'}:{status:'failed'};},
      showUsage(){throw Error('Stale usage');},showStageArtifacts(){throw Error('Stale stages');},openJob(){throw Error('Stale navigation');},
      history:async showErrors=>{assert.equal(showErrors,false);},setTimeout:fn=>fn()});
    vm.runInContext(code.slice(start,end),context);await context.runJob({mode:'stage_resume'},{id:id('a')});
    assert.equal(logs.length,1,'Only the original pre-navigation message is allowed');assert.equal(get('run-button').disabled,false);
  }
});

test('opening a saved report names the selected source while another task keeps running',async()=>{
  const start=code.indexOf('async function openJob('),end=code.indexOf('function renderPack(',start),logs=[];
  const context=vm.createContext({window:{},viewGeneration:0,activeJob:id('c'),currentJob:null,
    api:async route=>route.endsWith('/pack.json')?{stage_checkpoints:{}}:{},readHypothesisReview:async()=>null,readCheckJournal:async()=>null,
    message:value=>logs.push(value),t:(key,slots)=>key.replace('{id}',slots.id),savedTypesafeUsage:()=>({}),showUsage(){},showStageArtifacts(){},renderPack(){}});
  vm.runInContext(code.slice(start,end),context);await context.openJob(id('a'));
  assert.match(logs.at(-1),new RegExp(id('a')));assert.equal(context.currentJob,id('a'));assert.equal(context.activeJob,id('c'));
});

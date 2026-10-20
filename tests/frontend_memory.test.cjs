const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const dir=path.join(__dirname,'../src/issueintel/interfaces/static'),code=fs.readFileSync(path.join(dir,'memory.js'),'utf8');
const locale={window:{}};vm.runInNewContext(fs.readFileSync(path.join(dir,'locales.js'),'utf8'),locale);const messages=locale.window.IssueIntelMessages;
const hash=c=>c.repeat(64),job=c=>c.repeat(24),clone=v=>JSON.parse(JSON.stringify(v));
function fixture(){
  const source={source_job_id:job('a'),source_pack_sha256:hash('1'),repository:'synthetic/repo',commit_sha:'2'.repeat(40),snapshot_sha256:hash('3')};
  const pack={case:{repository:source.repository},snapshot:{commit_sha:source.commit_sha,snapshot_sha256:source.snapshot_sha256},
    annotation:{hypotheses:[{id:'H1',statement:'An input guard might be missing'},{id:'H2',statement:'The caller might pass a sentinel'}]}};
  const memory={schema:'repository_architecture_memory_v1',sha256:hash('4'),runtime_verified:false,causal_verified:false,facts_omitted:0,
    source:{repository:'local/synthetic',commit_sha:'5'.repeat(40),snapshot_sha256:hash('6')},facts:[{id:'memory_fact',sha256:hash('7'),kind:'definition',
      source:{path:'parser.py',qualname:'parse',start_line:1,end_line:2},target:null}]};
  const receipt={id:'capture_'+'8'.repeat(32),sha256:hash('9'),repository:source.repository,kind:'architecture',memory_sha256:memory.sha256,
    source_job_id:job('b'),source_pack_sha256:hash('a'),snapshot_sha256:memory.source.snapshot_sha256,recorded_at:'2026-10-20T00:00:00Z'};
  const opened={source,receipt,memory,model_calls:0,selectable_items:[{id:'memory_fact',item_sha256:hash('7')}]};
  return {source,pack,opened,status:{...source,available:true,can_capture_lesson:true,can_preview:true,model_calls:0}};
}
function boot(handler,value=fixture()){
  const calls=[],events=[],listeners=new Map(),all=n=>[n,...n.children.flatMap(all)],text=n=>n.textContent+' '+n.children.map(text).join(' ');
  class El{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.value='';this.attributes={};}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}setAttribute(k,v){this.attributes[k]=v;}set innerHTML(_){throw Error('Unsafe source markup');}}
  const document={documentElement:{lang:'vi'},createElement:tag=>new El(tag),addEventListener:(name,fn)=>listeners.set(name,fn),dispatchEvent:event=>events.push(event)};
  const window={IssueIntelUI:{t:(key,slots={})=>{assert.ok(messages[key]?.[document.documentElement.lang],'Missing translation '+key);return messages[key][document.documentElement.lang].replace(/\{(\w+)\}/g,(_,name)=>slots[name]);}}};
  async function normal(route,body){
    if(route.endsWith('memory-status'))return clone(value.status);
    if(route.endsWith('memory-search'))return {schema:'repository_memory_list_v1',source:clone(value.source),repository:value.source.repository,query:body.query,model_calls:0,
      results:[{id:value.opened.receipt.id,receipt_sha256:value.opened.receipt.sha256,kind:value.opened.receipt.kind,source:clone(value.opened.memory.source),retained_count:1}]};
    if(route.endsWith('memory-open'))return clone(value.opened);
    if(route.endsWith('memory-capture')){
      const saved=clone(value.opened);saved.receipt.source_job_id=value.source.source_job_id;saved.receipt.source_pack_sha256=value.source.source_pack_sha256;
      saved.memory.source.commit_sha=value.source.commit_sha;saved.memory.source.snapshot_sha256=value.source.snapshot_sha256;saved.receipt.snapshot_sha256=value.source.snapshot_sha256;
      if(body.kind==='lesson'){
        saved.receipt.kind='investigation_lesson';saved.memory={schema:'repository_investigation_lesson_v1',sha256:saved.receipt.memory_sha256,
          source:saved.memory.source,runtime_verified:false,causal_verified:false,historical_eligible:false,independent_human:false,
          truth_level:'unverified_prior_investigation',issue_excerpt:'Prior issue',author_note:body.note,evidence:[],
          hypotheses:body.hypothesis_ids.map(id=>({id,statement:'Prior hypothesis '+id,falsification_test:'Inspect input'}))};
        saved.selectable_items=saved.memory.hypotheses.map(h=>({id:h.id,item_sha256:hash('7')}));
      }
      return saved;
    }
    if(route.endsWith('memory-assess'))return {source:clone(value.source),capture_id:value.opened.receipt.id,receipt_sha256:value.opened.receipt.sha256,model_calls:0,
      assessment:{schema:'repository_memory_assessment_v1',source:clone(value.opened.memory.source),current:{commit_sha:value.source.commit_sha,snapshot_sha256:value.source.snapshot_sha256},memory_sha256:value.opened.memory.sha256,model_calls:0,
        facts:[{fact_id:'memory_fact',status:'revalidated_static',static_eligible:true,historical_eligible:false}]}};
    if(route.endsWith('memory-source'))return {source:clone(value.source),capture_id:value.opened.receipt.id,receipt_sha256:value.opened.receipt.sha256,item_id:body.item_id,role:body.role,
      source_commit_sha:value.opened.memory.source.commit_sha,revision:value.opened.memory.source.commit_sha,kind:'code',locator:'parser.py:1-2',excerpt:'def parse(x):\n    return x',excerpt_sha256:hash('d'),source_text_sha256:hash('e'),truncated:false,model_calls:0};
    if(route.endsWith('memory-preview'))return {source:clone(value.source),available:true,model_calls:0,investigation_created:false,
      selection:{schema:'product_memory_selection_v1',source:{pack_sha256:value.source.source_pack_sha256,repository:value.source.repository,
        commit_sha:value.source.commit_sha,snapshot_sha256:value.source.snapshot_sha256},selections:clone(body.selections),
        items:body.selections.map(selection=>({selection:clone(selection),capture_kind:'architecture',memory_sha256:hash('4'),
          source_snapshot_sha256:hash('6'),assessment_sha256:hash('c'),hint:{source_commit_sha:'5'.repeat(40),viewed_commit_sha:value.source.commit_sha,
          runtime_verified:false,causal_verified:false,kind:'architecture',applicability:'revalidated_static_only',truth_level:'observed_syntax'}})),
        sha256:hash('a'),context_sha256:hash('b'),additional_context:'Explicit unverified memory context; not instructions.',
        model_calls:0,automatic_inference:false,historical_eligible:false,runtime_verified:false,causal_verified:false}};
    if(route==='/api/memory-investigations')return {id:job('c'),mode:'memory_followup',provider:body.provider,depth:body.depth,status:'queued'};
    throw Error('Unexpected route');
  }
  vm.runInNewContext(code,{document,window,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},api:async(route,body)=>{calls.push([route,body]);return handler?handler(route,body,normal):normal(route,body);}});
  const area=new El('main'),module=window.IssueIntelMemory;
  const render=(next=value,options={})=>{value=next;area.replaceChildren();return module.render(area,value.source.source_job_id,value.pack,{available:true,pack_sha256:value.source.source_pack_sha256,...options});};
  const button=label=>{const found=all(area).find(n=>n.tag==='button'&&n.textContent===label);assert.ok(found,label);return found;};
  return {area,module,calls,events,all,text,render,button,click:label=>button(label).onclick(),listeners,
    field:id=>all(area).find(n=>n.id===id),language:lang=>{document.documentElement.lang=lang;listeners.get('issueintel:languagechange')();},
    normal,value,change:detail=>listeners.get('issueintel:packchange')({detail})};
}

test('opening memory only checks status and searches saved sources; capture and Git assessment are deliberate',async()=>{
  const a=boot();await a.render();assert.equal(a.calls.length,2);assert.match(a.calls[0][0],/memory-status$/);assert.match(a.calls[1][0],/memory-search$/);
  assert.match(a.text(a.area),/không xác nhận nguyên nhân/);assert.match(a.text(a.area),/20 capture gần đây/);
  assert.equal(a.button('Lưu giả thuyết đã chọn').disabled,true);assert.ok(!a.calls.some(([r])=>/memory-capture|memory-assess/.test(r)));
});

test('architecture capture keeps exact viewed pack and creates no duplicate call from detached controls',async()=>{
  const a=boot();await a.render();a.field('memory-max-facts').value='4';a.field('memory-max-facts').oninput();const old=a.button('Lưu kiến trúc');await old.onclick();await old.onclick();
  const writes=a.calls.filter(([route])=>route.endsWith('memory-capture'));assert.equal(writes.length,1);
  assert.deepEqual(clone(writes[0][1]),{source_pack_sha256:hash('1'),kind:'architecture',max_facts:4});assert.match(a.text(a.area),/Đã lưu capture/);
  assert.equal(writes[0][1].provider,undefined);
});

test('hypothesis selection follows source order and preserves unverified notes across language and rerender',async()=>{
  const a=boot();await a.render();const boxes=a.all(a.area).filter(n=>n.tag==='input'&&n.type==='checkbox');boxes[1].checked=true;boxes[1].onchange();boxes[0].checked=true;boxes[0].onchange();
  a.field('memory-note').value='Keep my unverified note';a.field('memory-note').oninput();a.language('en');assert.equal(a.field('memory-note').value,'Keep my unverified note');
  await a.click('Retain selected hypotheses');const request=a.calls.find(([route])=>route.endsWith('memory-capture'))[1];
  assert.deepEqual(clone(request.hypothesis_ids),['H1','H2']);assert.equal(request.note,'Keep my unverified note');
  assert.equal(request.kind,'lesson');
});

test('capture acknowledgement loss retains draft and requires a successful list refresh before another write',async()=>{
  const a=boot((route,body,normal)=>{if(route.endsWith('memory-capture'))throw Error('Lost acknowledgement');return normal(route,body);});await a.render();
  a.field('memory-note').value='Keep this note';a.field('memory-note').oninput();await a.click('Lưu kiến trúc');assert.equal(a.button('Lưu kiến trúc').disabled,true);
  assert.equal(a.field('memory-note').value,'Keep this note');assert.match(a.text(a.area),/Bản nháp giữ nguyên/);
  await a.click('Kiểm lại nguồn và tải danh sách');assert.equal(a.button('Lưu kiến trúc').disabled,false);
});

test('open, assessment and code inspection retain original commit and selected receipt',async()=>{
  const a=boot();await a.render();await a.click('Xem capture');assert.match(a.text(a.area),new RegExp('5'.repeat(40)));
  await a.click('Đối chiếu với phiên đang xem');assert.match(a.text(a.area),/Đã đối chiếu tĩnh/);
  await a.click('Nguồn code');assert.match(a.text(a.area),/def parse/);assert.match(a.text(a.area),/chưa tự trở thành citation của phiên đang xem/);
  for(const [,body] of a.calls.filter(([route])=>/memory-(open|assess|source)$/.test(route))){assert.equal(body.capture_id,a.value.opened.receipt.id);assert.equal(body.receipt_sha256,hash('9'));assert.equal(body.source_pack_sha256,hash('1'));}
});

test('navigation discards late source replies and stale controls without relabeling the new viewed run',async()=>{
  let finish;const a=boot((route,body,normal)=>{const reply=normal(route,body);return route.endsWith('memory-source')?new Promise(resolve=>finish=()=>reply.then(resolve)):reply;});
  await a.render();await a.click('Xem capture');const stale=a.button('Lưu kiến trúc'),pending=a.click('Nguồn code');a.module.cancel();
  const next=fixture();next.source.source_job_id=job('c');next.status={...next.source,available:true,can_capture_lesson:true,can_preview:true,model_calls:0};next.opened.source=next.source;
  // New source initialization fails closed because this synthetic handler still returns the old source; no old code may appear.
  await a.render(next);await stale.onclick();await finish();await pending;assert.doesNotMatch(a.text(a.area),/def parse/);
  assert.equal(a.calls.filter(([route])=>route.endsWith('memory-capture')).length,0);
});

test('source, receipt and truth tampering disables further source actions',async()=>{
  for(const mutate of [v=>v.source.source_pack_sha256=hash('0'),v=>v.receipt.sha256='bad',v=>v.memory.runtime_verified=true,v=>v.memory.facts[0].source=null]){
    const a=boot(async(route,body,normal)=>{const value=await normal(route,body);if(route.endsWith('memory-open'))mutate(value);return value;});await a.render();await a.click('Xem capture');
    assert.match(a.text(a.area),/Không đọc được nguồn memory/);assert.equal(a.all(a.area).filter(n=>n.tag==='button'&&n.textContent==='Nguồn code').length,0);
  }
});

test('a report without product authority performs no memory reads or writes',async()=>{
  const a=boot();await a.render(a.value,{available:false});assert.equal(a.calls.length,0);assert.match(a.text(a.area),/ngoài benchmark/);
});

test('untrusted source and note text remain inert; invalid bound never posts',async()=>{
  const value=fixture();value.opened.memory.facts[0].source.qualname='<script>inert</script>';const a=boot(null,value);await a.render();await a.click('Xem capture');
  assert.match(a.text(a.area),/<script>inert/);a.field('memory-max-facts').value='0';a.field('memory-max-facts').oninput();await a.click('Lưu kiến trúc');
  assert.equal(a.calls.filter(([route])=>route.endsWith('memory-capture')).length,0);
});

test('module and status labels are bilingual, source-bound and wired into the Workbench',()=>{
  for(const match of code.matchAll(/\bt\(['"]([^'"]+)['"]/g)){const key=match[1];assert.ok(messages[key]?.vi,key);assert.ok(messages[key]?.en,key);
    const slots=s=>[...s.matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort();assert.deepEqual(slots(messages[key].vi),slots(messages[key].en));}
  const html=fs.readFileSync(path.join(dir,'index.html'),'utf8');assert.match(html,/data-tab="memory"/);assert.match(html,/src="\/memory.js"/);assert.match(html,/href="\/memory.css"/);
});

test('five selections remain bounded and checkbox interaction preserves keyboard focus without rerender',async()=>{
  const value=fixture();value.pack.annotation.hypotheses=Array.from({length:7},(_,i)=>({id:'H'+i,statement:'Distinct proposal '+i}));const a=boot(null,value);await a.render();
  const choices=a.all(a.area).filter(n=>n.tag==='input'&&n.type==='checkbox');for(const input of choices.slice(0,5)){input.checked=true;input.onchange();}
  assert.equal(choices[5].disabled,true);assert.equal(choices[0].disabled,false);assert.match(a.text(a.area),/Đã chọn 5\/5/);
  choices[0].checked=false;choices[0].onchange();assert.equal(choices[5].disabled,false);
});

test('assessment cannot move historical eligibility, revision or source snapshot bindings',async()=>{
  for(const mutate of [v=>v.assessment.facts[0].historical_eligible=true,v=>v.assessment.source.snapshot_sha256=hash('0'),v=>v.assessment.current.commit_sha='f'.repeat(40),v=>v.assessment.facts[0].status='verified_fix']){
    const a=boot(async(route,body,normal)=>{const reply=await normal(route,body);if(route.endsWith('memory-assess'))mutate(reply);return reply;});await a.render();await a.click('Xem capture');await a.click('Đối chiếu với phiên đang xem');
    assert.match(a.text(a.area),/Có lỗi khi đối chiếu nguồn/);assert.doesNotMatch(a.text(a.area),/Đã đối chiếu tĩnh/);
  }
});

test('busy queue is reported distinctly and never treated as an unacknowledged capture',async()=>{
  const a=boot((route,body,normal)=>{if(route.endsWith('memory-capture'))throw Error('Queue is full; wait for a current investigation');return normal(route,body);});await a.render();await a.click('Lưu kiến trúc');
  assert.match(a.text(a.area),/Hàng đợi đang bận/);assert.equal(a.button('Lưu kiến trúc').disabled,false);assert.equal(a.calls.filter(([route])=>route.endsWith('memory-capture')).length,1);
});

test('expanded capture controls and draft survive repaint and language changes',async()=>{
  const a=boot();await a.render();const details=a.all(a.area).find(n=>n.tag==='details'&&n.className==='memory-capture');details.open=true;details.ontoggle();
  a.field('memory-note').value='Keep this draft';a.field('memory-note').oninput();a.language('en');
  assert.equal(a.all(a.area).find(n=>n.tag==='details'&&n.className==='memory-capture').open,true);assert.equal(a.field('memory-note').value,'Keep this draft');
});

test('failed re-assessment clears the earlier status while saved code remains readable',async()=>{
  let failed=false;const a=boot((route,body,normal)=>{if(failed&&route.endsWith('memory-assess'))throw Error('Missing Git');return normal(route,body);});await a.render();await a.click('Xem capture');
  await a.click('Đối chiếu với phiên đang xem');assert.match(a.text(a.area),/Đã đối chiếu tĩnh/);failed=true;await a.click('Đối chiếu với phiên đang xem');
  assert.doesNotMatch(a.text(a.area),/Đã đối chiếu tĩnh/);assert.match(a.text(a.area),/Có lỗi khi đối chiếu nguồn/);
  await a.click('Nguồn code');assert.match(a.text(a.area),/def parse/);assert.match(a.text(a.area),/chưa được đối chiếu/);
});

test('an offline run can retain architecture while explaining why hypothesis retention is unavailable',async()=>{
  const value=fixture();value.pack.annotation=null;value.status.can_capture_lesson=false;const a=boot(null,value);await a.render();
  assert.match(a.text(a.area),/chưa có giả thuyết để lưu/);assert.equal(a.button('Lưu kiến trúc').disabled,false);
  assert.equal(a.button('Lưu giả thuyết đã chọn').disabled,true);assert.equal(a.field('memory-note').disabled,true);
});

test('preview requires deliberate item selection and never creates an investigation or provider call',async()=>{
  const a=boot();await a.render();await a.click('Xem capture');assert.equal(a.button('Kiểm lựa chọn và xem preview').disabled,true);
  const box=a.field('memory-select-memory_fact');box.checked=true;box.onchange();assert.equal(a.button('Kiểm lựa chọn và xem preview').disabled,false);
  await a.click('Kiểm lựa chọn và xem preview');const call=a.calls.find(([route])=>route.endsWith('memory-preview'));
  assert.deepEqual(clone(call[1]),{source_pack_sha256:hash('1'),selections:[{capture_id:a.value.opened.receipt.id,receipt_sha256:hash('9'),item_id:'memory_fact',item_sha256:hash('7')}]});
  assert.match(a.text(a.area),/Explicit unverified memory context/);assert.match(a.text(a.area),/chưa tạo phiên hoặc gọi model/);
  assert.ok(a.calls.every(([route])=>!route.includes('investigations')));
  a.language('en');assert.match(a.text(a.area),/Explicit unverified memory context/);assert.equal(a.calls.filter(([r])=>r.endsWith('memory-preview')).length,1);
  const current=a.field('memory-select-memory_fact');current.checked=false;current.onchange();assert.doesNotMatch(a.text(a.area),/Explicit unverified memory context/);
  assert.equal(a.button('Check selection and preview').disabled,true);
});

test('six memory items remain bounded without replacing the keyboard-focused checkbox',async()=>{
  const value=fixture(),first=value.opened.memory.facts[0];
  value.opened.memory.facts=Array.from({length:7},(_,i)=>({...clone(first),id:'memory_'+i}));
  value.opened.selectable_items=value.opened.memory.facts.map(f=>({id:f.id,item_sha256:hash('7')}));const a=boot(null,value);await a.render();await a.click('Xem capture');
  for(let i=0;i<6;i++){const input=a.field('memory-select-memory_'+i);input.checked=true;input.onchange();}
  assert.equal(a.field('memory-select-memory_6').disabled,true);assert.equal(a.field('memory-select-memory_0').disabled,false);
  const selected=a.field('memory-select-memory_0');selected.checked=false;selected.onchange();assert.equal(a.field('memory-select-memory_6').disabled,false);
  assert.match(a.text(a.area),/Đã chọn 5\/6/);
});

test('waiting Planned sources can inspect memory but cannot preview a new branch that resets budget',async()=>{
  const value=fixture();value.status.can_preview=false;const a=boot(null,value);await a.render();await a.click('Xem capture');
  assert.equal(a.field('memory-select-memory_fact').disabled,true);assert.equal(a.button('Kiểm lựa chọn và xem preview').disabled,true);
  assert.match(a.text(a.area),/giữ ngân sách/);await a.click('Nguồn code');assert.match(a.text(a.area),/def parse/);
});

test('preview responses cannot promote hints or change the selected item/source identity',async()=>{
  for(const mutate of [v=>v.selection.source.pack_sha256=hash('f'),v=>v.selection.selections[0].item_sha256=hash('f'),
      v=>v.selection.items[0].hint.runtime_verified=true,v=>v.selection.items[0].hint.truth_level='verified_cause',
      v=>v.investigation_created=true]){
    const a=boot(async(route,body,normal)=>{const response=await normal(route,body);if(route.endsWith('memory-preview'))mutate(response);return response;});
    await a.render();await a.click('Xem capture');const input=a.field('memory-select-memory_fact');input.checked=true;input.onchange();await a.click('Kiểm lựa chọn và xem preview');
    assert.match(a.text(a.area),/Không đọc được nguồn memory/);assert.doesNotMatch(a.text(a.area),/Explicit unverified memory context/);
  }
});

test('invalidated architecture selection explains removal without exposing an old preview',async()=>{
  let denied=false;const a=boot((route,body,normal)=>denied&&route.endsWith('memory-preview')?{
    source:clone(a.value.source),available:false,reason:'architecture_item_not_revalidated',item_id:'memory_fact',status:'source_changed',model_calls:0,investigation_created:false}:normal(route,body));
  await a.render();await a.click('Xem capture');const input=a.field('memory-select-memory_fact');input.checked=true;input.onchange();await a.click('Kiểm lựa chọn và xem preview');
  assert.match(a.text(a.area),/Explicit unverified/);denied=true;await a.click('Kiểm lựa chọn và xem preview');
  assert.match(a.text(a.area),/Bỏ mục đó/);assert.doesNotMatch(a.text(a.area),/Explicit unverified/);assert.equal(a.field('memory-select-memory_fact').checked,true);
});

async function selectedPreview(a){
  await a.render();await a.click('Xem capture');const box=a.field('memory-select-memory_fact');box.checked=true;box.onchange();await a.click('Kiểm lựa chọn và xem preview');
}

test('offline execution binds exact preview and stale submit controls cannot create another attempt',async()=>{
  const a=boot();await selectedPreview(a);const stale=a.button('Tạo phiên từ preview này');await stale.onclick();await stale.onclick();
  const posts=a.calls.filter(([r])=>r==='/api/memory-investigations');assert.equal(posts.length,1);
  const request=clone(posts[0][1]);assert.equal(request.provider,'none');assert.equal(request.depth,'quick');assert.equal(request.selection_sha256,hash('a'));
  assert.equal(request.parent_job_id,job('a'));assert.equal(request.parent_pack_sha256,hash('1'));assert.equal(request.confirmation,'start_memory_investigation');
  assert.equal(request.selections[0].item_sha256,hash('7'));assert.equal(a.events[0].type,'issueintel:memorycreated');
  assert.deepEqual(clone(a.events[0].detail),{job_id:job('c'),source_job_id:job('a'),source_pack_sha256:hash('1')});
  assert.equal(a.field('memory-execution-groq').disabled,true);assert.equal(a.field('memory-execution-depth').disabled,true);
  await a.click('Kiểm trạng thái phiên đã gửi');assert.deepEqual(clone(a.calls.at(-1)[1]),request);
});

test('paid execution needs explicit consent, preserves chosen depth and survives language changes',async()=>{
  const a=boot();await selectedPreview(a);a.field('memory-execution-depth').value='deep';a.field('memory-execution-depth').onchange();
  a.field('memory-execution-groq').checked=true;a.field('memory-execution-groq').onchange();
  assert.equal(a.button('Tạo phiên từ preview này').disabled,true);await a.click('Tạo phiên từ preview này');
  assert.equal(a.calls.filter(([r])=>r==='/api/memory-investigations').length,0);
  a.field('memory-execution-consent').checked=true;a.field('memory-execution-consent').onchange();a.language('en');
  assert.equal(a.field('memory-execution-consent').checked,true);await a.click('Start a run from this preview');
  const request=a.calls.find(([r])=>r==='/api/memory-investigations')[1];assert.equal(request.provider,'groq');assert.equal(request.depth,'deep');
  assert.equal(request.confirmation,'start_memory_investigation_with_api_cost');
});

test('lost execution acknowledgement locks exact request and checking status cannot change provider or depth',async()=>{
  let lost=true;const a=boot((route,body,normal)=>{if(lost&&route==='/api/memory-investigations')throw Error('Lost reply');return normal(route,body);});
  await selectedPreview(a);await a.click('Tạo phiên từ preview này');assert.equal(a.events.length,0);assert.match(a.text(a.area),/Chưa xác nhận được phiên mới/);
  assert.equal(a.field('memory-execution-groq').disabled,true);assert.equal(a.field('memory-execution-depth').disabled,true);
  lost=false;await a.click('Kiểm trạng thái phiên đã gửi');const requests=a.calls.filter(([r])=>r==='/api/memory-investigations');
  assert.equal(requests.length,2);assert.deepEqual(clone(requests[0][1]),clone(requests[1][1]));assert.equal(a.events.length,1);
});

test('a late execution acknowledgement cannot switch the newly viewed source',async()=>{
  let finish;const a=boot(async(route,body,normal)=>{const response=await normal(route,body);return route==='/api/memory-investigations'?new Promise(resolve=>finish=()=>resolve(response)):response;});
  await selectedPreview(a);const pending=a.click('Tạo phiên từ preview này');a.module.cancel();
  const next=fixture();next.source.source_job_id=job('d');await a.render(next);finish();await pending;
  assert.equal(a.events.length,0);assert.ok(!a.text(a.area).includes('Đã nhận phiên điều tra dùng memory'));
});

test('changed selection clears execution controls and detached submit cannot send',async()=>{
  const a=boot();await selectedPreview(a);const stale=a.button('Tạo phiên từ preview này');
  const box=a.field('memory-select-memory_fact');box.checked=false;box.onchange();await stale.onclick();
  assert.ok(!a.calls.some(([r])=>r==='/api/memory-investigations'));assert.ok(!a.field('memory-execution-groq'));
});

test('a child displays its saved memory separately from current capture search and source assessment',async()=>{
  const value=fixture();value.pack.followup={memory_selection:{sha256:hash('b'),additional_context:'Saved unverified context',items:[{
    selection:{capture_id:'capture_'+'f'.repeat(32),item_id:'old_fact'},hint:{kind:'architecture',source:{path:'prior.py',qualname:'parse'},source_commit_sha:'e'.repeat(40)}}]}};
  const a=boot(null,value);await a.render();assert.match(a.text(a.area),/Memory đã dùng trong phiên này/);assert.match(a.text(a.area),/prior.py · parse/);
  assert.match(a.text(a.area),/Saved unverified context/);assert.match(a.text(a.area),/nguồn chưa được đối chiếu lại/);
  assert.equal(a.calls.length,2);assert.ok(a.calls.every(([r])=>/memory-status|memory-search/.test(r)));
  a.language('en');assert.match(a.text(a.area),/Memory used in this run/);assert.match(a.text(a.area),/Saved unverified context/);
});

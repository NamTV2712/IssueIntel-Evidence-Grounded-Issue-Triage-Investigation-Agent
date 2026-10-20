/* Immutable repository observations; every action remains bound to the viewed run. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t,node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),job=v=>typeof v==='string'&&/^[a-f0-9]{24}$/.test(v);
  const commit=v=>typeof v==='string'&&/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(v),capture=v=>typeof v==='string'&&/^capture_[a-f0-9]{32}$/.test(v);
  const kinds={architecture:'Kiến trúc và quan hệ tĩnh',investigation_lesson:'Giả thuyết cũ chưa xác minh'};
  const relations={definition:'Định nghĩa',calls:'Lời gọi tĩnh',imports:'Import tĩnh',inherits:'Kế thừa tĩnh'};
  const statuses={revalidated_static:'Đã đối chiếu tĩnh',source_changed:'Nguồn thay đổi',source_missing:'Nguồn mất',source_unavailable:'Nguồn chưa đọc được',
    parser_changed:'Producer thay đổi',relationship_changed:'Quan hệ đã đổi',no_code_dependencies:'Chưa có dependency code',
    source_unchanged_hypothesis_unverified:'Nguồn code còn nguyên; giả thuyết chưa xác minh'};
  const drafts=new Map(),states=new Map();let active=null,area=null,view=0;
  const sameSource=(a,b)=>a&&b&&['source_job_id','source_pack_sha256','repository','commit_sha','snapshot_sha256'].every(k=>a[k]===b[k]);
  const current=e=>active===e&&area!==null;
  const binding=e=>({source_pack_sha256:e.source.source_pack_sha256});
  const selection=(e,value=e.opened)=>({...binding(e),capture_id:value.receipt.id,receipt_sha256:value.receipt.sha256});
  function validCapture(value,e){
    const r=value?.receipt,m=value?.memory;
    if(!sameSource(value?.source,e.source)||value.model_calls!==0||!r||!m||!capture(r.id)||!hash(r.sha256)||!hash(m.sha256)
      ||r.repository!==e.source.repository||r.memory_sha256!==m.sha256||r.snapshot_sha256!==m.source?.snapshot_sha256
      ||!commit(m.source?.commit_sha)||!hash(m.source?.snapshot_sha256)||m.runtime_verified!==false||m.causal_verified!==false)return false;
    if(r.kind==='architecture')return m.schema==='repository_architecture_memory_v1'&&Array.isArray(m.facts)&&m.facts.length<=1000
      &&Number.isInteger(m.facts_omitted)&&m.facts_omitted>=0
      &&new Set(m.facts.map(f=>f?.id)).size===m.facts.length&&m.facts.every(f=>typeof f.id==='string'&&!!f.id&&hash(f.sha256)
        &&['definition','calls','imports','inherits'].includes(f.kind)&&f.source&&typeof f.source.path==='string'&&typeof f.source.qualname==='string'
        &&(f.target===null||f.target&&typeof f.target.path==='string'&&typeof f.target.qualname==='string'));
    return r.kind==='investigation_lesson'&&m.schema==='repository_investigation_lesson_v1'&&m.truth_level==='unverified_prior_investigation'
      &&m.historical_eligible===false&&m.independent_human===false&&Array.isArray(m.hypotheses)&&m.hypotheses.length<=5
      &&m.hypotheses.every(h=>typeof h.id==='string'&&typeof h.statement==='string'&&typeof h.falsification_test==='string')&&Array.isArray(m.evidence);
  }
  async function run(e,operation,call,apply){
    if(e.busy)return false;const token=++e.operation;e.busy=true;e.error=false;e.notice='Đang xử lý memory cục bộ…';if(current(e))paint(e);
    try{const value=await call();if(token!==e.operation)return false;apply(value);return true;}
    catch(error){if(token===e.operation){const queueFull=typeof error?.message==='string'&&error.message.startsWith('Queue is full');
      e.error=true;e.notice=queueFull?'Hàng đợi đang bận; thử lại sau khi tác vụ hiện tại kết thúc.':operation==='assess'?'Có lỗi khi đối chiếu nguồn. Git tại commit cần có trên máy; memory cũ vẫn mở được.':'Không đọc được nguồn memory khớp phiên. Kiểm lại trước khi tiếp tục.';
      if(operation==='capture'&&!queueFull){e.uncertain=true;e.notice='Chưa xác nhận được thao tác memory. Bản nháp giữ nguyên; tải lại danh sách trước khi gửi lại.';}}return false;}
    finally{if(token===e.operation){e.busy=false;if(current(e))paint(e);}}
  }
  async function search(e,saved=false){
    if(!e.status?.available)return;
    const query=e.draft.query.trim();
    return run(e,'search',()=>api('/api/jobs/'+e.source.source_job_id+'/memory-search',{...binding(e),query,limit:20}),value=>{
      if(!sameSource(value?.source,e.source)||value.schema!=='repository_memory_list_v1'||value.repository!==e.source.repository||value.query!==query
        ||value.model_calls!==0||!Array.isArray(value.results)||value.results.length>50||new Set(value.results.map(r=>r?.id)).size!==value.results.length
        ||value.results.some(r=>!capture(r?.id)||!hash(r.receipt_sha256)||!Object.hasOwn(kinds,r.kind)||!commit(r.source?.commit_sha)
          ||!Number.isInteger(r.retained_count)||r.retained_count<0))throw Error('Unbound memory list');
      e.rows=value.results;e.resultQuery=value.query;e.uncertain=false;e.notice=saved?'Đã lưu capture; nguồn và báo cáo gốc giữ nguyên.':null;
    });
  }
  async function load(e){
    const ok=await run(e,'status',()=>api('/api/jobs/'+e.source.source_job_id+'/memory-status'),value=>{
      if(value?.available!==true){e.status={available:false};e.notice='Memory chỉ dùng với phiên product đã lưu, ngoài benchmark.';return;}
      if(!sameSource(value,e.source)||value.model_calls!==0||typeof value.can_capture_lesson!=='boolean')throw Error('Changed product source');e.status=value;e.notice=null;
    });
    if(ok&&e.status?.available)await search(e);
  }
  async function save(e,kind){
    if(!e.status?.available||e.uncertain||e.busy)return;
    const request={...binding(e),kind};
    if(kind==='architecture'){
      const n=Number(e.draft.maxFacts);if(!Number.isInteger(n)||n<1||n>1000){e.error=true;e.notice='Chọn phạm vi memory hợp lệ trước khi lưu.';paint(e);return;}request.max_facts=n;
    }else{
      request.hypothesis_ids=e.hypotheses.filter(h=>e.draft.selected.has(h.id)).map(h=>h.id);request.note=e.draft.note;
      if(!request.hypothesis_ids.length||request.hypothesis_ids.length>5||request.note.length>2000)return;
    }
    const ok=await run(e,'capture',()=>api('/api/jobs/'+e.source.source_job_id+'/memory-capture',request),value=>{
      if(!validCapture(value,e)||value.receipt.source_pack_sha256!==e.source.source_pack_sha256||value.receipt.source_job_id!==e.source.source_job_id
        ||value.receipt.kind!==(kind==='architecture'?'architecture':'investigation_lesson')
        ||value.memory.source.commit_sha!==e.source.commit_sha||value.memory.source.snapshot_sha256!==e.source.snapshot_sha256)throw Error('Changed capture source');
      e.opened=value;e.assessment=null;e.excerpts.clear();e.shown=20;e.notice='Đã lưu capture; nguồn và báo cáo gốc giữ nguyên.';
    });
    if(ok)await search(e,true);
  }
  async function open(e,row){
    return run(e,'open',()=>api('/api/jobs/'+e.source.source_job_id+'/memory-open',{...binding(e),capture_id:row.id,receipt_sha256:row.receipt_sha256}),value=>{
      if(!validCapture(value,e)||value.receipt.id!==row.id||value.receipt.sha256!==row.receipt_sha256)throw Error('Changed memory receipt');
      e.opened=value;e.assessment=null;e.excerpts.clear();e.shown=20;e.notice=null;
    });
  }
  async function assess(e){
    const selected=e.opened;if(!selected)return;
    if(e.busy)return;e.assessment=null;
    return run(e,'assess',()=>api('/api/jobs/'+e.source.source_job_id+'/memory-assess',selection(e,selected)),value=>{
      const a=value?.assessment;
      if(!sameSource(value?.source,e.source)||value.capture_id!==selected.receipt.id||value.receipt_sha256!==selected.receipt.sha256||value.model_calls!==0
        ||a?.memory_sha256!==selected.memory.sha256||a.current?.commit_sha!==e.source.commit_sha||a.current?.snapshot_sha256!==e.source.snapshot_sha256
        ||a.source?.commit_sha!==selected.memory.source.commit_sha||a.source?.snapshot_sha256!==selected.memory.source.snapshot_sha256||a.model_calls!==0)throw Error('Changed assessment scope');
      if(selected.receipt.kind==='architecture'){
        const ids=new Set(selected.memory.facts.map(f=>f.id));
        if(a.schema!=='repository_memory_assessment_v1'||!Array.isArray(a.facts)||a.facts.length!==ids.size||new Set(a.facts.map(f=>f?.fact_id)).size!==ids.size
          ||a.facts.some(f=>!ids.has(f.fact_id)||!Object.hasOwn(statuses,f.status)||f.historical_eligible!==false||f.static_eligible!==(f.status==='revalidated_static')))throw Error('Invalid static assessment');
      }else if(a.schema!=='repository_lesson_assessment_v1'||a.historical_eligible!==false||a.hypothesis_verified!==false||a.truth_level!=='unverified_prior_investigation'||!Object.hasOwn(statuses,a.dependency_status))throw Error('Lesson promoted');
      e.assessment=a;e.notice=null;
    });
  }
  async function excerpt(e,itemId,role){
    const selected=e.opened;if(!selected)return;
    return run(e,'source',()=>api('/api/jobs/'+e.source.source_job_id+'/memory-source',{...selection(e,selected),item_id:itemId,role}),value=>{
      if(!sameSource(value?.source,e.source)||value.capture_id!==selected.receipt.id||value.receipt_sha256!==selected.receipt.sha256||value.item_id!==itemId||value.role!==role
        ||value.source_commit_sha!==selected.memory.source.commit_sha||typeof value.excerpt!=='string'||value.excerpt.length>6000||typeof value.locator!=='string'
        ||!['code','issue','repository_context'].includes(value.kind)||(value.kind==='code'&&value.revision!==selected.memory.source.commit_sha)
        ||typeof value.truncated!=='boolean'||!hash(value.excerpt_sha256)||!hash(value.source_text_sha256)||value.model_calls!==0)throw Error('Changed memory excerpt');
      e.excerpts.set(itemId+':'+role,value);e.notice=null;
    });
  }
  function button(e,label,action){
    const revision=view,b=node('button',t(label));b.type='button';b.disabled=e.busy;
    b.onclick=()=>{if(current(e)&&revision===view&&!b.disabled)return action();};return b;
  }
  function codeSource(e,parent,id,role,label){
    parent.append(button(e,label,()=>excerpt(e,id,role)));const found=e.excerpts.get(id+':'+role);if(!found)return;
    const box=node('section',undefined,'memory-code');box.append(node('strong',found.locator),node('p',found.source_commit_sha,'hash-note'),node('pre',found.excerpt));
    if(found.truncated)box.append(node('p',t('Nội dung code có rút gọn.'),'notice'));
    box.append(node('p','SHA256 '+found.excerpt_sha256,'hash-note'),node('p',t('Chứng cứ từ capture đã lưu; chưa tự trở thành citation của phiên đang xem.'),'field-note'));parent.append(box);
  }
  function controls(e){
    if(!e.live)return;const locked=e.busy||!e.status?.available||e.uncertain;
    e.live.arch.disabled=locked;e.live.lesson.disabled=locked||!e.status?.can_capture_lesson||!e.draft.selected.size;
    e.live.count.textContent=t('Đã chọn {count}/5 giả thuyết',{count:e.draft.selected.size});
    for(const [id,input] of e.live.checks)input.disabled=e.busy||(!input.checked&&e.draft.selected.size>=5);
  }
  function paint(e){
    if(!current(e))return;view++;const revision=view;area.replaceChildren();
    area.append(node('h3',t('Bộ nhớ điều tra')),node('p',t('Lưu và xem lại kiến trúc, nguồn code và giả thuyết của repository.')),
      node('p',e.source.repository,'memory-repository'),node('p',e.source.commit_sha,'hash-note'),
      node('p',t('Memory chưa tự đi vào model. Quan hệ tĩnh và giả thuyết cũ không xác nhận nguyên nhân.'),'notice'));
    const notice=node('p',e.notice?t(e.notice):'',e.error?'memory-error':'notice');notice.setAttribute('role',e.error?'alert':'status');area.append(notice);
    area.append(button(e,'Kiểm lại nguồn và tải danh sách',()=>load(e)));
    if(!e.status?.available)return;
    const captureBox=node('details',undefined,'memory-capture');captureBox.append(node('summary',t('Lưu từ phiên đang xem')),
      node('p',t('Lưu và đối chiếu có thể đọc Git local; mở/tìm chỉ đọc capture đã lưu.'),'field-note'));
    captureBox.open=!!e.captureExpanded;captureBox.ontoggle=()=>{if(current(e)&&revision===view)e.captureExpanded=captureBox.open;};
    const architecture=node('form'),label=node('label',t('Tối đa quan sát kiến trúc')),limit=node('input');limit.type='number';limit.min='1';limit.max='1000';limit.id='memory-max-facts';limit.value=e.draft.maxFacts;limit.disabled=e.busy;label.htmlFor=limit.id;
    limit.oninput=()=>{if(current(e)&&revision===view)e.draft.maxFacts=limit.value;};const arch=button(e,'Lưu kiến trúc',()=>save(e,'architecture'));
    architecture.onsubmit=event=>{event.preventDefault();if(current(e)&&revision===view)return save(e,'architecture');};architecture.append(label,limit,arch);captureBox.append(architecture);
    const lesson=node('section');lesson.append(node('h4',t('Lưu giả thuyết cũ')),node('p',t('Chọn tối đa 5 giả thuyết, giữ thứ tự nguồn.'),'field-note'));
    if(!e.hypotheses.length)lesson.append(node('p',t('Phiên này chưa có giả thuyết để lưu.'),'notice'));
    const checks=new Map(),count=node('p');
    for(const h of e.hypotheses){const choice=node('label',undefined,'checkbox memory-hypothesis'),input=node('input');input.type='checkbox';input.checked=e.draft.selected.has(h.id);
      input.onchange=()=>{if(!current(e)||revision!==view||input.disabled)return;if(input.checked&&e.draft.selected.size<5)e.draft.selected.add(h.id);else{e.draft.selected.delete(h.id);input.checked=false;}controls(e);};
      choice.append(input,node('span',h.id+' · '+h.statement));checks.set(h.id,input);lesson.append(choice);}
    const noteLabel=node('label',t('Ghi chú chưa xác minh (tùy chọn)')),note=node('textarea');note.id='memory-note';note.rows=3;note.maxLength=2000;note.value=e.draft.note;note.disabled=e.busy||!e.status.can_capture_lesson;noteLabel.htmlFor=note.id;
    note.oninput=()=>{if(current(e)&&revision===view)e.draft.note=note.value;};
    const saveLesson=button(e,'Lưu giả thuyết đã chọn',()=>save(e,'lesson'));lesson.append(count,noteLabel,note,saveLesson);captureBox.append(lesson);area.append(captureBox);
    e.live={arch,lesson:saveLesson,count,checks};controls(e);
    const searchForm=node('form',undefined,'memory-search'),queryLabel=node('label',t('Từ khóa memory')),query=node('input');query.id='memory-query';query.value=e.draft.query;query.maxLength=500;query.disabled=e.busy;queryLabel.htmlFor=query.id;
    query.oninput=()=>{if(current(e)&&revision===view)e.draft.query=query.value;};searchForm.onsubmit=event=>{event.preventDefault();if(current(e)&&revision===view)return search(e);};
    searchForm.append(queryLabel,query,button(e,'Tìm memory',()=>search(e)));area.append(searchForm,node('p',t('Chỉ tìm trong 16.000 ký tự index đầu mỗi capture.'),'field-note'));
    const list=node('section',undefined,'memory-list');list.append(node('h4',t('Danh sách memory')),
      node('p',t('Phạm vi kết quả: {query}',{query:e.resultQuery||t('20 capture gần đây')}),'field-note'));
    if(!e.rows.length)list.append(node('p',t('Chưa có capture trong phạm vi tìm kiếm này.'),'notice'));
    for(const row of e.rows){const item=node('article',undefined,'memory-row');item.append(node('strong',t(kinds[row.kind])),node('code',row.source.commit_sha.slice(0,12)),node('span',row.retained_count===1?t('1 mục đã lưu'):t('{count} mục đã lưu',{count:row.retained_count})),
      button(e,'Xem capture',()=>open(e,row)));list.append(item);}area.append(list);
    if(e.opened)detail(e);
  }
  function detail(e){
    const {receipt:r,memory:m}=e.opened,detail=node('section',undefined,'memory-detail');
    detail.append(node('h4',t('Capture đã mở')),node('p',t(kinds[r.kind])),node('p',t('Revision nguồn đã lưu: {commit}',{commit:m.source.commit_sha}),'hash-note'),
      node('p',t('Revision đang xem: {commit}',{commit:e.source.commit_sha}),'hash-note'),
      node('p',t('Đã lưu lúc: {time}',{time:r.recorded_at}),'hash-note'),button(e,'Đối chiếu với phiên đang xem',()=>assess(e)),
      node('p',t('Chỉ đối chiếu Git/code, không chạy code hoặc gọi model.'),'field-note'));
    if(!e.assessment)detail.append(node('p',t('Memory chưa được đối chiếu với revision đang xem.'),'notice'));
    const byId=new Map((e.assessment?.facts||[]).map(f=>[f.fact_id,f]));
    if(r.kind==='architecture'){
      for(const fact of m.facts.slice(0,e.shown)){const item=node('article',undefined,'memory-observation');item.append(node('strong',fact.source.path+' · '+fact.source.qualname),
        node('p',t(relations[fact.kind])+(fact.target?' → '+fact.target.path+' · '+fact.target.qualname:'')),node('p',t('Nguồn tĩnh chưa xác minh runtime.'),'field-note'));
        if(byId.has(fact.id))item.append(node('p',t(statuses[byId.get(fact.id).status]),'memory-validity'));
        codeSource(e,item,fact.id,'source','Nguồn code');if(fact.target)codeSource(e,item,fact.id,'target','Nguồn đích');detail.append(item);}
      if(e.shown<m.facts.length)detail.append(button(e,'Hiển thị thêm quan sát',()=>{e.shown+=20;paint(e);}));
      if(m.facts_omitted)detail.append(node('p',t('Quan sát không được lưu do giới hạn: {count}',{count:m.facts_omitted}),'notice'));
    }else{
      detail.append(node('blockquote',m.issue_excerpt),node('p',m.author_note));
      if(e.assessment)detail.append(node('p',t(statuses[e.assessment.dependency_status]),'memory-validity'));
      for(const h of m.hypotheses){const item=node('article',undefined,'memory-observation');item.append(node('strong',h.id+' · '+h.statement),node('p',h.falsification_test));detail.append(item);}
      for(const record of m.evidence){const item=node('article',undefined,'memory-observation');item.append(node('p',record.id+' · '+record.locator));codeSource(e,item,record.id,'source',record.kind==='code'?'Nguồn code':'Xem nguồn đã lưu');detail.append(item);}
    }
    const hashes=node('details');hashes.append(node('summary',t('Nguồn và hash memory')),node('p',r.id,'hash-note'),node('p','Memory SHA256 '+m.sha256,'hash-note'),
      node('p','Receipt SHA256 '+r.sha256,'hash-note'),node('p',t('Hash giúp đối chiếu nội dung, không xác thực tác giả.'),'field-note'));detail.append(hashes);area.append(detail);
  }
  function render(target,identifier,pack,options={}){
    area=target;view++;
    if(!job(identifier)||!hash(options.pack_sha256)||!commit(pack?.snapshot?.commit_sha)||!hash(pack?.snapshot?.snapshot_sha256)||options.available!==true){
      active=null;target.replaceChildren(node('p',t('Memory chỉ dùng với phiên product đã lưu, ngoài benchmark.'),'notice'));return;
    }
    const key=identifier+':'+options.pack_sha256;let e=states.get(key);
    if(!e){let draft=drafts.get(key);if(!draft){draft={query:'',note:'',maxFacts:'500',selected:new Set()};drafts.set(key,draft);}
      e={key,source:{source_job_id:identifier,source_pack_sha256:options.pack_sha256,repository:pack.case.repository,commit_sha:pack.snapshot.commit_sha,
        snapshot_sha256:pack.snapshot.snapshot_sha256},draft,hypotheses:(pack.annotation?.hypotheses||[]).map(h=>({id:h.id,statement:h.statement})),
        status:null,rows:[],opened:null,assessment:null,excerpts:new Map(),shown:20,operation:0,busy:false,uncertain:false,error:false,notice:null};states.set(key,e);}
    active=e;paint(e);
    for(const [oldKey,old] of states)if(states.size>8&&old!==e&&!old.busy)states.delete(oldKey);
    if(!e.status&&!e.busy)return load(e);
  }
  function cancel(){view++;area=null;active=null;}
  document.addEventListener('issueintel:packchange',event=>{if(active&&(event.detail?.job_id!==active.source.source_job_id||event.detail?.intake?.source_pack_sha256!==active.source.source_pack_sha256))cancel();});
  document.addEventListener('issueintel:languagechange',()=>{if(active)paint(active);});
  window.IssueIntelMemory={render,cancel};
})();

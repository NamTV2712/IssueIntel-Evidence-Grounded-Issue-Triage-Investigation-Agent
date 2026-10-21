/* Source-bound recovery of an owned stopped product job; no automatic calls. */
(() => {
  'use strict';
  const get=id=>document.getElementById(id),t=window.IssueIntelUI.t;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),job=v=>typeof v==='string'&&/^[a-f0-9]{24}$/.test(v);
  const stages=['execution_input','initial_retrieval','prepared_input','seed_generation','focused_retrieval','refined_generation',
    'verification_input','verification','planned_input','planned_complete',...Array.from({length:3},(_,i)=>'planned_retrieval_'+(i+1)),...Array.from({length:3},(_,i)=>'planned_generation_'+(i+1))];
  const states=new Map();let selected=null,epoch=0,frame=0;
  function stageName(stage){
    const numbered=/^planned_(retrieval|generation)_([1-3])$/.exec(stage);
    return numbered?t(numbered[1]==='retrieval'?'Truy xuất vòng {round}':'Sinh giả thuyết vòng {round}',{round:numbered[2]}):t(stage);
  }
  function valid(p,e){
    const bound=e.source,max=bound.provider==='none'?0:bound.depth==='planned'?9:bound.depth==='deep'?6:4;
    return p?.schema==='product_stage_recovery_preview_v1'&&p.available===true&&p.source_job_id===bound.id
      &&p.provider===bound.provider&&p.depth===bound.depth&&hash(p.binding_sha256)&&hash(p.context_sha256)
      &&typeof p.commit_sha==='string'&&/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(p.commit_sha)
      &&Array.isArray(p.completed_stages)&&p.completed_stages.length>0&&p.completed_stages.length<=18
      &&p.completed_stages[0]==='execution_input'&&new Set(p.completed_stages).size===p.completed_stages.length
      &&p.completed_stages.every(s=>stages.includes(s))&&[...stages,'export'].includes(p.next_stage)
      &&Number.isInteger(p.prior_provider_calls)&&p.prior_provider_calls>=0&&p.prior_provider_calls<=p.max_provider_calls
      &&validBudget(p,e,max)
      &&typeof p.requires_api==='boolean'&&(!p.requires_api||bound.provider==='groq'&&p.prior_provider_calls<p.max_provider_calls)
      &&p.automatic_retry===false&&p.model_calls===0;
  }
  function validBudget(p,e,max){
    if(p.budget_scope===undefined)return e.source.provider==='groq'&&e.source.depth==='planned'?[5,7,9].includes(p.max_provider_calls):p.max_provider_calls===max;
    return p.budget_scope==='planned_continuation_remaining'&&['planned_continuation','stage_resume'].includes(e.source.mode)
      &&e.source.provider==='groq'&&e.source.depth==='planned'
      &&[5,7,9].includes(p.root_max_provider_calls)&&Number.isInteger(p.inherited_root_provider_calls)
      &&p.inherited_root_provider_calls>=1&&p.inherited_root_provider_calls<=p.root_max_provider_calls
      &&p.max_provider_calls===p.root_max_provider_calls-p.inherited_root_provider_calls;
  }
  function validChain(p){
    if(p.source_job_ids===undefined&&p.root_job_id===undefined)return true;
    return job(p.root_job_id)&&Array.isArray(p.source_job_ids)&&p.source_job_ids.length>=1&&p.source_job_ids.length<=8
      &&p.source_job_ids.every(job)&&new Set(p.source_job_ids).size===p.source_job_ids.length
      &&p.source_job_ids[0]===p.root_job_id&&p.source_job_ids.at(-1)===p.source_job_id;
  }
  const current=(e,version)=>selected===e&&epoch===version;
  function deniedBudget(p,e){
    const max=e.source.provider==='none'?0:e.source.depth==='planned'?9:e.source.depth==='deep'?6:4;
    return p?.available===false&&p.reason==='original_call_budget_exhausted'&&p.source_job_id===e.source.id
      &&p.automatic_retry===false&&p.model_calls===0&&Number.isInteger(p.prior_provider_calls)
      &&p.prior_provider_calls>=p.max_provider_calls&&p.prior_provider_calls<=max
      &&validBudget(p,e,max);
  }
  function open(e,id){
    if(get('run-button')?.disabled){e.notice='Đang theo dõi tác vụ khác; mở lại phiên đã tạo sau khi tác vụ đó kết thúc.';paint();return;}
    document.dispatchEvent(new CustomEvent('issueintel:stagerecoverycreated',{detail:{job_id:id,source_job_id:e.source.id}}));
  }
  async function check(e){
    if(e.busy)return;const version=epoch;e.busy=true;e.preview=null;e.notice=null;e.denied=null;paint();
    try{
      const value=await api('/api/jobs/'+e.source.id+'/stage-recovery');
      if(current(e,version)){
        if(valid(value,e)&&validChain(value)){e.preview=value;e.consent=false;}
        else if(deniedBudget(value,e))e.denied=value;
        else e.notice='Chưa có journal hợp lệ, nguồn còn đang chạy hoặc policy đã thay đổi.';
      }
    }catch(_){if(current(e,version))e.notice='Chưa kiểm tra được các stage đã lưu; kiểm tra server local rồi thử lại.';}
    finally{e.busy=false;if(current(e,version))paint();}
  }
  async function submit(e){
    if(e.busy||!e.preview)return;
    if(get('run-button')?.disabled){e.notice='Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.';paint();return;}
    const p=e.preview,version=epoch;let attempt=e.attempts.get(p.binding_sha256);
    if(!attempt){
      if(!e.consent)return;
      attempt={request:{binding_sha256:p.binding_sha256,confirmation:p.requires_api?
        'resume_product_stages_with_api_cost':'resume_product_stages_without_api'}};
      e.attempts.set(p.binding_sha256,attempt);
    }
    e.busy=true;e.notice=null;paint();
    try{
      const value=await api('/api/jobs/'+e.source.id+'/stage-recovery',attempt.request);
      if(!job(value?.id)||value.mode!=='stage_resume'||value.provider!==e.source.provider||value.depth!==e.source.depth
        ||value.stage_recovery_source?.source_job_id!==e.source.id||value.stage_recovery_source?.binding_sha256!==p.binding_sha256
        ||!['queued','running','completed','failed','interrupted','cancelled'].includes(value.status))throw Error('Unbound recovery acknowledgement');
      attempt.created=value.id;
      if(current(e,version)){e.notice='Đã nhận phiên phục hồi; stage cũ và chi phí nguồn giữ nguyên.';open(e,value.id);}
    }catch(_){if(current(e,version))e.notice='Chưa nhận được xác nhận; kiểm trạng thái bằng đúng yêu cầu đã gửi, không tự chạy lại.';}
    finally{e.busy=false;if(current(e,version))paint();}
  }
  function paint(){
    const area=get('stage-recovery-content');area.replaceChildren();const e=selected;if(!e)return;
    const version=epoch,revision=++frame;
    function button(label,action){const b=node('button',t(label));b.type='button';b.disabled=e.busy;
      b.onclick=()=>{if(current(e,version)&&frame===revision&&!b.disabled)return action();};return b;}
    area.append(node('p',t('Phiên nguồn: ')+e.source.id,'hash-note'),
      node('p',t('Dùng lại stage đã lưu, kiểm nguồn và tiếp tục phần còn thiếu tại commit gốc.'),'notice'));
    const message=node('p',e.notice?t(e.notice):e.busy?t('Đang kiểm journal và nguồn…'):'','notice');message.setAttribute('role','status');area.append(message);
    area.append(button('Kiểm tra các stage để tiếp tục',()=>check(e)));
    if(e.denied)area.append(node('p',t('Đã dùng {used}/{max} lượt thử trong giới hạn gốc. Không gọi thêm model hoặc đặt lại ngân sách khi phục hồi.',
      {used:e.denied.prior_provider_calls,max:e.denied.max_provider_calls}),'notice'));
    const budget=e.preview||e.denied;
    if(budget?.budget_scope==='planned_continuation_remaining')area.append(node('p',t('Phiên cha đã dùng {used}/{max} lượt. Giới hạn phục hồi bên dưới là phần còn lại của cùng ngân sách.',
      {used:budget.inherited_root_provider_calls,max:budget.root_max_provider_calls}),'notice'));
    const p=e.preview;if(!p)return;const attempt=e.attempts.get(p.binding_sha256);
    area.append(node('p','Commit '+p.commit_sha,'hash-note'),node('p',t('Stage đã lưu: {stages}',{stages:p.completed_stages.map(stageName).join(' → ')}),'hash-note'),
      node('p',t('Bước còn thiếu: {stage}',{stage:stageName(p.next_stage)})),
      node('p',t('Lượt thử đã ghi: {used}/{max}; gồm cả lượt lỗi hoặc chưa rõ đã tới provider.',{used:p.prior_provider_calls,max:p.max_provider_calls}),'notice'),
      node('p',t(p.requires_api?'Phần còn lại cần API. Chi phí trước đó không được hoàn lại hoặc đặt lại.':'Phần còn lại không cần API; vẫn đọc lại Git để kiểm nguồn.'),'notice'),
      node('p','Source SHA256 '+p.binding_sha256,'hash-note'));
    if(p.source_job_ids?.length>1)area.append(node('p',t('Lượt gốc: {id}',{id:p.root_job_id}),'hash-note'),
      node('p',t('Đã ghép nguồn từ {count} phiên. Mọi lượt thử trước đó vẫn nằm trong giới hạn gốc; không cấp lại ngân sách.',{count:p.source_job_ids.length}),'notice'));
    if(attempt?.created)area.append(node('p',t('Phiên phục hồi đã tạo: {id}',{id:attempt.created}),'hash-note'),button('Mở phiên phục hồi đã tạo',()=>open(e,attempt.created)));
    if(attempt)area.append(node('p',t('Yêu cầu đã khóa; lỗi, hủy hoặc restart không tự tạo lượt chạy mới.'),'notice'));
    else{
      const label=node('label',undefined,'checkbox'),consent=node('input');consent.type='checkbox';consent.id='stage-recovery-consent';consent.checked=e.consent;consent.disabled=e.busy;
      label.append(consent,node('span',t(p.requires_api?'Tôi đồng ý tiếp tục với issue và code đã khóa, có thể phát sinh chi phí API.':'Tôi xác nhận phục hồi phần còn lại mà không gọi API.')));
      consent.onchange=()=>{if(current(e,version)&&frame===revision&&!consent.disabled){e.consent=consent.checked;start.disabled=e.busy||!e.consent;}};area.append(label);
    }
    const start=button(attempt?'Kiểm trạng thái yêu cầu phục hồi':'Tiếp tục phần còn thiếu',()=>submit(e));start.disabled=e.busy||!attempt&&!e.consent;area.append(start);
  }
  function show(row){
    const original=['example','local','github','planned_continuation'].includes(row?.mode),sourceHash=original?row?.stage_checkpoints?.execution_input?.sha256:row?.pipeline_context_sha256;
    const allowed=row&&job(row.id)&&['failed','cancelled','interrupted'].includes(row.status)&&(original||row.mode==='stage_resume')
      &&['none','groq'].includes(row.provider)&&['quick','deep','planned'].includes(row.depth)&&hash(sourceHash)
      &&!(original&&row.provider==='groq'&&row.depth!=='planned'&&row.stage_checkpoints?.verification_input);
    get('stage-recovery-panel').hidden=!allowed;
    if(!allowed){selected=null;epoch++;return;}
    const key=row.id+':'+sourceHash;let e=states.get(key);
    if(!e){e={source:{id:row.id,mode:row.mode,provider:row.provider,depth:row.depth},preview:null,attempts:new Map(),busy:false,notice:null,consent:false};states.set(key,e);}
    if(selected!==e){selected=e;epoch++;}paint();
  }
  document.addEventListener('issueintel:languagechange',paint);
  window.IssueIntelStageRecovery={show};
})();

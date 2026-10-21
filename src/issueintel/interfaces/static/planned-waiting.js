/* Explicit, source-bound continuation of a waiting Planned investigation. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t,cache=new Map();let active=null;
  const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const job=value=>typeof value==='string'&&/^[a-f0-9]{24}$/.test(value);
  const count=value=>Number.isSafeInteger(value)&&value>=0;
  const keys=['retrieval_rounds','generation_passes','verifier_passes','provider_calls','added_evidence'];
  const statuses=['queued','running','completed','failed','interrupted','cancelled'];
  const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
  const fields=['observed_behavior','expected_behavior','reproduction','environment'];
  const prompts={
    observed_behavior:{en:'What happens when the problem occurs? Include the exact error or output if available, and say when you do not know.',
      vi:'Khi gặp vấn đề, thực tế xảy ra điều gì? Nếu có, hãy ghi nguyên văn lỗi hoặc kết quả; nếu chưa biết, hãy nói rõ.'},
    expected_behavior:{en:'What behavior did you expect, and why? Include the intended result or relevant requirement if available; unknown is a valid answer.',
      vi:'Bạn mong đợi hành vi nào và vì sao? Nếu có, hãy nêu kết quả dự kiến hoặc yêu cầu liên quan; có thể trả lời là chưa biết.'},
    reproduction:{en:'Which minimal steps or input reproduce the problem? Include an example if available, or say that you have not reproduced it.',
      vi:'Các bước hoặc đầu vào tối thiểu nào tái hiện được vấn đề? Nếu có, hãy đưa ví dụ; nếu chưa tái hiện được, hãy nói rõ.'},
    environment:{en:'Which software/runtime versions and operating system were involved? Include relevant configuration if known; leave unknown details explicitly unknown.',
      vi:'Vấn đề xảy ra với phiên bản phần mềm/runtime và hệ điều hành nào? Nếu biết, hãy nêu cấu hình liên quan; ghi rõ chi tiết nào chưa biết.'}
  };
  const current=b=>active===b&&b.target.isConnected!==false;
  const sameCounts=(left,right)=>left&&right&&keys.every(key=>count(left[key])&&left[key]===right[key]);
  const canonical=value=>Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'
    ?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}':JSON.stringify(value);
  const exactKeys=(value,expected)=>value&&Object.keys(value).sort().join('|')===expected.slice().sort().join('|');
  function validPlan(plan){
    try{
      const waiting=plan.waiting,questions=waiting?.questions;
      if(plan.schema!=='planned_investigation_v3'||plan.policy!=='bounded_planner_v3'||!sha(plan.sha256)||!sha(plan.policy_sha256)
        ||!(plan.inherited===null||plan.inherited?.counts)
        ||waiting?.schema!=='planned_waiting_v1'||waiting.state!=='waiting_for_user'||waiting.reason!=='no_new_evidence_with_unreviewed_input'
        ||!exactKeys(waiting,['schema','state','reason','after_round','input_annotation_sha256','questions','remaining','sha256'])
        ||!Number.isInteger(waiting.after_round)||waiting.after_round<1||waiting.after_round!==plan.rounds.length
        ||!sha(waiting.input_annotation_sha256)||!sha(waiting.sha256)||plan.stop_reason!=='waiting_for_user'
        ||plan.final_decision?.action!=='ask'||plan.final_decision.reason!=='unreviewed_user_input'
        ||questions?.schema!=='issue_clarifications_v1'||questions.query_sha256!==plan.source.query_sha256
        ||!sha(questions.sha256)||!sha(questions.cue_text_sha256)
        ||!['public_query','user_content_excluding_generated_questions'].includes(questions.cue_basis)
        ||(questions.cue_basis==='public_query'&&questions.cue_text_sha256!==questions.query_sha256)
        ||!Array.isArray(questions.questions)||!questions.questions.length||questions.questions.length>4)return false;
      const offered=questions.questions.map(question=>question?.field);
      if(new Set(offered).size!==offered.length||offered.join('|')!==fields.filter(field=>offered.includes(field)).join('|'))return false;
      for(const q of questions.questions){if(q.id!=='missing:'+q.field||q.reason!=='cue_not_found'||!prompts[q.field]
        ||q.prompts?.vi!==prompts[q.field].vi||q.prompts?.en!==prompts[q.field].en)return false;}
      const round=plan.rounds.at(-1);
      if(round.action!=='retrieve'||round.added_ids?.length!==0||!round.search?.queries?.length
        ||round.input_annotation_sha256!==waiting.input_annotation_sha256)return false;
      const inherited=plan.inherited?.counts||Object.fromEntries(keys.map(key=>[key,0])),limits=plan.limits;
      if(!inherited||!limits||!exactKeys(inherited,keys)||!exactKeys(waiting.remaining,keys)||!keys.every(key=>count(inherited[key])))return false;
      const debit=plan.inherited?.recovery_attempt_debit??0;
      if(!count(debit)||plan.inherited&&Object.hasOwn(plan.inherited,'recovery_attempt_debit')&&debit===0
        ||inherited.provider_calls-debit<inherited.generation_passes||inherited.provider_calls-debit>2*inherited.generation_passes)return false;
      const local={retrieval_rounds:plan.rounds.length,generation_passes:plan.generation_passes,
        verifier_passes:plan.verifier_passes,provider_calls:plan.provider_calls,
        added_evidence:plan.rounds.reduce((sum,row)=>sum+row.added_ids.length,0)};
      const maxima={retrieval_rounds:limits.max_retrieval_rounds,generation_passes:limits.max_generation_passes,
        verifier_passes:limits.max_verifier_passes,provider_calls:limits.max_provider_calls,added_evidence:limits.max_added_total};
      if(!keys.every(key=>count(local[key])&&count(maxima[key])&&count(waiting.remaining?.[key])
        &&waiting.remaining[key]===maxima[key]-inherited[key]-local[key]))return false;
      return waiting.remaining.generation_passes>=1&&waiting.remaining.provider_calls>=3&&waiting.remaining.verifier_passes===1;
    }catch(_){return false;}
  }
  function validPreview(value,b){
    return value&&value.source_job_id===b.job&&value.source_pack_sha256===b.sourcePackSha256
      &&value.workflow_plan_sha256===b.plan.sha256&&value.waiting_sha256===b.plan.waiting.sha256
      &&value.policy_sha256===b.plan.policy_sha256&&typeof value.available==='boolean'
      &&sameCounts(value.remaining,b.remaining)&&value.waiting?.sha256===b.plan.waiting.sha256
      &&canonical(value.waiting)===canonical(b.plan.waiting)
      &&(value.continuation_job===null||validJob(value.continuation_job));
  }
  const validJob=value=>value&&job(value.id)&&value.mode==='planned_continuation'&&statuses.includes(value.status);
  function recoveryDebit(plan,pack){
    if(!pack.recovered_from)return 0;
    const link=pack.recovered_from,source=link.source,value=pack.stage_recovery;
    if(link.schema!=='product_stage_resume_v1'||link.automatic_retry!==false||plan.inherited!==null
      ||value?.schema!=='product_pipeline_recovery_v1'||source?.provider!=='groq'||source.depth!=='planned'
      ||source.context_sha256!==value.context_sha256||source.core_policy_sha256!==value.policy_sha256
      ||source.prior_provider_calls!==value.inherited_provider_calls||!sha(source.sha256)
      ||!count(value.inherited_provider_calls)||!count(value.new_provider_calls)||value.max_provider_calls!==9
      ||value.call_count_kind!=='attempt_upper_bound'||value.automatic_retry!==false||value.billing_verified!==false)return null;
    const total=value.inherited_provider_calls+value.new_provider_calls;
    if(total<plan.provider_calls||total>plan.limits.max_provider_calls)return null;
    return total-plan.provider_calls;
  }
  function remainingFor(plan,pack){
    const debit=recoveryDebit(plan,pack);if(debit===null)return null;
    return {...plan.waiting.remaining,provider_calls:plan.waiting.remaining.provider_calls-debit};
  }
  const language=()=>document.documentElement.lang==='en'?'en':'vi';
  function cancel(){active=null;}
  function button(label,action,b){const el=node('button',t(label));el.type='button';el.onclick=()=>{if(current(b)&&!el.disabled)return action();};return el;}
  function repaint(entry){if(active?.entry===entry)paint(active);}
  function open(b,row){if(!current(b)||!validJob(row))return;
    document.dispatchEvent(new CustomEvent('issueintel:plannedcontinuation-created',{detail:{source_job_id:b.job,
      source_pack_sha256:b.sourcePackSha256,job_id:row.id}}));
  }
  function paint(b){
    if(!current(b))return;const {entry,plan,target}=b;target.replaceChildren();const waiting=plan.waiting;
    target.append(node('h3',t('Bổ sung dữ kiện để tiếp tục Planned')),
      node('p',t('Tìm kiếm vừa không có bằng chứng mới. Những câu hỏi theo cue này cần bạn xem lại; thiếu cue không chứng minh thiếu thông tin.'),'notice'),
      node('p',t('Có thể trả lời một phần hoặc ghi “chưa biết”. Câu trả lời là dữ kiện chưa xác minh, không phải bằng chứng lịch sử hoặc kết luận nguyên nhân.'),'notice'));
    const remaining=node('dl',undefined,'workflow-budgets');
    for(const [key,label] of [['retrieval_rounds','Vòng tìm thêm còn lại'],['generation_passes','Lượt sinh còn lại'],
      ['verifier_passes','Lượt verifier còn lại'],['provider_calls','Call API còn lại'],['added_evidence','Trích đoạn mới còn lại']]){
      const row=node('div');row.append(node('dt',t(label)),node('dd',String(b.remaining[key])));remaining.append(row);
    }
    target.append(remaining,node('p',t('Đây là phần ngân sách chưa dùng của cùng chuỗi. Tiếp tục giữ commit và bằng chứng đã có; không đặt lại giới hạn.'),'notice'));
    if(b.remaining.provider_calls!==waiting.remaining.provider_calls)target.append(node('p',
      t('Đã trừ thêm {calls} lượt thử trước khi phục hồi, gồm lượt lỗi hoặc chưa rõ đã tới provider; đây không phải hóa đơn.',
        {calls:waiting.remaining.provider_calls-b.remaining.provider_calls}),'notice'));
    const form=node('form',undefined,'planned-waiting-form');form.onsubmit=event=>{event.preventDefault();return submit(b);};
    for(const question of waiting.questions.questions){
      const label=node('label',undefined,'planned-waiting-question'),caption=node('span',question.prompts[language()]);
      const input=node('textarea');input.rows=3;input.maxLength=4000;input.value=entry.answers[question.id]||'';input.disabled=entry.busy||!!entry.preview?.continuation_job;
      input.setAttribute('aria-label',question.prompts[language()]);input.dataset.questionId=question.id;
      input.oninput=()=>{if(current(b)){entry.answers[question.id]=input.value;updateSubmit(b);}};
      label.append(caption,input);form.append(label);
    }
    form.append(node('p',t('Bản nháp chỉ giữ trong bộ nhớ trang này; tải lại trang sẽ mất câu trả lời chưa gửi.'),'field-note'));
    const controls=node('div',undefined,'planned-waiting-controls');
    const check=button('Kiểm tra khả năng tiếp tục',()=>preview(b),b);check.disabled=entry.busy;controls.append(check);
    if(entry.preview){
      if(entry.preview.continuation_job){const existing=entry.preview.continuation_job;
        controls.append(node('p',t('Đã có một phiên tiếp tục cho nguồn này. Không tạo thêm lời gọi từ yêu cầu lặp.'),'notice'),
          node('p',existing.id+' · '+t(existing.status),'hash-note'),button('Mở phiên Planned tiếp tục',()=>open(b,existing),b));
      }else if(!entry.preview.available)controls.append(node('p',t('Nguồn này hiện chưa thể tiếp tục. Kiểm policy, snapshot, nguồn và ngân sách trên máy trước khi kiểm lại.'),'notice'));
      else controls.append(node('p',t('Đã kiểm nguồn và phần ngân sách còn lại. Chưa có lời gọi model mới.'),'notice'));
    }
    const label=node('label',undefined,'checkbox'),consent=node('input');consent.type='checkbox';consent.checked=entry.consent;
    consent.disabled=entry.busy||!entry.preview?.available||!!entry.preview?.continuation_job||entry.uncertain;
    consent.onchange=()=>{if(current(b)){entry.consent=consent.checked;updateSubmit(b);}};
    label.append(consent,node('span',t('Tôi đồng ý gửi câu trả lời và nguồn điều tra tới Groq để tiếp tục trong ngân sách còn lại, có chi phí API.')));
    const proceed=button('Tiếp tục Planned với dữ kiện này',()=>submit(b),b);b.proceed=proceed;controls.append(label,proceed);
    if(entry.busy){const status=node('p',t(entry.busy==='preview'?'Đang kiểm nguồn tiếp tục…':'Đang đăng ký phiên tiếp tục…'),'notice');status.setAttribute('role','status');controls.append(status);}
    if(entry.uncertain)controls.append(node('p',t('Chưa biết yêu cầu đã được nhận chưa. Kiểm lại khả năng tiếp tục để tìm phiên đã có; không tự gửi lại lời gọi trả phí.'),'notice'));
    if(entry.error){const error=node('p',t(entry.error),'error');error.setAttribute('role','alert');controls.append(error);}
    form.append(controls);target.append(form);
    const source=node('details');source.append(node('summary',t('Nguồn của lần chờ')),node('p',b.job,'hash-note'),
      node('p','Pack SHA256 '+b.sourcePackSha256,'hash-note'),node('p','Plan SHA256 '+plan.sha256,'hash-note'),
      node('p','Waiting SHA256 '+waiting.sha256,'hash-note'),node('p','Policy SHA256 '+plan.policy_sha256,'hash-note'));
    target.append(source);updateSubmit(b);
  }
  function answers(b){return b.plan.waiting.questions.questions.filter(q=>(b.entry.answers[q.id]||'').trim())
    .map(q=>({question_id:q.id,answer:b.entry.answers[q.id]}));}
  function updateSubmit(b){if(b.proceed)b.proceed.disabled=!!b.entry.busy||!b.entry.preview?.available||!!b.entry.preview?.continuation_job
    ||b.entry.uncertain||!b.entry.consent||!answers(b).length||answers(b).some(row=>Array.from(row.answer).length>4000);}
  async function preview(b){
    if(!current(b)||b.entry.busy)return;const {entry}=b;entry.busy='preview';entry.error=null;entry.consent=false;paint(b);
    try{const value=await api('/api/jobs/'+b.job+'/planned-continuation');
      if(!validPreview(value,b))throw Error('Invalid source-bound continuation preview');
      entry.preview=value;entry.uncertain=false;
    }catch(_){entry.preview=null;entry.error='Chưa kiểm được nguồn tiếp tục. Không tự gửi yêu cầu trả phí; có thể chủ động kiểm lại.';}
    finally{entry.busy=null;repaint(entry);}
  }
  async function submit(b){
    updateSubmit(b);if(!current(b)||b.proceed.disabled)return;const {entry}=b;
    const body={source_pack_sha256:b.sourcePackSha256,workflow_plan_sha256:b.plan.sha256,waiting_sha256:b.plan.waiting.sha256,
      policy_sha256:b.plan.policy_sha256,answers:answers(b),language:language(),confirmation:'continue_planned_with_api_cost'};
    entry.busy='submit';entry.error=null;entry.consent=false;entry.uncertain=true;paint(b);
    try{const row=await api('/api/jobs/'+b.job+'/planned-continuation',body);if(!validJob(row))throw Error('Invalid continuation job');
      entry.preview={...entry.preview,continuation_job:row};entry.uncertain=false;
      if(active?.entry===entry)open(active,row);
    }catch(_){entry.error='Chưa xác nhận được phiên tiếp tục. Không tự gửi lại; kiểm nguồn để tìm tác vụ đã đăng ký.';}
    finally{entry.busy=null;repaint(entry);}
  }
  function render(target,plan,id,pack,options={}){
    cancel();if(!job(id)||!sha(options.sourcePackSha256)||!validPlan(plan)||pack?.workflow_plan!==plan)return;
    const remaining=remainingFor(plan,pack);
    if(!remaining){target.append(node('p',t('Không xác định được ngân sách attempt của phiên phục hồi; chưa thể tiếp tục Planned.'),'notice'));return;}
    const key=id+':'+options.sourcePackSha256+':'+plan.sha256+':'+plan.waiting.sha256;
    if(!cache.has(key))cache.set(key,{answers:{},consent:false,preview:null,busy:null,error:null,uncertain:false});
    const entry=cache.get(key);entry.consent=false;
    const area=node('section',undefined,'planned-waiting');target.append(area);
    active={target:area,plan,job:id,sourcePackSha256:options.sourcePackSha256,entry,remaining};paint(active);
  }
  document.addEventListener('issueintel:packchange',cancel);
  document.addEventListener('issueintel:languagechange',()=>{if(active){active.entry.consent=false;paint(active);}});
  window.IssueIntelPlannedWaiting=Object.freeze({render,cancel,validPlan,recoveryDebit});
})();

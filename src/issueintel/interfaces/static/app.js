'use strict';
const t = window.IssueIntelUI.t;
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="issueintel-session"]').content;
let catalog = [], pack = null, currentJob = null, activeJob = null, activeTab = 'overview';
let catalogInfo=null, jobRows=[], comparisonResult=null, lastMessage=null;
let intake=null;
let reportView=null;
let hypothesisReview=null;
let journalData=null;
let relatedResult=null;
let planData=null;
let viewGeneration=0;
let stageView=null;
let usageView=null,usageInfo=null;
let typesafeStatus=null,typesafeStatusLoaded=false,typesafeStatusRequested=false;
async function api(path, body) {
  let response;
  try {response = await fetch(path, {method:body === undefined ? 'GET':'POST', headers:{'X-IssueIntel-Token':token, ...(body === undefined ? {}:{'Content-Type':'application/json'})}, ...(body === undefined ? {}:{body:JSON.stringify(body)})});}
  catch(_) {throw new Error(t('Không kết nối được server local.'));}
  const data = await response.json();
  if (!response.ok) throw new Error(t('Lỗi yêu cầu. Kiểm tra đầu vào hoặc dữ liệu cục bộ.'));
  return data;
}
async function downloadHandoff(id,request,preview,isCurrent){
  const response=await fetch('/api/jobs/'+id+'/handoff-export',{method:'POST',headers:{'X-IssueIntel-Token':token,'Content-Type':'application/json'},body:JSON.stringify(request)});
  if(!response.ok||response.headers.get('X-IssueIntel-Dossier-SHA256')!==preview.source.dossier_sha256)throw Error('Handoff source changed');
  const bytes=await response.arrayBuffer();
  const checksum=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
  if(checksum!==preview.archive_sha256||bytes.byteLength!==preview.archive_bytes)throw Error('Handoff bytes changed');
  if(!isCurrent())return false;
  const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'})),link=node('a');link.href=url;link.download='issueintel-'+id+'-handoff.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  return true;
}
function node(tag, text, className) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(className) el.className = className; return el; }
function message(text, error=false) {lastMessage={key:window.IssueIntelUI.sourceKey(text),error};$('message').textContent=text;$('message').className=error?'error':'';}
function updateTypesafeControls() {
  const eligible=['local','github','example'].includes($('mode').value)&&$('depth').value==='planned';
  const control=$('use-typesafe');
  $('typesafe-controls').hidden=!eligible;
  control.disabled=!(eligible&&$('use-groq').checked&&typesafeStatus?.ready===true&&typesafeStatus.enabled===true&&typesafeStatus.key_configured===true);
  if(control.disabled)control.checked=false;
  let key='Đang kiểm cấu hình Typesafe trên máy…';
  if(typesafeStatusLoaded){
    if(!typesafeStatus)key='Chưa đọc được cấu hình Typesafe. Tải lại trang sau khi kiểm server local.';
    else if(!typesafeStatus.key_configured)key='Chưa có API key Typesafe trên máy.';
    else if(!typesafeStatus.enabled)key='Typesafe chưa được bật trong cấu hình. Cần bật và đặt ngân sách riêng trước khi dùng.';
    else if(typesafeStatus.ready!==true)key='Typesafe chưa sẵn sàng. Kiểm ngân sách request, USD/ngày, USD/tháng và bảng giá trước khi dùng.';
    else if(!$('use-groq').checked)key='Typesafe sẵn sàng. Bật Groq trước rồi chủ động chọn Jev cho phiên này.';
    else key='Typesafe sẵn sàng; chỉ gọi sau khi bạn chọn Jev và bắt đầu điều tra.';
  }
  $('typesafe-status').textContent=t(key);
}
async function loadTypesafeStatus() {
  if(typesafeStatusRequested)return;
  typesafeStatusRequested=true;
  try {typesafeStatus=await api('/api/typesafe/status');}catch(_){typesafeStatus=null;}
  typesafeStatusLoaded=true;
  updateTypesafeControls();
}
function updateDepthControls() {
  const allowed=['local','github','example'].includes($('mode').value);
  $('planned-depth-option').disabled=!allowed;
  if(!allowed && $('depth').value==='planned')$('depth').value='quick';
  const key=$('depth').value==='planned'
    ?'Planned dùng tín hiệu quan sát để giới hạn 1–3 vòng tìm thêm, mỗi vòng tối đa 3 truy vấn/6 trích đoạn mới (tổng 18). Khi bật Groq: tối đa 4 lượt sinh + 1 verifier; mỗi lượt sinh có thể sửa schema một lần (tối đa 9 call). Có thể dừng sớm; ledger và hủy tác vụ vẫn áp dụng. Đây chưa phải đánh giá độ khó hoặc chất lượng.'
    :'Deep với Groq có tối đa một vòng tìm thêm evidence và một lượt sinh bổ sung; mọi call vẫn qua ledger.';
  $('depth-note').dataset.i18n=key;$('depth-note').textContent=t(key);
  if(typeof updateTypesafeControls==='function')updateTypesafeControls();
}
function switchMode() { const mode=$('mode').value; $('main-dev-fields').hidden=mode!=='main_dev'; $('github-fields').hidden=mode!=='github'; $('local-fields').hidden=mode!=='local'; $('example-note').hidden=mode!=='example';updateDepthControls(); }
function filterCases() {
  const selected=$('case-id').value;
  const query=$('case-search').value.toLowerCase(); const rows=catalog.filter(c => (c.repository+' '+c.case_id+' '+c.summary).toLowerCase().includes(query));
  $('case-id').replaceChildren(...rows.map(c=>{const opt=node('option',c.case_id.replace('verified:','')+' · '+c.summary.slice(0,52));opt.value=c.case_id;return opt;}));
  if(!rows.length) {$('case-id').append(node('option',t("Không có ca phù hợp")));$('case-id').firstChild.value='';}
  else if(rows.some(row=>row.case_id===selected))$('case-id').value=selected;
}
function requestInput() {
  const mode=$('mode').value;const request={mode,provider:$('use-groq').checked?'groq':'none',depth:$('depth').value};
  if(['local','github','example'].includes(mode)&&request.depth==='planned'&&request.provider==='groq'&&typesafeStatus?.ready===true&&typesafeStatus.enabled===true&&typesafeStatus.key_configured===true&&$('use-typesafe').checked)request.controller='jev';
  if(mode==='main_dev') request.case_id=$('case-id').value;
  if(mode==='github') Object.assign(request,{issue_url:$('github-url').value,revision:$('github-revision').value});
  if(mode==='local') Object.assign(request,{repository:$('repository').value,revision:$('revision').value,issue_text:$('issue-text').value});
  return request;
}
async function submit(event) {
  event?.preventDefault();await runJob(requestInput());
}
async function runJob(request,existingJob=null) {
  window.IssueIntelClaimReview?.cancel();
  if($('run-button').disabled)return;
  window.IssueIntelStageRecovery?.show(null);
  window.IssueIntelFinalization?.show(null);
  window.IssueIntelDossier?.cancel();
  const finalizing=request.mode==='finalization',originView=viewGeneration;
  try {
    stageView=null;$('stage-artifacts').hidden=true;
    $('run-button').disabled=true;$('followup-button').disabled=true;message(t(finalizing?'Đang kiểm checkpoint và kết xuất hồ sơ đã lưu…':"Đang chuẩn bị snapshot và bằng chứng…"));
    const job=existingJob||await api('/api/investigations',request);activeJob=job.id;$('cancel-button').hidden=false;
    for(;;) {
      const state=await api('/api/jobs/'+job.id);
      const following=viewGeneration===originView;
      if(following)showUsage(state);
      if(['completed','failed','cancelled','interrupted'].includes(state.status)) {
        if(!following){
          message(t('Phiên {id} đã kết thúc; mở lại trong lịch sử để xem trạng thái và kết quả.',{id:job.id}));
        }else if(state.status==='completed') {
          await openJob(job.id);message(t("Đã lưu investigation pack cục bộ."));
          if(request.mode==='followup' && $('additional-context').value===request.additional_context)$('additional-context').value='';
        }
        else {message(t('Tác vụ không hoàn tất. Kiểm tra đầu vào, dữ liệu hoặc cấu hình provider; không tự thử lại.'),true);showStageArtifacts(state);}
        break;
      }
      message(finalizing?t('Đang kiểm checkpoint và kết xuất hồ sơ đã lưu…'):state.stage==='prepare_snapshot'?t("Đang kiểm query và snapshot…"):t("Đang truy xuất, điều tra và kiểm chứng…"));
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
  } catch(error) {message(error.message,true);}
  finally {activeJob=null;$('run-button').disabled=false;$('followup-button').disabled=false;$('cancel-button').hidden=true;await history();}
}
async function openJob(id) {
  window.IssueIntelClaimReview?.cancel();
  const generation=++viewGeneration;
  window.IssueIntelDossier?.cancel();
  window.IssueIntelFinalization?.show(null);
  window.IssueIntelHypothesisReview?.cancel();
  window.IssueIntelCheckJournal?.cancel();
  const loaded=await api('/api/jobs/'+id+'/pack.json');
  const [inspected,plan,cost,report,review,journal]=await Promise.allSettled([api('/api/jobs/'+id+'/intake'),api('/api/jobs/'+id+'/plan'),api('/api/jobs/'+id+'/usage'),api('/api/jobs/'+id+'/investigation-report.json'),readHypothesisReview(id),readCheckJournal(id)]);
  if(generation!==viewGeneration)return;
  if(!activeJob)message(t('Đang xem phiên: {id}',{id}));
  pack=loaded;intake=inspected.status==='fulfilled'?inspected.value:null;planData=plan.status==='fulfilled'?plan.value:null;currentJob=id;
  reportView=report.status==='fulfilled'?report.value:null;
  hypothesisReview=review.status==='fulfilled'?review.value:null;
  journalData=journal.status==='fulfilled'?journal.value:null;
  relatedResult=null;
  usageInfo=cost.status==='fulfilled'?cost.value:null;
  showUsage({id,accounting:usageInfo,...savedTypesafeUsage()});
  showStageArtifacts({id,stage_checkpoints:pack.stage_checkpoints});
  renderPack();
}
function renderPack() {
  if(!pack)return;
  const waiting=pack.workflow_plan?.schema==='planned_investigation_v3' && !!pack.workflow_plan.waiting;
  document.dispatchEvent(new CustomEvent('issueintel:packchange',{detail:{job_id:currentJob,
    planned_waiting:waiting,
    clarifications_available:intake?.clarifications_available===true,adaptive_available:intake?.clarifications_available===true,intake,pack,
    release_available:!pack.case.split && !pack.followup && pack.snapshot.protocol==='benchmark_base' && pack.case.case_id===pack.case.issue_url && /^https:\/\/github\.com\/[^/]+\/[^/]+\/issues\/[1-9][0-9]*$/.test(pack.case.case_id),
    context_available:!waiting && !pack.case.split && !pack.followup && !pack.continued_from && pack.snapshot.protocol==='benchmark_base' && pack.case.case_id===pack.case.issue_url && /^https:\/\/github\.com\/[^/]+\/[^/]+\/issues\/[1-9][0-9]*$/.test(pack.case.case_id),
    available:!pack.case.split && !pack.followup && pack.snapshot.protocol==='benchmark_base' && !pack.case.case_id.startsWith('verified:') && !pack.case.case_id.startsWith('live:')}}));
  $('empty-state').hidden=true;$('result').hidden=false;$('exports').hidden=false;
  $('followup-panel').hidden=waiting;$('followup-parent').textContent=t('Phiên nguồn: ')+currentJob;
  $('result-title').textContent=pack.case.case_id;
  $('viewed-job').hidden=false;$('viewed-job').textContent=t('Đang xem phiên: {id}',{id:currentJob});
  const strict=pack.leakage_audit.strict_issue_time_verified;
  const boxes=[[t("Snapshot"),pack.snapshot.commit_sha.slice(0,12)],[t("Bằng chứng"),t("{count} nguồn",{count:pack.evidence.length})],[t("Thời gian"),strict?t("Strict issue-time"):t("Chưa xác minh lịch sử")]];
  $('summary').replaceChildren(...boxes.map(([label,value])=>{const box=node('div',undefined,'summary-box');box.append(node('small',label),node('strong',value));return box;}));
  renderTab();
}
function evidenceCard(e) {
  const card=node('article',undefined,'evidence-card');card.id='evidence-'+e.id;
  const head=node('div',undefined,'card-head');head.append(node('span',e.locator,'locator'),node('span',e.kind==='code'?'CODE':e.kind==='repository_context'?t('Context repository hiện tại'):'ISSUE','badge'));
  card.append(head,node('pre',e.text),node('div','SHA256 '+e.sha256+' · '+e.id,'hash-note'));
  if(e.kind==='repository_context'){
    card.append(node('p',t('Nguồn liên quan chưa xác nhận duplicate hay bản sửa; không phải proof lịch sử.'),'notice'));
    const url=new URL(e.provenance.source_url);
    if(url.protocol==='https:' && url.hostname==='github.com' && !url.username && !url.password){const link=node('a',t('Mở nguồn GitHub'));link.href=url.href;link.target='_blank';link.rel='noreferrer noopener';card.append(link);}
  }
  return card;
}
function citation(id) {
  const button=node('button',id,'citation');button.onclick=()=>{activeTab='evidence';renderTab();document.getElementById('evidence-'+id)?.scrollIntoView({behavior:'smooth',block:'center'});};return button;
}
function recommendedText(text) {
  return !pack.annotation && text==='Provide a traceback, relevant component or reproducible example'?t(text):text;
}
function showStageArtifacts(row) {
  stageView=row;
  window.IssueIntelStageRecovery?.show(row);
  window.IssueIntelResume?.show(row);
  window.IssueIntelFinalization?.show(row);
  const refs=row.stage_checkpoints || {},container=$('stage-list');container.replaceChildren();
  $('stage-artifacts').hidden=!Object.keys(refs).length;
  if(!Object.keys(refs).length)return;
  container.append(node('p',t('Checkpoint của phiên: {id}',{id:row.id}),'hash-note'));
  container.append(node('p',t('Checkpoint là đầu ra từng bước, chưa phải báo cáo hoàn tất. Mở hoặc tải không gọi lại model.'),'notice'));
  for(const [stage,ref] of Object.entries(refs)){
    const button=node('button',t('Tải checkpoint: {stage}',{stage:t(stage)}));
    button.type='button';button.onclick=()=>downloadPath('/api/jobs/'+row.id+'/stages/'+stage,'issueintel-'+row.id+'-'+stage+'.json');
    container.append(button,node('p','SHA256 '+ref.sha256,'hash-note'));
  }
}
function showUsage(row){
  usageView=row;
  $('usage-panel').hidden=false;
  window.IssueIntelUsage.render($('usage-content'),row.accounting,row);
}
function savedTypesafeUsage(){
  const sidecar=pack?.controller_decisions;
  if(!sidecar)return {};
  return {controller:'jev',typesafe_decisions:sidecar.records,typesafe_receipts:Object.fromEntries(sidecar.records.filter(row=>row.receipt).map(row=>[row.receipt.reservation_id,row.receipt]))};
}
function planDrafts() {
  return [...document.querySelectorAll('[data-plan-step]')].map(form=>({id:form.dataset.planStep,
    status:form.querySelector('select').value,note:form.querySelector('textarea').value}));
}
function restorePlanDrafts(drafts) {
  for(const draft of drafts){
    const form=[...document.querySelectorAll('[data-plan-step]')].find(el=>el.dataset.planStep===draft.id);
    if(!form)continue;
    form.querySelector('select').value=draft.status;
    const note=form.querySelector('textarea');note.value=draft.note;note.required=['blocked','reported_done'].includes(draft.status);
  }
}
function renderTab() {
  window.IssueIntelMemory?.cancel();
  window.IssueIntelClaimReview?.cancel();
  document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button.dataset.tab===activeTab));
  const content=$('tab-content');content.replaceChildren();window.IssueIntelEvidenceGraph?.cancel();window.IssueIntelHypothesisReview?.cancel();window.IssueIntelCheckJournal?.cancel();window.IssueIntelProductChecks?.cancel();if(!pack)return;
  if(activeTab==='overview')window.IssueIntelReport?.render(content,reportView,currentJob,{
    pack_sha256:intake?.source_pack_sha256,commit_sha:pack.snapshot.commit_sha,
    snapshot_sha256:pack.snapshot.snapshot_sha256,query_sha256:intake?.query_sha256});
  window.IssueIntelDossier?.cancel();
  if(activeTab==='dossier')window.IssueIntelDossier?.render(content,currentJob,{
    available:intake?.check_journal_available===true,pack_sha256:intake?.source_pack_sha256,report_sha256:reportView?.sha256});
  if(activeTab==='evidence') content.append(...pack.evidence.map(evidenceCard));
  if(activeTab==='memory')window.IssueIntelMemory?.render(content,currentJob,pack,{
    available:intake?.check_journal_available===true,pack_sha256:intake?.source_pack_sha256});
  if(activeTab==='intake' && !intake)content.append(node('p',t('Chưa đọc được metadata; báo cáo nguồn vẫn có thể xuất.'),'notice'));
  if(activeTab==='plan')window.IssueIntelProductChecks?.render(content,reportView,currentJob,pack,{available:intake?.check_journal_available===true});
  if(activeTab==='plan')window.IssueIntelCheckJournal?.render(content,journalData,currentJob,reportView,hypothesisReviewSource(),{
    available:intake?.check_journal_available,reload:reloadCheckJournal,save:saveCheckObservation,export:exportCheckJournal,rerender:renderPlanPreservingDrafts});
  if(activeTab==='plan' && !planData)content.append(node('p',t('Chưa đọc được metadata; báo cáo nguồn vẫn có thể xuất.'),'notice'));
  if(activeTab==='plan' && planData){
    const checklist=node('details',undefined,'journal-checklist');checklist.append(node('summary',t('Trạng thái các bước trong kế hoạch')));content.append(checklist);
    const reload=node('button',t('Tải lại kế hoạch'));reload.onclick=async()=>{
      const selected=currentJob;
      try{const plan=await api('/api/jobs/'+selected+'/plan');if(currentJob===selected){planData=plan;renderTab();}}
      catch(error){message(error.message,true);}
    };
    const exportPlan=node('button',t('Xuất kế hoạch JSON'));exportPlan.onclick=()=>{
      const url=URL.createObjectURL(new Blob([JSON.stringify(planData,null,2)],{type:'application/json'}));
      const link=node('a');link.href=url;link.download='issueintel-'+planData.job_id+'-plan.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    checklist.append(node('p',t('Phiên bản kế hoạch: {revision}',{revision:planData.revision}),'notice'),
      node('p',t('Trạng thái và ghi chú do người dùng tự báo cáo; hệ thống không chạy code và chưa xác minh bản sửa.'),'notice'),reload,exportPlan);
    for(const step of planData.steps){
      const form=node('form',undefined,'hypothesis-card');form.dataset.planStep=step.id;form.append(node('h3',recommendedText(step.description)));
      const label=node('label',t('Trạng thái bước kiểm tra'));const status=node('select');status.id='status-'+step.id;label.htmlFor=status.id;
      for(const value of ['todo','in_progress','blocked','reported_done']){const option=node('option',t(value));option.value=value;status.append(option);}status.value=step.status;
      const noteLabel=node('label',t('Ghi chú hoặc quan sát'));const note=node('textarea');note.id='note-'+step.id;noteLabel.htmlFor=note.id;note.value=step.note;note.maxLength=2000;note.rows=3;
      const requireNote=()=>note.required=['blocked','reported_done'].includes(status.value);status.onchange=requireNote;requireNote();
      const save=node('button',t('Lưu bước kiểm tra'));save.type='submit';
      form.append(label,status,noteLabel,note,save);
      const selected=currentJob,expected=planData.sha256;
      form.onsubmit=async event=>{
        event.preventDefault();save.disabled=true;
        try{
          const plan=await api('/api/jobs/'+selected+'/plan',{expected_sha256:expected,step_id:step.id,status:status.value,note:note.value});
          if(currentJob===selected){const drafts=planDrafts().filter(d=>d.id!==step.id);planData=plan;renderTab();restorePlanDrafts(drafts);message(t('Đã lưu phiên bản kế hoạch mới.'));}
        }catch(error){message(t('Chưa lưu được. Kiểm tra ghi chú hoặc tải lại kế hoạch nếu phiên bản đã đổi.'),true);save.disabled=false;}
      };
      checklist.append(form);
    }
    const history=node('details');history.append(node('summary',t('Lịch sử thay đổi kế hoạch')),node('pre',JSON.stringify({source_pack_sha256:planData.source_pack_sha256,sha256:planData.sha256,history:planData.history},null,2)));checklist.append(history);
  }
  if(activeTab==='related') {
    const search=node('button',t('Tìm issue liên quan trong lịch sử local'));
    content.append(node('p',t('Chỉ tìm trong 50 phiên local gần nhất, cùng repository. Tương đồng chưa chứng minh duplicate; không đưa vào inference strict.'),'notice'),search);
    search.onclick=async()=>{
      const selected=currentJob;search.disabled=true;
      try {
        const result=await api('/api/related',{job_id:selected});
        if(currentJob===selected){relatedResult=result;renderTab();}
      }catch(error){message(error.message,true);search.disabled=false;}
    };
    if(relatedResult?.job_id===currentJob){
      content.append(node('p',t('Đã đọc {scanned} phiên; {issues} issue khác; bỏ qua {skipped} nguồn lỗi.',{scanned:relatedResult.scanned_completed,issues:relatedResult.distinct_other_issues,skipped:relatedResult.skipped_integrity}),'notice'));
      if(!relatedResult.results.length)content.append(node('p',t('Chưa tìm thấy ứng viên phù hợp trong phạm vi này.'),'notice'));
      for(const result of relatedResult.results){
        const card=node('article',undefined,'hypothesis-card');
        card.append(node('h3',result.case_id),node('span',t('Ứng viên liên quan · chưa xác nhận duplicate'),'badge'),
          node('p',t('Điểm lexical: {score}; không phải xác suất.',{score:result.score})),
          node('p',t('Từ khớp: ')+result.matched_terms.join(', ')),node('pre',result.snippet.text),
          node('p','Query SHA256 '+result.query_sha256,'hash-note'),node('p','Pack SHA256 '+result.pack_sha256,'hash-note'));
        const open=node('button',t('Mở báo cáo nguồn'));open.onclick=()=>{activeTab='evidence';openJob(result.job_id).catch(error=>message(error.message,true));};
        card.append(open);content.append(card);
      }
    }
  }
  if(activeTab==='intake' && intake) {
    content.append(node('h3',t('Loại issue: ')+t(intake.intent)),node('p',t('Phân loại dựa trên từ khóa, chưa xác minh. Có nhắc thông tin không có nghĩa là thông tin đầy đủ.'),'notice'));
    if(intake.cue_basis==='user_content_excluding_generated_questions'){
      content.append(node('p',t('Các dòng bên dưới thuộc bản nội dung người dùng đã loại câu hỏi tự sinh; câu trả lời vẫn chưa xác minh.'),'notice'));
      const source=node('details');source.append(node('summary',t('Nội dung dùng để kiểm tra từ khóa')),node('pre',intake.cue_text),node('p','SHA256 '+intake.cue_text_sha256,'hash-note'));content.append(source);
    }
    if(pack.followup?.clarification){
      const receipt=pack.followup.clarification,saved=node('details');
      saved.append(node('summary',t('Câu trả lời đã lưu (chưa xác minh)')),
        node('p','Question SHA256 '+receipt.questions_sha256,'hash-note'),node('p','Answers SHA256 '+receipt.sha256,'hash-note'));
      if(pack.followup.question_job_id){
        const sourceId=pack.followup.parent_job_id,open=node('button',t('Mở phiên nguồn của câu hỏi'));
        open.type='button';open.onclick=()=>openJob(sourceId);
        saved.append(node('p',t('Giả thuyết và citation của câu hỏi thuộc phiên nguồn: {id}',{id:sourceId}),'notice'),open);
      }
      for(const answer of receipt.answers){
        const card=node('article',undefined,'hypothesis-card');
        card.append(node('h3',t(answer.field)),node('p',answer.question),node('pre',answer.answer));
        if(answer.purpose)card.append(node('p',answer.purpose),node('p',answer.answer_use),node('p',answer.hypothesis_ids.join(' · '),'hash-note'));
        saved.append(card);
      }
      content.append(saved);
    }
    if(pack.followup?.check_observations){
      const receipt=pack.followup.check_observations,saved=node('details');
      saved.append(node('summary',t('Quan sát dùng cho phiên này (chưa xác minh)')),
        node('p',t('Các quan sát giữ đúng phiên bản đã chọn; đính chính về sau được xem ở báo cáo nguồn.'),'notice'),
        node('p','Check plan SHA256 '+receipt.source.plan_sha256,'hash-note'),
        node('p','Check observations SHA256 '+receipt.journal_sha256,'hash-note'));
      const sourceId=pack.followup.parent_job_id,open=node('button',t('Mở báo cáo nguồn'));
      open.type='button';open.onclick=()=>openJob(sourceId);saved.append(open);
      for(const event of receipt.observations){
        const observation=event.observation,card=node('article',undefined,'hypothesis-card');
        card.append(node('h3',event.observation_id+' · '+observation.check_id+' · '+observation.outcome));
        for(const [label,key] of [['Điều kiện, đầu vào và môi trường thử','conditions'],['Kết quả quan sát được','observed_result'],['Diễn giải hoặc lý do chưa chạy','interpretation']])
          card.append(node('p',t(label)),node('pre',observation[key]));
        card.append(node('p',t('Commit thực sự đã thử (tùy chọn)')+': '+(observation.tested_commit_sha||t('Chưa biết')),'hash-note'));
        saved.append(card);
      }
      content.append(saved);
    }
    for(const [field,details] of Object.entries(intake.fields)){
      const card=node('article',undefined,'hypothesis-card');card.append(node('h3',t(field)+' · '+t(details.status)));
      for(const source of details.sources)card.append(node('pre',t('Dòng {line}: ',{line:source.line})+source.text),node('p','SHA256 '+source.text_sha256,'hash-note'));
      if(details.status==='not_found')card.append(node('p',t('Cần xem lại hoặc bổ sung mục này.'),'notice'));content.append(card);
    }
    if(pack.followup)content.append(node('p',t('Dữ kiện bổ sung chưa xác minh; không kế thừa chứng nhận query lịch sử.'),'notice'),node('pre',JSON.stringify({parent_job_id:pack.followup.parent_job_id,parent_pack_sha256:pack.followup.parent_pack_sha256,depth:pack.followup.depth},null,2)));
  }
  if(activeTab==='hypotheses') {
    window.IssueIntelHypothesisReview?.render(content,hypothesisReview,currentJob,reportView,hypothesisReviewSource(),{reload:reloadHypothesisReview,export:exportHypothesisReview});
    if(pack.annotation?.abstention) content.append(node('p','Abstention: '+t(pack.annotation.abstention.label)+' — '+pack.annotation.abstention.reason,'notice'));
  }
  if(activeTab==='verification') {
    const claims=node('div');content.append(claims);window.IssueIntelClaimReview?.render(claims,reportView,currentJob,pack,{available:intake?.check_journal_available===true});
    content.append(node('h3',t('Nhận xét toàn giả thuyết đã lưu')));
    content.append(node('p',t("Citation hợp lệ không đồng nghĩa đã chứng minh nguyên nhân. Critique bằng model được báo riêng với kiểm người."),'notice'));
    for(const review of pack.verification?.reviews || []) {const card=node('article',undefined,'hypothesis-card');card.append(node('h3',review.hypothesis_id+' · '+t(review.assessment)),node('p',review.reason),...review.evidence_ids.map(citation));content.append(card);}
    if(!pack.verification)content.append(node('p',t("Chưa có verifier pass cho lần điều tra này."),'notice'));
    content.append(node('h3',t("Bước kiểm tra tiếp theo")));
    const list=node('ol');for(const step of pack.recommended_steps) list.append(node('li',recommendedText(step)));content.append(list);
  }
  if(activeTab==='trace') {
    window.IssueIntelWorkflow?.render(content,pack.workflow_plan,currentJob,pack,{sourcePackSha256:reportView?.source?.pack_sha256});
    if(pack.refinement)content.append(node('p',t('Vòng truy xuất bổ sung: {rounds}/1 · lượt sinh: {passes}',{rounds:pack.refinement.retrieval_rounds,passes:pack.refinement.generation_passes}),'notice'));
    content.append(node('pre',JSON.stringify({stop_reason:pack.stop_reason,trace:pack.trace,refinement:pack.refinement,workflow_plan:pack.workflow_plan,controller_decisions:pack.controller_decisions,stage_checkpoints:pack.stage_checkpoints,resumed_from:pack.resumed_from,finalized_from:pack.finalized_from,context_selection:pack.context_selection,leakage_audit:pack.leakage_audit},null,2)));
  }
  if(activeTab==='graph')window.IssueIntelEvidenceGraph.render(content,currentJob);
  if(activeTab==='usage')window.IssueIntelUsage.render(content,usageInfo,{id:currentJob,...savedTypesafeUsage()});
  if(activeTab==='github') {
    const button=node('button',t("Tạo bản nháp comment để duyệt"));
    content.append(node('p',t("Bản nháp chứa snapshot, code links và kết quả điều tra. Chỉ đăng sau khi bạn xem nội dung và xác nhận đúng issue."),'notice'),button);
    button.onclick=async()=>{
      try {
        const reviewedJob=currentJob;const draft=await api('/api/jobs/'+reviewedJob+'/github-preview',{});
        const target=node('p',t("Issue đích: ")+draft.issue_url,'notice');const preview=node('pre',draft.body);
        const label=node('label',undefined,'checkbox');const check=node('input');check.type='checkbox';
        label.append(check,node('span',t("Tôi đã duyệt nội dung này và đồng ý đăng lên issue đích.")));
        const publish=node('button',t("Đăng comment đã duyệt"));publish.disabled=true;
        check.onchange=()=>publish.disabled=!check.checked;
        publish.onclick=async()=>{
          if(!check.checked)return;publish.disabled=true;
          try {const receipt=await api('/api/jobs/'+reviewedJob+'/github-publish',{approved_sha256:draft.body_sha256,confirmation:'publish_reviewed_comment'});content.append(node('p',t("Đã đăng: ")+receipt.comment_url,'notice'));}
          catch(error){message(error.message,true);}
        };
        content.replaceChildren(target,preview,label,publish);
      }catch(error){message(t("Cần một lần điều tra bằng GitHub issue URL đã hoàn tất để tạo bản nháp."),true);}
    };
  }
}
async function history() {
  try {jobRows=await api('/api/jobs');renderHistory();}catch(error){message(error.message,true);}
}
function renderHistory() {
  const rows=jobRows;comparisonChoices(rows);$('history-list').replaceChildren(...rows.map(row=>{const el=node('button',undefined,'history-row');el.append(node('strong',row.case_id || (row.mode==='example'?t("Ví dụ tổng hợp"):row.mode==='main_dev'?t("Main Dev"):row.mode==='finalization'?t("Kết xuất từ checkpoint"):row.mode==='stage_resume'?t('Phục hồi điều tra'):t("Issue của bạn"))),node('span',t(row.status)),node('small',new Date(row.created_at).toLocaleString(window.IssueIntelUI.locale())));el.disabled=['queued','running'].includes(row.status);el.onclick=()=>row.status==='completed'?openJob(row.id).catch(error=>message(error.message,true)):(showStageArtifacts(row),showUsage(row));return el;}));if(!rows.length)$('history-list').append(node('p',t("Chưa có lần điều tra nào."),'muted'));
}
function comparisonChoices(rows) {
  const completed=rows.filter(row=>row.status==='completed');
  for(const side of ['before','after']) {
    const select=$('compare-'+side), previous=select.value;
    select.replaceChildren(...completed.map(row=>{const option=node('option',(row.case_id || row.mode)+' · '+row.depth+' / '+row.provider+' · '+new Date(row.created_at).toLocaleString(window.IssueIntelUI.locale())+' · '+row.id.slice(0,8));option.value=row.id;return option;}));
    if(completed.some(row=>row.id===previous))select.value=previous;
    else if(side==='before' && completed.length>1)select.selectedIndex=1;
  }
  if(completed.length>1 && $('compare-before').value===$('compare-after').value) {
    $('compare-after').value=completed.find(row=>row.id!==$('compare-before').value).id;
  }
  $('compare-button').disabled=completed.length<2;
  if(completed.length<2)$('compare-message').textContent=t("Cần ít nhất hai phiên đã hoàn tất.");
  else $('compare-message').textContent='';
}
async function compareRuns(event) {
  event.preventDefault();const button=$('compare-button');button.disabled=true;
  comparisonResult=null;$('comparison-result').replaceChildren();$('compare-message').textContent=t("Đang kiểm báo cáo và đối chiếu…");
  try {
    comparisonResult=await api('/api/comparisons',{before_id:$('compare-before').value,after_id:$('compare-after').value});
    renderComparison();
  }catch(error){$('compare-message').textContent=t("Không thể so sánh: chọn hai phiên hoàn tất của cùng issue và repository, với file còn nguyên vẹn.");}
  finally{button.disabled=false;}
}
function renderComparison() {
    if(!comparisonResult)return;
    const comparison=comparisonResult,content=$('comparison-result');content.replaceChildren();
    $('compare-message').textContent=t("Đã so sánh hai báo cáo đã lưu.");
    const scope=node('article',undefined,'hypothesis-card');
    scope.append(node('h3',comparison.same_context?t("Cùng query và snapshot"):t("Context đã thay đổi · cần gắn lại citation")),
      node('p',t("Trước: ")+comparison.before.commit_sha+' · '+comparison.before.protocol),
      node('p',t("Sau: ")+comparison.after.commit_sha+' · '+comparison.after.protocol));
    if(comparison.changed_bindings.length)scope.append(node('p',t("Thay đổi: ")+comparison.changed_bindings.join(', ')));
    scope.append(node('p',t("Khác biệt giữa đầu ra không chứng minh cải thiện chất lượng hoặc đã sửa lỗi."),'notice'));
    content.append(scope);
    const labels={unchanged:t("Giữ nguyên"),added:t("Thêm"),removed:t("Bỏ"),content_changed:t("Nội dung đổi"),rebinding_required:t("Cùng nội dung · cần gắn lại citation")};
    const table=node('table');table.className='comparison-table';const head=node('thead'),header=node('tr');
    for(const title of [t("Nguồn"),t("Khác biệt"),t("SHA256 trước → sau")])header.append(node('th',title));head.append(header);
    const body=node('tbody');
    for(const evidence of comparison.evidence){const row=node('tr');row.append(node('td',evidence.kind+' · '+evidence.locator),node('td',labels[evidence.status]),node('td',(evidence.before?.sha256 || '—')+' → '+(evidence.after?.sha256 || '—'),'hash-note'));body.append(row);}
    table.append(head,body);const wrapper=node('div',undefined,'comparison-scroll');wrapper.append(table);content.append(wrapper);
    const hypotheses=comparison.hypotheses;
    content.append(node('h3',t("Giả thuyết: {same} giữ nguyên · {added} thêm · {removed} bỏ",{same:hypotheses.unchanged.length,added:hypotheses.added.length,removed:hypotheses.removed.length})));
    for(const [label,rows] of [[t("Thêm"),hypotheses.added],[t("Bỏ"),hypotheses.removed]])for(const row of rows)content.append(node('p',label+': '+row.statement,'notice'));
    const source=node('details');source.append(node('summary',t("Nguồn báo cáo và giới hạn")),node('pre',JSON.stringify({sources:comparison.sources,before:comparison.before,after:comparison.after,evidence_counts:comparison.evidence_counts,limitations:comparison.limitations},null,2)));content.append(source);
}
async function download(name) {
  await downloadPath('/api/jobs/'+currentJob+'/'+name,'issueintel-'+currentJob+'-'+name);
}
async function downloadPath(path,filename) {
  try {const response=await fetch(path,{headers:{'X-IssueIntel-Token':token}});if(!response.ok)throw new Error(t("Không xuất được báo cáo."));const url=URL.createObjectURL(await response.blob());const a=node('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){message(error.message,true);}
}
function hypothesisReviewSource(){
  return {pack_sha256:intake?.source_pack_sha256,query_sha256:intake?.query_sha256,
    commit_sha:pack?.snapshot.commit_sha,snapshot_sha256:pack?.snapshot.snapshot_sha256};
}
async function readHypothesisReview(id){
  const response=await fetch('/api/jobs/'+id+'/hypothesis-review.json',{headers:{'X-IssueIntel-Token':token}});
  if(!response.ok)throw new Error(t('Không xuất được báo cáo.'));
  const value=await response.json();
  if(!/^[a-f0-9]{64}$/.test(value?.source?.report_sha256||'')||response.headers.get('X-IssueIntel-Report-SHA256')!==value.source.report_sha256)
    throw new Error(t('Không xuất được báo cáo.'));
  return value;
}
async function reloadHypothesisReview(){
  const selected=currentJob,generation=viewGeneration,sourceReport=reportView;
  try{
    const [loaded,loadedReport,loadedIntake]=await Promise.all([
      readHypothesisReview(selected),
      reportView?Promise.resolve(reportView):api('/api/jobs/'+selected+'/investigation-report.json'),
      intake?Promise.resolve(intake):api('/api/jobs/'+selected+'/intake')]);
    if(selected!==currentJob||generation!==viewGeneration||sourceReport!==reportView)return;
    hypothesisReview=loaded;reportView=loadedReport;intake=loadedIntake;renderTab();
  }catch(error){if(selected===currentJob&&generation===viewGeneration)message(error.message,true);}
}
async function exportHypothesisReview(format){
  const selected=currentJob,generation=viewGeneration,sourceReport=reportView,review=hypothesisReview,expected=hypothesisReviewSource();
  const current=()=>selected===currentJob&&generation===viewGeneration&&sourceReport===reportView&&review===hypothesisReview;
  if(!['json','md'].includes(format)||!window.IssueIntelHypothesisReview.valid(review,selected,sourceReport,expected))return;
  try{
    const response=await fetch('/api/jobs/'+selected+'/hypothesis-review.'+format,{headers:{'X-IssueIntel-Token':token}});
    if(!response.ok||response.headers.get('X-IssueIntel-Report-SHA256')!==sourceReport.sha256)throw new Error(t('Không xuất được báo cáo.'));
    let body;
    if(format==='json'){
      const value=await response.json();if(value?.sha256!==review.sha256||!window.IssueIntelHypothesisReview.valid(value,selected,sourceReport,expected))throw new Error(t('Không xuất được báo cáo.'));
      body=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});
    }else body=await response.blob();
    if(!current())return;
    const url=URL.createObjectURL(body),link=node('a');link.href=url;link.download='issueintel-'+selected+'-hypothesis-review.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(error){if(current())message(error.message,true);}
}
function renderPlanPreservingDrafts(){const drafts=planDrafts();renderTab();restorePlanDrafts(drafts);}
async function readCheckJournal(id){
  const response=await fetch('/api/jobs/'+id+'/check-journal.json',{headers:{'X-IssueIntel-Token':token}});
  if(!response.ok)throw new Error('Journal unavailable');
  const value=await response.json();
  if(response.headers.get('X-IssueIntel-Report-SHA256')!==value?.source?.report_sha256
    ||response.headers.get('X-IssueIntel-Journal-SHA256')!==value?.sha256)throw new Error('Journal source changed');
  return value;
}
async function reloadCheckJournal(){
  const selected=currentJob,generation=viewGeneration,sourceReport=reportView;
  try{
    const [loaded,loadedReport,loadedIntake]=await Promise.all([readCheckJournal(selected),
      sourceReport?Promise.resolve(sourceReport):api('/api/jobs/'+selected+'/investigation-report.json'),
      intake?Promise.resolve(intake):api('/api/jobs/'+selected+'/intake')]);
    if(selected!==currentJob||generation!==viewGeneration||sourceReport!==reportView)return;
    const expected={...hypothesisReviewSource(),pack_sha256:loadedIntake?.source_pack_sha256,query_sha256:loadedIntake?.query_sha256};
    if(!window.IssueIntelCheckJournal.valid(loaded,selected,loadedReport,expected))throw new Error('Journal source changed');
    if(journalData&&loaded.revision<journalData.revision)return;
    journalData=loaded;reportView=loadedReport;intake=loadedIntake;renderPlanPreservingDrafts();
  }catch(_){if(selected===currentJob&&generation===viewGeneration)message(t('Chưa tải được nhật ký; bản ghi gốc vẫn được giữ.'),true);}
}
async function saveCheckObservation(request){
  const selected=currentJob,generation=viewGeneration,sourceReport=reportView,previous=journalData,expected=hypothesisReviewSource();
  try{
    const saved=await api('/api/jobs/'+selected+'/check-journal',request);
    if(!window.IssueIntelCheckJournal.valid(saved,selected,sourceReport,expected)
      ||saved.revision!==previous.revision+1||saved.parent_sha256!==request.expected_sha256)throw new Error('Journal source changed');
    window.IssueIntelCheckJournal.completed(selected,sourceReport);
    if(selected!==currentJob||generation!==viewGeneration||sourceReport!==reportView)return;
    journalData=saved;renderPlanPreservingDrafts();message(t('Đã lưu quan sát thủ công; chưa xác minh kết quả.'));
  }catch(error){
    window.IssueIntelCheckJournal.failed(selected,sourceReport);
    if(selected===currentJob&&generation===viewGeneration&&sourceReport===reportView){
      if(activeTab==='plan')renderPlanPreservingDrafts();
      message(t('Chưa lưu được. Tải lại nhật ký nếu phiên bản đã đổi; nội dung đang nhập vẫn được giữ.'),true);
    }
    throw error;
  }
}
async function exportCheckJournal(format){
  const selected=currentJob,generation=viewGeneration,sourceReport=reportView,journal=journalData,expected=hypothesisReviewSource();
  const current=()=>selected===currentJob&&generation===viewGeneration&&sourceReport===reportView&&journal===journalData;
  if(!['json','md'].includes(format)||!window.IssueIntelCheckJournal.valid(journal,selected,sourceReport,expected))return;
  try{
    const response=await fetch('/api/jobs/'+selected+'/check-journal.'+format,{headers:{'X-IssueIntel-Token':token}});
    if(!response.ok||response.headers.get('X-IssueIntel-Report-SHA256')!==sourceReport.sha256
      ||response.headers.get('X-IssueIntel-Journal-SHA256')!==journal.sha256)throw new Error('Journal changed');
    let body;if(format==='json'){
      const value=await response.json();if(value.sha256!==journal.sha256||!window.IssueIntelCheckJournal.valid(value,selected,sourceReport,expected))throw new Error('Journal source changed');
      body=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});
    }else body=await response.blob();
    if(!current())return;
    const url=URL.createObjectURL(body),link=node('a');link.href=url;link.download='issueintel-'+selected+'-check-journal.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(_){if(current())message(t('Chưa xuất được nhật ký đúng phiên bản; tải lại để nhận bản mới.'),true);}
}
$('mode').onchange=switchMode;$('depth').onchange=updateDepthControls;$('use-groq').onchange=updateTypesafeControls;$('case-search').oninput=filterCases;$('investigation-form').onsubmit=submit;
$('compare-form').onsubmit=compareRuns;
$('followup-form').onsubmit=event=>{event.preventDefault();if(!currentJob)return;runJob({mode:'followup',parent_job_id:currentJob,additional_context:$('additional-context').value,provider:$('followup-groq').checked?'groq':'none',depth:$('followup-depth').value});};
$('try-example').onclick=()=>{$('mode').value='example';switchMode();submit();};
$('cancel-button').onclick=async()=>{try{if(!activeJob)return;await api('/api/jobs/'+activeJob+'/cancel',{});message(t("Đã yêu cầu hủy; lời gọi API đang xử lý có thể vẫn tính phí."));}catch(error){message(error.message,true);}};
$('export-md').onclick=()=>downloadPath('/api/jobs/'+currentJob+'/investigation-report.md','issueintel-'+currentJob+'-report.md');
$('export-report-json').onclick=()=>downloadPath('/api/jobs/'+currentJob+'/investigation-report.json','issueintel-'+currentJob+'-report.json');
$('export-json').onclick=()=>download('pack.json');
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{activeTab=button.dataset.tab;renderTab();});
document.addEventListener('issueintel:resumecreated',event=>{
  if(/^[a-f0-9]{24}$/.test(event.detail.job_id))runJob({mode:'resume'},{id:event.detail.job_id});
});
document.addEventListener('issueintel:stagerecoverycreated',event=>{
  if(/^[a-f0-9]{24}$/.test(event.detail?.job_id))runJob({mode:'stage_resume'},{id:event.detail.job_id});
});
document.addEventListener('issueintel:finalizationcreated',event=>{
  if(/^[a-f0-9]{24}$/.test(event.detail.job_id))runJob({mode:'finalization'},{id:event.detail.job_id});
});
document.addEventListener('issueintel:contextcreated',event=>{
  if(/^[a-f0-9]{24}$/.test(event.detail.job_id))runJob({mode:'context'},{id:event.detail.job_id});
});
document.addEventListener('issueintel:clarificationcreated',event=>{
  if(/^[a-f0-9]{24}$/.test(event.detail.job_id))runJob({mode:'clarification'},{id:event.detail.job_id});
});
document.addEventListener('issueintel:checkfollowupcreated',event=>{
  if(/^[a-f0-9]{24}$/.test(event.detail.job_id))runJob({mode:'check_followup'},{id:event.detail.job_id});
});
document.addEventListener('issueintel:memorycreated',event=>{
  if(event.detail?.source_job_id!==currentJob||event.detail.source_pack_sha256!==reportView?.source?.pack_sha256)return;
  if(/^[a-f0-9]{24}$/.test(event.detail.job_id))runJob({mode:'memory_followup'},{id:event.detail.job_id});
});
document.addEventListener('issueintel:plannedcontinuation-created',event=>{
  if(event.detail?.source_job_id!==currentJob||event.detail.source_pack_sha256!==reportView?.source?.pack_sha256)return;
  if(/^[a-f0-9]{24}$/.test(event.detail.job_id))runJob({mode:'planned_continuation'},{id:event.detail.job_id});
});
document.addEventListener('issueintel:showevidence',event=>{
  if(event.detail.job_id!==currentJob || !pack?.evidence.some(e=>e.id===event.detail.evidence_id))return;
  activeTab='evidence';renderTab();document.getElementById('evidence-'+event.detail.evidence_id)?.scrollIntoView({behavior:'smooth',block:'center'});
});
document.addEventListener('issueintel:dossieropen',event=>{
  if(/^[a-f0-9]{24}$/.test(event.detail?.job_id))openJob(event.detail.job_id).catch(error=>message(error.message,true));
});
loadTypesafeStatus();
(async()=>{try{catalogInfo=await api('/api/catalog');catalog=catalogInfo.cases;renderCatalog();filterCases();if(!catalog.length){$('mode').value='example';switchMode();}}catch(error){$('data-status').textContent=t("Không đọc được dữ liệu");message(error.message,true);$('mode').value='example';switchMode();}await history();})();

function renderCatalog() {
  if(!catalogInfo)return;
  $('accepted-count').textContent=catalogInfo.accepted;
  $('data-status').textContent=catalogInfo.original_total?t('{count} pending · Holdout giữ riêng',{count:catalogInfo.pending}):t('Chưa có dataset cục bộ');
}
document.addEventListener('issueintel:languagechange',()=>{
  const drafts=planDrafts();
  updateDepthControls();
  filterCases();renderCatalog();renderHistory();renderPack();renderComparison();
  restorePlanDrafts(drafts);
  if(stageView)showStageArtifacts(stageView);
  if(usageView)showUsage(usageView);
  if(lastMessage)message(t(lastMessage.key),lastMessage.error);
});

'use strict';
const t = window.IssueIntelUI.t;
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="issueintel-session"]').content;
let catalog = [], pack = null, currentJob = null, activeJob = null, activeTab = 'evidence';
let catalogInfo=null, jobRows=[], comparisonResult=null, lastMessage=null;
let intake=null;
let relatedResult=null;
let planData=null;
let viewGeneration=0;
let stageView=null;
async function api(path, body) {
  let response;
  try {response = await fetch(path, {method:body === undefined ? 'GET':'POST', headers:{'X-IssueIntel-Token':token, ...(body === undefined ? {}:{'Content-Type':'application/json'})}, ...(body === undefined ? {}:{body:JSON.stringify(body)})});}
  catch(_) {throw new Error(t('Không kết nối được server local.'));}
  const data = await response.json();
  if (!response.ok) throw new Error(t('Lỗi yêu cầu. Kiểm tra đầu vào hoặc dữ liệu cục bộ.'));
  return data;
}
function node(tag, text, className) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(className) el.className = className; return el; }
function message(text, error=false) {lastMessage={key:window.IssueIntelUI.sourceKey(text),error};$('message').textContent=text;$('message').className=error?'error':'';}
function switchMode() { const mode=$('mode').value; $('main-dev-fields').hidden=mode!=='main_dev'; $('github-fields').hidden=mode!=='github'; $('local-fields').hidden=mode!=='local'; $('example-note').hidden=mode!=='example'; }
function filterCases() {
  const selected=$('case-id').value;
  const query=$('case-search').value.toLowerCase(); const rows=catalog.filter(c => (c.repository+' '+c.case_id+' '+c.summary).toLowerCase().includes(query));
  $('case-id').replaceChildren(...rows.map(c=>{const opt=node('option',c.case_id.replace('verified:','')+' · '+c.summary.slice(0,52));opt.value=c.case_id;return opt;}));
  if(!rows.length) {$('case-id').append(node('option',t("Không có ca phù hợp")));$('case-id').firstChild.value='';}
  else if(rows.some(row=>row.case_id===selected))$('case-id').value=selected;
}
function requestInput() {
  const mode=$('mode').value;const request={mode,provider:$('use-groq').checked?'groq':'none',depth:$('depth').value};
  if(mode==='main_dev') request.case_id=$('case-id').value;
  if(mode==='github') Object.assign(request,{issue_url:$('github-url').value,revision:$('github-revision').value});
  if(mode==='local') Object.assign(request,{repository:$('repository').value,revision:$('revision').value,issue_text:$('issue-text').value});
  return request;
}
async function submit(event) {
  event?.preventDefault();await runJob(requestInput());
}
async function runJob(request) {
  if($('run-button').disabled)return;
  try {
    stageView=null;$('stage-artifacts').hidden=true;
    $('run-button').disabled=true;$('followup-button').disabled=true;message(t("Đang chuẩn bị snapshot và bằng chứng…"));
    const job=await api('/api/investigations',request);activeJob=job.id;$('cancel-button').hidden=false;
    for(;;) {
      const state=await api('/api/jobs/'+job.id);
      if(['completed','failed','cancelled','interrupted'].includes(state.status)) {
        if(state.status==='completed') {
          await openJob(job.id);message(t("Đã lưu investigation pack cục bộ."));
          if(request.mode==='followup' && $('additional-context').value===request.additional_context)$('additional-context').value='';
        }
        else {message(t('Tác vụ không hoàn tất. Kiểm tra đầu vào, dữ liệu hoặc cấu hình provider; không tự thử lại.'),true);showStageArtifacts(state);}
        break;
      }
      message(state.stage==='prepare_snapshot'?t("Đang kiểm query và snapshot…"):t("Đang truy xuất, điều tra và kiểm chứng…"));
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
  } catch(error) {message(error.message,true);}
  finally {activeJob=null;$('run-button').disabled=false;$('followup-button').disabled=false;$('cancel-button').hidden=true;await history();}
}
async function openJob(id) {
  const generation=++viewGeneration;
  const loaded=await api('/api/jobs/'+id+'/pack.json');
  const [inspected,plan]=await Promise.allSettled([api('/api/jobs/'+id+'/intake'),api('/api/jobs/'+id+'/plan')]);
  if(generation!==viewGeneration)return;
  pack=loaded;intake=inspected.status==='fulfilled'?inspected.value:null;planData=plan.status==='fulfilled'?plan.value:null;currentJob=id;
  relatedResult=null;
  showStageArtifacts({id,stage_checkpoints:pack.stage_checkpoints});
  renderPack();
}
function renderPack() {
  if(!pack)return;
  document.dispatchEvent(new CustomEvent('issueintel:packchange',{detail:{job_id:currentJob,
    available:!pack.case.split && !pack.followup && pack.snapshot.protocol==='benchmark_base' && !pack.case.case_id.startsWith('verified:') && !pack.case.case_id.startsWith('live:')}}));
  $('empty-state').hidden=true;$('result').hidden=false;$('exports').hidden=false;
  $('followup-panel').hidden=false;$('followup-parent').textContent=t('Phiên nguồn: ')+currentJob;
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
  document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button.dataset.tab===activeTab));
  const content=$('tab-content');content.replaceChildren();window.IssueIntelEvidenceGraph?.cancel();if(!pack)return;
  if(activeTab==='evidence') content.append(...pack.evidence.map(evidenceCard));
  if(activeTab==='intake' && !intake)content.append(node('p',t('Chưa đọc được metadata; báo cáo nguồn vẫn có thể xuất.'),'notice'));
  if(activeTab==='plan' && !planData)content.append(node('p',t('Chưa đọc được metadata; báo cáo nguồn vẫn có thể xuất.'),'notice'));
  if(activeTab==='plan' && planData){
    const reload=node('button',t('Tải lại kế hoạch'));reload.onclick=async()=>{
      const selected=currentJob;
      try{const plan=await api('/api/jobs/'+selected+'/plan');if(currentJob===selected){planData=plan;renderTab();}}
      catch(error){message(error.message,true);}
    };
    const exportPlan=node('button',t('Xuất kế hoạch JSON'));exportPlan.onclick=()=>{
      const url=URL.createObjectURL(new Blob([JSON.stringify(planData,null,2)],{type:'application/json'}));
      const link=node('a');link.href=url;link.download='issueintel-'+planData.job_id+'-plan.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    content.append(node('p',t('Phiên bản kế hoạch: {revision}',{revision:planData.revision}),'notice'),
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
      content.append(form);
    }
    const history=node('details');history.append(node('summary',t('Lịch sử thay đổi kế hoạch')),node('pre',JSON.stringify({source_pack_sha256:planData.source_pack_sha256,sha256:planData.sha256,history:planData.history},null,2)));content.append(history);
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
    for(const [field,details] of Object.entries(intake.fields)){
      const card=node('article',undefined,'hypothesis-card');card.append(node('h3',t(field)+' · '+t(details.status)));
      for(const source of details.sources)card.append(node('pre',t('Dòng {line}: ',{line:source.line})+source.text),node('p','SHA256 '+source.text_sha256,'hash-note'));
      if(details.status==='not_found')card.append(node('p',t('Cần xem lại hoặc bổ sung mục này.'),'notice'));content.append(card);
    }
    if(pack.followup)content.append(node('p',t('Dữ kiện bổ sung chưa xác minh; không kế thừa chứng nhận query lịch sử.'),'notice'),node('pre',JSON.stringify({parent_job_id:pack.followup.parent_job_id,parent_pack_sha256:pack.followup.parent_pack_sha256,depth:pack.followup.depth},null,2)));
  }
  if(activeTab==='hypotheses') {
    if(!pack.annotation) content.append(node('p',t("Chưa gọi model để sinh giả thuyết. Bạn có thể đọc bằng chứng hiện có hoặc bật Groq khi điều tra."),'notice'));
    for(const h of pack.annotation?.hypotheses || []) {
      const card=node('article',undefined,'hypothesis-card');card.append(node('h3',h.id+' · '+h.statement));
      card.append(node('p',t("Bằng chứng hỗ trợ:")));card.append(...h.support_ids.map(citation));
      card.append(node('p',t("Bằng chứng phản bác: ")+(h.counter_ids.length?'':t("chưa tìm thấy"))));card.append(...h.counter_ids.map(citation));
      card.append(node('p',t("Cách kiểm tra: ")+h.falsification_test),node('span',t("Chưa xác minh nguyên nhân"),'badge'));content.append(card);
    }
    if(pack.annotation?.abstention) content.append(node('p','Abstention: '+t(pack.annotation.abstention.label)+' — '+pack.annotation.abstention.reason,'notice'));
  }
  if(activeTab==='verification') {
    content.append(node('p',t("Citation hợp lệ không đồng nghĩa đã chứng minh nguyên nhân. Critique bằng model được báo riêng với kiểm người."),'notice'));
    for(const review of pack.verification?.reviews || []) {const card=node('article',undefined,'hypothesis-card');card.append(node('h3',review.hypothesis_id+' · '+t(review.assessment)),node('p',review.reason),...review.evidence_ids.map(citation));content.append(card);}
    if(!pack.verification)content.append(node('p',t("Chưa có verifier pass cho lần điều tra này."),'notice'));
    content.append(node('h3',t("Bước kiểm tra tiếp theo")));
    const list=node('ol');for(const step of pack.recommended_steps) list.append(node('li',recommendedText(step)));content.append(list);
  }
  if(activeTab==='trace') {
    if(pack.refinement)content.append(node('p',t('Vòng truy xuất bổ sung: {rounds}/1 · lượt sinh: {passes}',{rounds:pack.refinement.retrieval_rounds,passes:pack.refinement.generation_passes}),'notice'));
    content.append(node('pre',JSON.stringify({stop_reason:pack.stop_reason,trace:pack.trace,refinement:pack.refinement,stage_checkpoints:pack.stage_checkpoints,leakage_audit:pack.leakage_audit},null,2)));
  }
  if(activeTab==='graph')window.IssueIntelEvidenceGraph.render(content,currentJob);
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
  const rows=jobRows;comparisonChoices(rows);$('history-list').replaceChildren(...rows.map(row=>{const el=node('button',undefined,'history-row');el.append(node('strong',row.case_id || (row.mode==='example'?t("Ví dụ tổng hợp"):row.mode==='main_dev'?t("Main Dev"):t("Issue của bạn"))),node('span',t(row.status)),node('small',new Date(row.created_at).toLocaleString(window.IssueIntelUI.locale())));el.disabled=row.status!=='completed'&&!Object.keys(row.stage_checkpoints || {}).length;el.onclick=()=>row.status==='completed'?openJob(row.id).catch(error=>message(error.message,true)):showStageArtifacts(row);return el;}));if(!rows.length)$('history-list').append(node('p',t("Chưa có lần điều tra nào."),'muted'));
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
$('mode').onchange=switchMode;$('case-search').oninput=filterCases;$('investigation-form').onsubmit=submit;
$('compare-form').onsubmit=compareRuns;
$('followup-form').onsubmit=event=>{event.preventDefault();if(!currentJob)return;runJob({mode:'followup',parent_job_id:currentJob,additional_context:$('additional-context').value,provider:$('followup-groq').checked?'groq':'none',depth:$('followup-depth').value});};
$('try-example').onclick=()=>{$('mode').value='example';switchMode();submit();};
$('cancel-button').onclick=async()=>{try{if(!activeJob)return;await api('/api/jobs/'+activeJob+'/cancel',{});message(t("Đã yêu cầu hủy; lời gọi API đang xử lý có thể vẫn tính phí."));}catch(error){message(error.message,true);}};
$('export-md').onclick=()=>download('report.md');$('export-json').onclick=()=>download('pack.json');
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{activeTab=button.dataset.tab;renderTab();});
document.addEventListener('issueintel:showevidence',event=>{
  if(event.detail.job_id!==currentJob || !pack?.evidence.some(e=>e.id===event.detail.evidence_id))return;
  activeTab='evidence';renderTab();document.getElementById('evidence-'+event.detail.evidence_id)?.scrollIntoView({behavior:'smooth',block:'center'});
});
(async()=>{try{catalogInfo=await api('/api/catalog');catalog=catalogInfo.cases;renderCatalog();filterCases();if(!catalog.length){$('mode').value='example';switchMode();}}catch(error){$('data-status').textContent=t("Không đọc được dữ liệu");message(error.message,true);$('mode').value='example';switchMode();}await history();})();

function renderCatalog() {
  if(!catalogInfo)return;
  $('accepted-count').textContent=catalogInfo.accepted;
  $('data-status').textContent=catalogInfo.original_total?t('{count} pending · Holdout giữ riêng',{count:catalogInfo.pending}):t('Chưa có dataset cục bộ');
}
document.addEventListener('issueintel:languagechange',()=>{
  const drafts=planDrafts();
  filterCases();renderCatalog();renderHistory();renderPack();renderComparison();
  restorePlanDrafts(drafts);
  if(stageView)showStageArtifacts(stageView);
  if(lastMessage)message(t(lastMessage.key),lastMessage.error);
});

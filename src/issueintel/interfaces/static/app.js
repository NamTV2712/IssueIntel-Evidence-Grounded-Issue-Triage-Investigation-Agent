'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="issueintel-session"]').content;
let catalog = [], pack = null, currentJob = null, activeJob = null, activeTab = 'evidence';
async function api(path, body) {
  const response = await fetch(path, {method:body === undefined ? 'GET':'POST', headers:{'X-IssueIntel-Token':token, ...(body === undefined ? {}:{'Content-Type':'application/json'})}, ...(body === undefined ? {}:{body:JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Không đọc được kết quả.');
  return data;
}
function node(tag, text, className) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(className) el.className = className; return el; }
function message(text, error=false) { $('message').textContent=text; $('message').className=error?'error':''; }
function switchMode() { const mode=$('mode').value; $('main-dev-fields').hidden=mode!=='main_dev'; $('local-fields').hidden=mode!=='local'; $('example-note').hidden=mode!=='example'; }
function filterCases() {
  const query=$('case-search').value.toLowerCase(); const rows=catalog.filter(c => (c.repository+' '+c.case_id+' '+c.summary).toLowerCase().includes(query));
  $('case-id').replaceChildren(...rows.map(c=>{const opt=node('option',c.case_id.replace('verified:','')+' · '+c.summary.slice(0,52));opt.value=c.case_id;return opt;}));
  if(!rows.length) {$('case-id').append(node('option','Không có ca phù hợp'));$('case-id').firstChild.value='';}
}
function requestInput() {
  const mode=$('mode').value;const request={mode,provider:$('use-groq').checked?'groq':'none',depth:$('depth').value};
  if(mode==='main_dev') request.case_id=$('case-id').value;
  if(mode==='local') Object.assign(request,{repository:$('repository').value,revision:$('revision').value,issue_text:$('issue-text').value});
  return request;
}
async function submit(event) {
  event?.preventDefault(); if($('run-button').disabled) return;
  try {
    $('run-button').disabled=true; message('Đang chuẩn bị snapshot và bằng chứng…');
    const job=await api('/api/investigations',requestInput()); currentJob=job.id;activeJob=job.id;$('cancel-button').hidden=false;
    for(;;) {
      const state=await api('/api/jobs/'+job.id);
      if(['completed','failed','cancelled','interrupted'].includes(state.status)) {
        if(state.status==='completed') {await openJob(job.id);message('Đã lưu investigation pack cục bộ.');}
        else message(state.error || 'Tác vụ chưa hoàn tất.',true);
        break;
      }
      message(state.stage==='prepare_snapshot'?'Đang kiểm query và snapshot…':'Đang truy xuất, điều tra và kiểm chứng…');
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
  } catch(error) {message(error.message,true);}
  finally {activeJob=null;$('run-button').disabled=false;$('cancel-button').hidden=true;await history();}
}
async function openJob(id) {
  pack=await api('/api/jobs/'+id+'/pack.json'); currentJob=id;
  $('empty-state').hidden=true;$('result').hidden=false;$('exports').hidden=false;
  $('result-title').textContent=pack.case.case_id;
  const strict=pack.leakage_audit.strict_issue_time_verified;
  const boxes=[['Snapshot',pack.snapshot.commit_sha.slice(0,12)],['Bằng chứng',pack.evidence.length+' nguồn'],['Thời gian',strict?'Strict issue-time':'Chưa xác minh lịch sử']];
  $('summary').replaceChildren(...boxes.map(([label,value])=>{const box=node('div',undefined,'summary-box');box.append(node('small',label),node('strong',value));return box;}));
  renderTab();
}
function evidenceCard(e) {
  const card=node('article',undefined,'evidence-card');card.id='evidence-'+e.id;
  const head=node('div',undefined,'card-head');head.append(node('span',e.locator,'locator'),node('span',e.kind==='code'?'CODE':'ISSUE','badge'));
  card.append(head,node('pre',e.text),node('div','SHA256 '+e.sha256+' · '+e.id,'hash-note'));return card;
}
function citation(id) {
  const button=node('button',id,'citation');button.onclick=()=>{activeTab='evidence';renderTab();document.getElementById('evidence-'+id)?.scrollIntoView({behavior:'smooth',block:'center'});};return button;
}
function renderTab() {
  document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button.dataset.tab===activeTab));
  const content=$('tab-content');content.replaceChildren();if(!pack)return;
  if(activeTab==='evidence') content.append(...pack.evidence.map(evidenceCard));
  if(activeTab==='hypotheses') {
    if(!pack.annotation) content.append(node('p','Chưa gọi model để sinh giả thuyết. Bạn có thể đọc bằng chứng hiện có hoặc bật Groq khi điều tra.','notice'));
    for(const h of pack.annotation?.hypotheses || []) {
      const card=node('article',undefined,'hypothesis-card');card.append(node('h3',h.id+' · '+h.statement));
      card.append(node('p','Bằng chứng hỗ trợ:'));card.append(...h.support_ids.map(citation));
      card.append(node('p','Bằng chứng phản bác: '+(h.counter_ids.length?'':'chưa tìm thấy')));card.append(...h.counter_ids.map(citation));
      card.append(node('p','Cách kiểm tra: '+h.falsification_test),node('span','Chưa xác minh nguyên nhân','badge'));content.append(card);
    }
    if(pack.annotation?.abstention) content.append(node('p','Abstention: '+pack.annotation.abstention.label+' — '+pack.annotation.abstention.reason,'notice'));
  }
  if(activeTab==='verification') {
    content.append(node('p','Citation hợp lệ không đồng nghĩa đã chứng minh nguyên nhân. Critique bằng model được báo riêng với kiểm người.','notice'));
    for(const review of pack.verification?.reviews || []) {const card=node('article',undefined,'hypothesis-card');card.append(node('h3',review.hypothesis_id+' · '+review.assessment),node('p',review.reason),...review.evidence_ids.map(citation));content.append(card);}
    if(!pack.verification)content.append(node('p','Chưa có verifier pass cho lần điều tra này.','notice'));
    content.append(node('h3','Bước kiểm tra tiếp theo'));
    const list=node('ol');for(const step of pack.recommended_steps) list.append(node('li',step));content.append(list);
  }
  if(activeTab==='trace') {
    content.append(node('pre',JSON.stringify({stop_reason:pack.stop_reason,trace:pack.trace,leakage_audit:pack.leakage_audit},null,2)));
  }
}
async function history() {
  try {const rows=await api('/api/jobs');$('history-list').replaceChildren(...rows.map(row=>{const el=node('button',undefined,'history-row');el.append(node('strong',row.case_id || (row.mode==='example'?'Ví dụ tổng hợp':row.mode==='main_dev'?'Main Dev':'Issue của bạn')),node('span',row.status),node('small',new Date(row.created_at).toLocaleString('vi-VN')));el.disabled=row.status!=='completed';el.onclick=()=>openJob(row.id).catch(error=>message(error.message,true));return el;}));if(!rows.length)$('history-list').append(node('p','Chưa có lần điều tra nào.','muted'));}catch(error){message(error.message,true);}
}
async function download(name) {
  try {const response=await fetch('/api/jobs/'+currentJob+'/'+name,{headers:{'X-IssueIntel-Token':token}});if(!response.ok)throw new Error('Không xuất được báo cáo.');const url=URL.createObjectURL(await response.blob());const a=node('a');a.href=url;a.download='issueintel-'+currentJob+'-'+name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){message(error.message,true);}
}
$('mode').onchange=switchMode;$('case-search').oninput=filterCases;$('investigation-form').onsubmit=submit;
$('try-example').onclick=()=>{$('mode').value='example';switchMode();submit();};
$('cancel-button').onclick=async()=>{try{if(!activeJob)return;await api('/api/jobs/'+activeJob+'/cancel',{});message('Đã yêu cầu hủy; lời gọi API đang xử lý có thể vẫn tính phí.');}catch(error){message(error.message,true);}};
$('export-md').onclick=()=>download('report.md');$('export-json').onclick=()=>download('pack.json');
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{activeTab=button.dataset.tab;renderTab();});
(async()=>{try{const data=await api('/api/catalog');catalog=data.cases;$('accepted-count').textContent=data.accepted;$('data-status').textContent=data.original_total?data.pending+' pending · Holdout giữ riêng':'Chưa có dataset cục bộ';filterCases();if(!catalog.length){$('mode').value='example';switchMode();}}catch(error){$('data-status').textContent='Không đọc được dữ liệu';message(error.message,true);$('mode').value='example';switchMode();}await history();})();

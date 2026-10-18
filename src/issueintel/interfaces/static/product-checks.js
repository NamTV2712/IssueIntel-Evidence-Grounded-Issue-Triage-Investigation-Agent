/* Explicit, source-bound proposals; saved checks never execute repository code. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t;
  const node=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const jobID=value=>typeof value==='string'&&/^[a-f0-9]{24}$/.test(value);
  const bounded=value=>typeof value==='string'&&value.trim().length>0&&value.length<=4000&&!value.includes('\0');
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  const same=(left,right)=>JSON.stringify(canonical(left))===JSON.stringify(canonical(right));
  const statuses=['queued','running','completed','failed','interrupted','cancelled'];
  const effort={low:'Ít công sức',medium:'Công sức vừa',high:'Nhiều công sức'};
  const rule='max_asserted_separated_pairs_then_min_model_estimated_effort_saved_order_v1';
  const hypothesisFields=['id','statement','support_ids','counter_ids','counter_status','falsification_test'];
  let selected=null,report=null,pack=null,binding=null,available=false,area=null,version=0,timer=null;
  let busy=false,posting=false,consent=false,saved=null,activeJob=null,bundle=null,notice=null,viewRevision=0;

  function sourceBinding(value,job,sourcePack){
    return value?.schema==='investigation_report_v1'&&value.source?.job_id===job&&jobID(job)&&hash(value.sha256)
      &&hash(value.source.pack_sha256)&&hash(value.source.query_sha256)&&hash(value.source.snapshot_sha256)
      &&value.source.protocol==='benchmark_base'&&value.source.strict_issue_time_verified===false
      &&value.source.snapshot_sha256===sourcePack?.snapshot?.snapshot_sha256&&value.source.commit_sha===sourcePack?.snapshot?.commit_sha
      &&Array.isArray(value.hypotheses)&&Array.isArray(value.evidence)
      ?[job,value.sha256,value.source.pack_sha256,value.source.query_sha256,value.source.snapshot_sha256,value.source.commit_sha].join(':'):null;
  }
  function valid(value,sourceReport=report,job=selected,sourcePack=pack){
    try{
      if(!sourceBinding(sourceReport,job,sourcePack)||value?.schema!=='product_check_plan_v1'||!hash(value.sha256)
        ||value.source?.schema!=='check_plan_source_v1'||value.source.source_job_id!==job
        ||value.source.source_pack_sha256!==sourceReport.source.pack_sha256||value.source.report_sha256!==sourceReport.sha256
        ||value.source.query_sha256!==sourceReport.source.query_sha256||value.source.snapshot_sha256!==sourceReport.source.snapshot_sha256
        ||value.source.commit_sha!==sourceReport.source.commit_sha||!hash(value.source.sha256)||!hash(value.source.annotation_sha256)
        ||!hash(value.prompt_sha256)||!Array.isArray(value.checks)||value.checks.length>3
        ||!Array.isArray(value.limitations)||!value.limitations.every(item=>typeof item==='string')
        ||value.provenance?.kind!=='llm'||value.provenance.provider!=='groq'||typeof value.provenance.model!=='string'
        ||![1,2].includes(value.provenance.attempts)||value.provenance.repaired!==(value.provenance.attempts===2)
        ||!hash(value.provenance.output_schema_sha256))return false;
      const project=rows=>rows.map(row=>Object.fromEntries(hypothesisFields.map(key=>[key,row[key]])));
      if(!same(value.source.hypotheses,project(sourcePack.annotation?.hypotheses||[])))return false;
      const hypotheses=sourceReport.hypotheses.map(row=>row.id),evidence=new Map(sourceReport.evidence.map(row=>[row.id,row]));
      if(hypotheses.length<2||hypotheses.length>5||new Set(hypotheses).size!==hypotheses.length||!Array.isArray(value.source.evidence))return false;
      const sourceEvidence=new Set();
      for(const row of value.source.evidence){const expected=evidence.get(row.id);
        if(!expected||sourceEvidence.has(row.id)||row.sha256!==expected.sha256||row.kind!==expected.kind
          ||(row.kind==='code'&&(row.locator!==expected.locator||row.revision!==sourceReport.source.commit_sha)))return false;
        sourceEvidence.add(row.id);}
      if(sourceEvidence.size!==evidence.size)return false;
      const ids=new Set(),comparison=[];
      for(const check of value.checks){
        if(!/^check_[1-3]$/.test(check?.id)||ids.has(check.id)||!['procedure','assumptions','outcome_a','outcome_b'].every(key=>bounded(check[key]))
          ||!Object.hasOwn(effort,check.effort)||!Array.isArray(check.evidence_ids)||!check.evidence_ids.length
          ||new Set(check.evidence_ids).size!==check.evidence_ids.length||check.evidence_ids.some(id=>!evidence.has(id))
          ||!check.evidence_ids.some(id=>evidence.get(id).kind==='code')||!Array.isArray(check.predictions)||check.predictions.length!==hypotheses.length)return false;
        const normalize=text=>text.toLowerCase().replace(/\s+/g,' ').trim();if(normalize(check.outcome_a)===normalize(check.outcome_b))return false;
        ids.add(check.id);const predicted=new Set();
        for(const row of check.predictions){if(!hypotheses.includes(row.hypothesis_id)||predicted.has(row.hypothesis_id)
          ||!['A','B','unknown'].includes(row.outcome)||!bounded(row.reason))return false;predicted.add(row.hypothesis_id);}
        const pairs=[];for(const [index,left] of check.predictions.entries())for(const right of check.predictions.slice(index+1))
          if(left.outcome!=='unknown'&&right.outcome!=='unknown'&&left.outcome!==right.outcome)pairs.push([left.hypothesis_id,right.hypothesis_id]);
        comparison.push({check_id:check.id,asserted_separated_pairs:pairs,unknown_predictions:check.predictions.filter(row=>row.outcome==='unknown').length,estimated_effort:check.effort});
      }
      const candidates=comparison.filter(row=>row.asserted_separated_pairs.length).sort((left,right)=>right.asserted_separated_pairs.length-left.asserted_separated_pairs.length
        ||['low','medium','high'].indexOf(left.estimated_effort)-['low','medium','high'].indexOf(right.estimated_effort));
      const compared=value.comparison;
      return compared?.schema==='check_plan_comparison_v1'&&hash(compared.sha256)&&same(compared.checks,comparison)
        &&compared.recommended_check_id===(candidates[0]?.check_id||null)&&compared.rule===rule
        &&compared.predictions_verified===false&&compared.checks_executed===false&&compared.semantic_quality==='unreviewed'
        &&compared.causal_correctness==='unverified'&&compared.information_gain_estimated===false;
    }catch(_){return false;}
  }
  function stopTimer(){if(timer!==null){clearTimeout(timer);timer=null;}}
  function reset(){stopTimer();version++;busy=false;posting=false;consent=false;saved=null;activeJob=null;bundle=null;notice=null;}
  function currentVersion(current){return current===version&&!!binding;}
  function citations(ids,current){const rendered=area,view=viewRevision,refs=node('div',undefined,'product-check-citations');for(const id of ids){const button=node('button',id,'citation');button.type='button';
    button.setAttribute('aria-label',t('Xem bằng chứng {id}',{id}));button.onclick=()=>{if(current===binding&&area===rendered&&view===viewRevision)document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:selected,evidence_id:id}}));};refs.append(button);}return refs;}
  function button(label,callback,disabled=false,values={}){const rendered=area,view=viewRevision,item=node('button',t(label,values));item.type='button';item.disabled=disabled;
    item.onclick=()=>{if(area===rendered&&view===viewRevision&&!item.disabled)return callback();};return item;}
  function redraw(){
    if(!area)return;viewRevision++;area.replaceChildren();area.append(node('h3',t('Kiểm tra phân biệt giả thuyết')));
    if(!binding){area.append(node('p',t('Cần báo cáo sản phẩm đã lưu, khớp query và snapshot để tạo kiểm tra phân biệt.'),'notice'));return;}
    area.append(node('p',t('Đề xuất của model để so sánh các giả thuyết đã lưu. IssueIntel chưa chạy các kiểm tra; dự đoán chưa được xác minh.'),'notice'),
      node('p',t('Kế hoạch bổ sung được lưu riêng. Nhật ký bên dưới chỉ ghi các bước của báo cáo gốc; chưa ghi kết quả cho các kiểm tra bổ sung này.'),'notice'),
      node('p',report.issue.repository+' · '+report.issue.case_id,'product-check-identity'),node('p',t('Commit của báo cáo: ')+report.source.commit_sha,'hash-note'));
    const controls=node('div',undefined,'product-check-controls');
    controls.append(button('Mở các kế hoạch kiểm tra đã lưu',loadSaved,busy||posting));
    if(!available)controls.append(node('p',t('Kiểm tra phân biệt chỉ dành cho điều tra sản phẩm đủ điều kiện. Mở danh sách đã lưu để kiểm tra khả năng sử dụng.'),'notice'));
    if(report.hypotheses.length<2)controls.append(node('p',t('Cần ít nhất hai giả thuyết đã lưu để đề xuất kiểm tra phân biệt.'),'notice'));
    const locked=busy||posting||!!activeJob||!available||report.hypotheses.length<2;
    const label=node('label',undefined,'checkbox'),checkbox=node('input');checkbox.type='checkbox';checkbox.checked=consent;checkbox.disabled=locked;
    const checkboxArea=area,checkboxView=viewRevision;checkbox.onchange=()=>{if(area===checkboxArea&&viewRevision===checkboxView&&!checkbox.disabled){consent=checkbox.checked;redraw();area?.querySelector?.('input[type="checkbox"]')?.focus?.();}};
    label.append(checkbox,node('span',t('Tôi đồng ý gửi issue, giả thuyết và bằng chứng đã lưu tới Groq để đề xuất kiểm tra, có chi phí API.')));
    controls.append(node('p',t('Một tác vụ tạo cho mỗi báo cáo nguồn, tối đa hai call kể cả sửa output. Mở lại và xuất kế hoạch đã lưu không gọi model.'),'field-note'),label,
      button('Đề xuất kiểm tra bằng Groq',generate,locked||!consent));
    if(activeJob&&['queued','running'].includes(activeJob.status))controls.append(button('Hủy tác vụ kiểm tra',cancelJob,posting));area.append(controls);
    if(saved){const listing=node('div',undefined,'product-check-saved');
      if(!saved.length)listing.append(node('p',t('Chưa có kế hoạch kiểm tra cho báo cáo nguồn này.'),'notice'));
      for(const row of saved){const line=node('div',undefined,'product-check-saved-row');line.append(button('Mở kế hoạch {id}',()=>openJob(row.id),busy||posting,{id:row.id}),node('span',t(row.status),'badge'));listing.append(line);}area.append(listing);}
    if(activeJob){const progress=node('div',undefined,'product-check-progress');progress.append(node('p',t('Tác vụ kiểm tra: {id} · {status}',{id:activeJob.id,status:t(activeJob.status)}),'hash-note'));
      if(activeJob.accounting&&window.IssueIntelUsage){const usage=node('div');window.IssueIntelUsage.render(usage,activeJob.accounting);progress.append(usage);}
      if(['failed','interrupted','cancelled'].includes(activeJob.status))progress.append(node('p',t('Tác vụ chưa tạo được kế hoạch hợp lệ; receipt vẫn được giữ. Không tự tạo tác vụ mới hoặc gọi lại model cho báo cáo nguồn này.'),'notice'));area.append(progress);}
    if(bundle){
      const toolbar=node('div',undefined,'product-check-actions');for(const format of ['json','md'])toolbar.append(button(format==='json'?'Xuất kiểm tra JSON':'Xuất kiểm tra Markdown',()=>exportPlan(format)));area.append(toolbar);
      const selectedCheck=bundle.comparison.recommended_check_id,recommendation=node('div',undefined,'product-check-recommendation');
      if(selectedCheck){const comparison=bundle.comparison.checks.find(row=>row.check_id===selectedCheck);
        recommendation.append(node('strong',t('Gợi ý xem trước: {id}',{id:selectedCheck})),node('p',t('{count} cặp giả thuyết có dự đoán A/B khác nhau do model nêu.',{count:comparison.asserted_separated_pairs.length}),'notice'));}
      else recommendation.append(node('strong',t('Chưa có kiểm tra được gợi ý theo quy tắc A/B.')));
      recommendation.append(node('p',t('Quy tắc: nhiều cặp dự đoán A/B khác nhau nhất, rồi ít công sức model ước tính nhất; hòa thì giữ thứ tự đã lưu. Không ước lượng information gain hoặc xác nhận chất lượng kiểm tra.'),'notice'));area.append(recommendation);
      if(!bundle.checks.length)area.append(node('p',t('Model không đề xuất được kiểm tra phân biệt từ bằng chứng hiện có. Danh sách trống được giữ nguyên; không tự gọi thêm.'),'notice'));
      const current=binding;
      for(const check of bundle.checks){const card=node('article',undefined,'product-check-card');card.append(node('h4',t('Kiểm tra bổ sung {id}',{id:check.id})),node('span',t(effort[check.effort]),'badge'));
        if(check.id===selectedCheck)card.append(node('span',t('Gợi ý theo dự đoán model'),'badge'));
        for(const [caption,key] of [['Cách kiểm tra','procedure'],['Giả định và điều kiện kiểm soát','assumptions']])card.append(node('h5',t(caption)),node('p',check[key],'product-check-source'));
        const outcomes=node('dl',undefined,'product-check-outcomes');for(const [caption,key] of [['Kết quả A','outcome_a'],['Kết quả B','outcome_b']]){const block=node('div');block.append(node('dt',t(caption)),node('dd',check[key]));outcomes.append(block);}card.append(outcomes,citations(check.evidence_ids,current));
        const scroll=node('div',undefined,'product-check-table-scroll');scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',t('Dự đoán theo từng giả thuyết cho {id}',{id:check.id}));
        const table=node('table',undefined,'product-check-table');table.append(node('caption',t('Dự đoán của model cho từng giả thuyết; chưa phải kết quả quan sát.')));
        const head=node('thead'),headRow=node('tr');for(const caption of ['Giả thuyết','Dự đoán','Lý do dự đoán']){const th=node('th',t(caption));th.scope='col';headRow.append(th);}head.append(headRow);table.append(head);
        const body=node('tbody');for(const hypothesis of report.hypotheses){const prediction=check.predictions.find(row=>row.hypothesis_id===hypothesis.id),row=node('tr'),name=node('th');name.scope='row';
          name.append(node('strong',hypothesis.id),node('p',hypothesis.statement,'product-check-source'));row.append(name,node('td',prediction.outcome==='unknown'?t('Chưa biết'):prediction.outcome),node('td',prediction.reason,'product-check-source'));body.append(row);}table.append(body);scroll.append(table);card.append(scroll);area.append(card);}
      const provenance=node('details',undefined,'product-check-provenance');provenance.append(node('summary',t('Nguồn và giới hạn kế hoạch kiểm tra')),
        node('p',t('Phiên nguồn: ')+selected,'hash-note'),node('p','Pack SHA256 '+bundle.source.source_pack_sha256,'hash-note'),node('p','Report SHA256 '+bundle.source.report_sha256,'hash-note'),
        node('p','Query SHA256 '+bundle.source.query_sha256,'hash-note'),node('p','Snapshot SHA256 '+bundle.source.snapshot_sha256,'hash-note'),node('p','Check plan SHA256 '+bundle.sha256,'hash-note'));
      for(const item of bundle.limitations)provenance.append(node('p',item,'notice'));area.append(provenance);
    }
    const status=node('p',notice?t(notice):'','notice');status.setAttribute('role','status');status.setAttribute('aria-live','polite');area.append(status);
  }
  async function poll(identifier,current){
    try{const row=await api('/api/jobs/'+identifier);if(!currentVersion(current))return;
      if(row?.id!==identifier||!statuses.includes(row.status))throw Error('Invalid check plan job');activeJob=row;
      if(row.status==='completed'){const value=await api('/api/jobs/'+identifier+'/check-plan.json');if(!currentVersion(current))return;
        if(!valid(value))throw Error('Invalid check plan source');bundle=value;busy=false;notice=null;redraw();return;}
      if(['queued','running'].includes(row.status)){busy=true;redraw();timer=setTimeout(()=>{timer=null;return poll(identifier,current);},1000);return;}
      busy=false;consent=false;notice=null;redraw();
    }catch(_){if(currentVersion(current)){busy=false;consent=false;notice='Chưa đọc được tác vụ hoặc kế hoạch khớp báo cáo nguồn. Mở lại tác vụ đã lưu; không tự gọi model.';redraw();}}
  }
  async function loadSaved(){
    if(!binding||busy||posting)return;const current=version;busy=true;notice=null;redraw();
    try{const value=await api('/api/jobs/'+selected+'/check-plans');if(!currentVersion(current))return;
      if(value?.source_job_id!==selected||value.source_pack_sha256!==report.source.pack_sha256||typeof value.available!=='boolean'||!Array.isArray(value.plan_jobs))throw Error('Invalid saved plans');
      const seen=new Set();for(const row of value.plan_jobs){if(!jobID(row?.id)||seen.has(row.id)||!statuses.includes(row.status))throw Error('Invalid saved job');seen.add(row.id);}
      available=value.available;saved=value.plan_jobs;consent=false;
    }catch(_){if(currentVersion(current))notice='Chưa đọc được danh sách kế hoạch đã lưu. Không có lời gọi model mới.';}
    finally{if(currentVersion(current)){busy=false;redraw();}}
  }
  async function openJob(identifier){if(!binding||busy||posting||!jobID(identifier))return;stopTimer();const current=++version;
    activeJob=null;bundle=null;consent=false;busy=true;notice=null;redraw();await poll(identifier,current);}
  async function generate(){
    if(!binding||!available||busy||posting||activeJob||!consent||report.hypotheses.length<2)return;
    if(document.getElementById('run-button')?.disabled){notice='Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.';redraw();return;}
    const current=version,request={source_job_id:selected,source_pack_sha256:report.source.pack_sha256,confirmation:'generate_check_plan_with_api_cost'};
    busy=true;consent=false;notice='Đang đề xuất kiểm tra từ các giả thuyết và bằng chứng đã lưu…';redraw();
    try{const row=await api('/api/check-plans',request);if(!currentVersion(current))return;if(!jobID(row?.id))throw Error('Invalid check job');await poll(row.id,current);}
    catch(_){if(currentVersion(current)){busy=false;notice='Chưa xác nhận được tác vụ kiểm tra. Mở danh sách đã lưu để kiểm tra; gửi lại cùng nguồn chỉ nhận lại tác vụ cũ.';redraw();}}
  }
  async function cancelJob(){
    if(!activeJob||!['queued','running'].includes(activeJob.status)||posting)return;const current=version,identifier=activeJob.id;posting=true;redraw();
    try{await api('/api/jobs/'+identifier+'/cancel',{});if(currentVersion(current))notice='Đã yêu cầu hủy; lời gọi đang gửi có thể vẫn tính phí.';}
    catch(_){if(currentVersion(current))notice='Chưa gửi được yêu cầu hủy; mở lại tác vụ để kiểm tra trạng thái.';}
    finally{if(currentVersion(current)){posting=false;redraw();}}
  }
  async function exportPlan(format){
    if(!['json','md'].includes(format)||!activeJob||!valid(bundle))return;const current=version,value=bundle,identifier=activeJob.id,source=report.sha256;
    const matches=()=>currentVersion(current)&&bundle===value&&activeJob?.id===identifier&&report.sha256===source;
    try{const response=await fetch('/api/jobs/'+identifier+'/check-plan.'+format,{headers:{'X-IssueIntel-Token':document.querySelector('meta[name="issueintel-session"]').content}});
      if(!matches())return;if(!response.ok||response.headers.get('X-IssueIntel-Report-SHA256')!==source||response.headers.get('X-IssueIntel-Check-Plan-SHA256')!==value.sha256)throw Error('Invalid exported plan');
      let body;if(format==='json'){const exported=await response.json();if(!matches())return;if(exported.sha256!==value.sha256||!valid(exported))throw Error('Invalid exported plan');body=new Blob([JSON.stringify(exported,null,2)],{type:'application/json'});}
      else body=await response.blob();if(!matches())return;const url=URL.createObjectURL(body),link=node('a');link.href=url;link.download='issueintel-'+identifier+'-check-plan.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(_){if(matches()){notice='Chưa xuất được kế hoạch khớp báo cáo đang xem. Mở lại tác vụ đã lưu để kiểm tra.';redraw();}}
  }
  function render(target,value,job,sourcePack,options={}){
    const next=sourceBinding(value,job,sourcePack);if(next!==binding){reset();selected=job;report=value;pack=sourcePack;binding=next;available=options.available===true;}
    else if(options.available===false)available=false;
    area=node('section',undefined,'product-checks');target.append(area);redraw();
  }
  document.addEventListener('issueintel:packchange',event=>{if(event.detail.job_id!==selected||event.detail.intake?.source_pack_sha256!==report?.source?.pack_sha256){reset();selected=null;report=null;pack=null;binding=null;area=null;}});
  document.addEventListener('issueintel:languagechange',()=>{consent=false;redraw();});
  window.IssueIntelProductChecks=Object.freeze({render,valid,cancel:()=>{area=null;}});
})();

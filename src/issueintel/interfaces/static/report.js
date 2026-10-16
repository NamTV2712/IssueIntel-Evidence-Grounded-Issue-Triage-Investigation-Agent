/* Presentation of the saved, versioned report. Rendering never calls a provider. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t;
  const node=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const text=value=>typeof value==='string';
  const strings=value=>Array.isArray(value)&&value.every(text);
  let active=null;

  function valid(report,job,expected){
    if(!report||typeof report!=='object'||report.schema!=='investigation_report_v1'||!/^[a-f0-9]{24}$/.test(job||'')||report.source?.job_id!==job
      ||!digest(report.sha256)||!digest(report.source.pack_sha256)||!digest(report.source.query_sha256)
      ||!digest(report.source.snapshot_sha256)||!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(report.source.commit_sha||'')
      ||!text(report.issue?.case_id)||!text(report.issue?.repository)||!text(report.summary?.text)
      ||report.summary.kind!=='issue_excerpt'||!['evidence_only','hypotheses_unverified','abstained'].includes(report.findings?.status)
      ||report.findings.causal_status!=='unverified'||report.findings.severity!=='unassessed'
      ||report.findings.confidence_probability!==null||report.findings.confidence_method!==null
      ||report.execution?.status!=='completed'||!Array.isArray(report.evidence)||!Array.isArray(report.hypotheses)
      ||!Array.isArray(report.candidate_locations)||!Array.isArray(report.next_checks)||!strings(report.unknowns)||!strings(report.limitations)
      ||(report.provenance!==undefined&&(!Array.isArray(report.provenance)||report.provenance.some(row=>!text(row?.kind)||!text(row?.details)))))return false;
    if(expected&&['pack_sha256','query_sha256','snapshot_sha256','commit_sha'].some(key=>report.source[key]!==expected[key]))return false;
    const evidence=new Set(),hypotheses=new Set();
    for(const item of report.evidence){
      if(!text(item.id)||!item.id||evidence.has(item.id)||!text(item.kind)||!text(item.locator)||!digest(item.sha256)||!digest(item.excerpt_sha256))return false;
      evidence.add(item.id);
    }
    const refs=(values,known)=>strings(values)&&values.every(value=>known.has(value))&&new Set(values).size===values.length;
    if(!refs(report.summary.evidence_ids,evidence))return false;
    for(const item of report.hypotheses){
      if(!text(item.id)||!item.id||hypotheses.has(item.id)||!text(item.statement)||!text(item.falsification_test)
        ||!refs(item.support_ids,evidence)||!refs(item.counter_ids,evidence)||!item.verification
        ||!refs(item.verification.model_evidence_ids,evidence)||item.verification.citation_integrity!=='valid'
        ||item.verification.semantic_support!=='unverified'||item.verification.independent_human!==false
        ||![null,'supported','unsupported','contradicted','uncertain'].includes(item.verification.model_assessment))return false;
      hypotheses.add(item.id);
    }
    return report.candidate_locations.every(item=>text(item.path)&&Number.isInteger(item.start_line)&&item.start_line>0
      &&Number.isInteger(item.end_line)&&item.end_line>=item.start_line&&refs(item.evidence_ids,evidence))
      &&report.next_checks.every(item=>text(item.id)&&text(item.text)&&item.kind==='proposed_check'
        &&item.execution_status==='not_executed_by_issueintel'&&refs(item.hypothesis_ids,hypotheses)&&refs(item.evidence_ids,evidence));
  }

  function codeURL(report,evidence){
    if(evidence.kind!=='code'||evidence.revision!==report.source.commit_sha)return null;
    const repository=report.issue.repository.match(/^(?:https:\/\/github\.com\/)?([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?$/);
    const issue=report.issue.issue_url?.match(/^https:\/\/github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\/issues\/[1-9][0-9]*$/);
    const locator=evidence.locator.match(/^(.+):(\d+)-(\d+)$/);
    if(!repository||!issue||!locator||repository[1].toLowerCase()!==issue[1].toLowerCase()||repository[2].toLowerCase()!==issue[2].toLowerCase()
      ||issue.slice(1).some(part=>part==='.'||part==='..'))return null;
    const [,path,start,end]=locator,parts=path.split('/');
    if(path.includes('\\')||parts.some(part=>!part||part==='.'||part==='..')||/[\u0000-\u001f\u007f]/.test(path)
      ||Number(start)<1||Number(end)<Number(start))return null;
    const encode=part=>encodeURIComponent(part).replace(/[!'()*]/g,char=>'%'+char.charCodeAt(0).toString(16).toUpperCase());
    const expected='https://github.com/'+issue[1]+'/'+issue[2]+'/blob/'+report.source.commit_sha+'/'+parts.map(encode).join('/')+'#L'+start+'-L'+end;
    // Only a backend-approved exact-commit link is made clickable.
    return evidence.url===expected?expected:null;
  }

  function citations(ids,report,binding){
    const area=node('div',undefined,'report-citations');
    for(const id of ids){
      const evidence=report.evidence.find(item=>item.id===id),button=node('button',id,'citation');button.type='button';
      button.setAttribute('aria-label',t('Xem bằng chứng {id}',{id}));
      button.onclick=()=>{
        if(active!==binding)return;
        document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:report.source.job_id,evidence_id:id}}));
      };
      area.append(button);
      const url=codeURL(report,evidence);
      if(url){const link=node('a',t('Code tại commit này'));link.href=url;link.target='_blank';link.rel='noreferrer noopener';area.append(link);}
    }
    return area;
  }

  function section(title,description){
    const item=node('section',undefined,'report-section');item.append(node('h3',title));
    if(description)item.append(node('p',description,'notice'));return item;
  }
  function progressive(area,items,limit,renderItem,label){
    for(const item of items.slice(0,limit))area.append(renderItem(item));
    if(items.length>limit){const more=node('details',undefined,'report-more');more.append(node('summary',t(label,{count:items.length-limit})));
      for(const item of items.slice(limit))more.append(renderItem(item));area.append(more);}
  }

  function hypothesis(item,report,binding){
    const card=node('article',undefined,'report-hypothesis');
    card.append(node('h4',item.id+' · '+item.statement),node('span',t('Chưa xác minh nguyên nhân'),'badge'));
    const support=node('div',undefined,'report-reference-group');support.append(node('span',t('Nguồn model dùng để hỗ trợ')),citations(item.support_ids,report,binding));card.append(support);
    const counter=node('div',undefined,'report-reference-group');counter.append(node('span',t('Nguồn model dùng để phản bác')));
    if(item.counter_ids.length)counter.append(citations(item.counter_ids,report,binding));
    else counter.append(node('p',t('Chưa có nguồn phản bác trong kết quả này; không chứng minh giả thuyết đúng.'),'notice'));
    card.append(counter);
    const detail=node('details',undefined,'report-review');detail.append(node('summary',t('Citation, nhận xét model và kiểm người')));
    const verification=item.verification;
    detail.append(node('p',t('Tính toàn vẹn citation: ')+t(verification.citation_integrity||'unknown')),
      node('p',t('Ý nghĩa bằng chứng: ')+t(verification.semantic_support||'unverified')),
      node('p',t('Nhận xét model: ')+(verification.model_assessment?t(verification.model_assessment):t('Chưa có nhận xét model'))));
    if(verification.model_reason)detail.append(node('p',verification.model_reason,'report-source-text'));
    detail.append(citations(verification.model_evidence_ids,report,binding),node('p',t('Chưa có kiểm người độc lập cho kết luận này.')));
    card.append(detail);return card;
  }

  const reasons={
    causal_unverified:'Nguyên nhân chưa được xác minh.',
    unassessed_severity:'Chưa đánh giá mức độ ảnh hưởng.',
    uncalibrated_confidence:'Chưa có xác suất tin cậy đã hiệu chỉnh.',
    no_independent_human:'Chưa có kiểm người độc lập cho kết luận này.',
    runtime_not_executed:'IssueIntel chưa chạy các bước kiểm tra được đề xuất.',
    model_hypotheses_not_generated:'Phiên này chỉ truy xuất bằng chứng; chưa sinh giả thuyết.',
    counter_evidence_not_found_in_retrieved_scope:'Chưa tìm thấy phản chứng trong phạm vi đã tra.',
    model_answerability_abstain:'Báo cáo nguồn giữ kết luận ở trạng thái chưa đủ căn cứ.',
    model_answerability_uncertain:'Báo cáo nguồn giữ kết luận ở trạng thái chưa đủ căn cứ.'
  };
  // Only these fixed, authored contract limitations are localized. Source and
  // dynamically recorded limitations retain the exact saved wording.
  const authoredLimitations=new Map([
    ['The summary is an exact issue excerpt, not an independently verified observation.','Tóm tắt là trích nguyên văn issue, chưa phải quan sát đã được xác minh độc lập.'],
    ['Candidate locations are retrieved code, not confirmed affected files.','Vị trí ứng viên là code được truy xuất, chưa xác nhận là file bị ảnh hưởng.'],
    ['Citation integrity and model critique do not establish causal truth.','Citation khớp nguồn và nhận xét model chưa chứng minh nguyên nhân.'],
    ['No independent human validation is claimed.','Chưa có xác nhận kiểm người độc lập.'],
    ['Suggested checks were not executed by IssueIntel; user checklist states are separate.','IssueIntel chưa chạy các bước kiểm tra được đề xuất; trạng thái checklist do người dùng tự báo cáo được lưu riêng.'],
    ['Unknown severity and uncalibrated probability remain unassessed/null.','Mức độ ảnh hưởng chưa được đánh giá; xác suất chưa hiệu chỉnh được giữ ở null.']
  ]);
  function render(target,report,job,expected){
    active=null;target.replaceChildren();
    if(!valid(report,job,expected)){
      target.append(node('p',t('Chưa đọc được báo cáo tổng quan của phiên này. Các tab bằng chứng và bản JSON pack gốc vẫn dùng được.'),'notice'));return;
    }
    const binding=Object.freeze({job,sha256:report.sha256});active=binding;
    const overview=node('div',undefined,'investigation-report');
    const state=node('div',undefined,'report-status');state.append(node('span',t('Lượt phân tích đã hoàn tất'),'badge'),
      node('strong',t(report.findings.status==='evidence_only'?'Đã thu thập bằng chứng · chưa có giả thuyết':'Cần xác minh thêm')));overview.append(state);
    const identity=node('p',report.issue.repository+' · '+report.issue.case_id,'report-identity');overview.append(identity,
      node('p',t('Commit đã phân tích: ')+report.source.commit_sha,'hash-note'));
    if(report.abstention?.reason){const abstention=node('details',undefined,'report-review');
      abstention.append(node('summary',t('Giới hạn kết luận do model nêu')),node('p',report.abstention.reason,'report-source-text'));
      overview.append(abstention);}
    const summary=section(t('Issue mô tả điều gì?'),t('Trích nội dung người báo issue; chưa xác minh hành vi được mô tả.'));
    summary.append(node('blockquote',report.summary.text,'report-source-text'),citations(report.summary.evidence_ids,report,binding));
    if(report.summary.truncated)summary.append(node('p',t('Trích đoạn đã rút gọn. Mở bằng chứng issue để xem nguồn đã lưu.'),'field-note'));
    overview.append(summary);
    const ideas=section(t('Giả thuyết cần kiểm tra'),t('Thứ tự theo báo cáo nguồn, không phải xếp hạng độ tin cậy.'));
    if(report.hypotheses.length)progressive(ideas,report.hypotheses,2,item=>hypothesis(item,report,binding),'Xem thêm {count} giả thuyết');
    else ideas.append(node('p',t('Phiên này chỉ truy xuất bằng chứng; chưa sinh giả thuyết.'),'notice'));
    overview.append(ideas);
    const next=section(t('Bước tiếp theo'),t('Các bước được đề xuất; IssueIntel chưa chạy code hoặc test để xác nhận kết quả.'));
    if(report.next_checks.length)progressive(next,report.next_checks,3,item=>{
      const row=node('article',undefined,'report-check');row.append(node('p',item.text,'report-source-text'));
      if(item.hypothesis_ids.length)row.append(node('p',t('Liên quan giả thuyết: ')+item.hypothesis_ids.join(' · '),'hash-note'));
      row.append(citations(item.evidence_ids,report,binding));return row;
    },'Xem thêm {count} bước kiểm tra');else next.append(node('p',t('Chưa có bước kiểm tra trong báo cáo nguồn.'),'notice'));
    overview.append(next);
    const locations=section(t('Vị trí code nên xem'),t('Nguồn được truy xuất, chưa xác nhận là vị trí gây lỗi hoặc cần sửa.'));
    if(report.candidate_locations.length)progressive(locations,report.candidate_locations,3,item=>{
      const row=node('div',undefined,'report-location');row.append(node('p',item.path+':'+item.start_line+'–'+item.end_line,'locator'),citations(item.evidence_ids,report,binding));return row;
    },'Xem thêm {count} vị trí');else locations.append(node('p',t('Chưa có vị trí code trong phạm vi bằng chứng này.'),'notice'));
    overview.append(locations);
    const unknowns=node('details',undefined,'report-unknowns');unknowns.append(node('summary',t('Điều chưa biết và giới hạn')));
    const list=node('ul');for(const reason of report.unknowns)list.append(node('li',Object.hasOwn(reasons,reason)?t(reasons[reason]):reason));
    unknowns.append(list);for(const limitation of report.limitations)unknowns.append(node('p',authoredLimitations.has(limitation)?t(authoredLimitations.get(limitation)):limitation,'notice'));overview.append(unknowns);
    const provenance=node('details',undefined,'report-provenance');provenance.append(node('summary',t('Nguồn của báo cáo tổng quan')),
      node('p',t('Báo cáo được dựng từ pack đã lưu, không gọi lại model.'),'notice'),
      node('p',t('Phiên nguồn: ')+report.source.job_id,'hash-note'),node('p','Pack SHA256 '+report.source.pack_sha256,'hash-note'),
      node('p','Query SHA256 '+report.source.query_sha256,'hash-note'),node('p','Snapshot SHA256 '+report.source.snapshot_sha256,'hash-note'),
      node('p','Report SHA256 '+report.sha256,'hash-note'),node('p',t('Lý do kết thúc: ')+report.execution.stop_reason,'hash-note'));
    for(const row of report.provenance||[])provenance.append(node('p',row.kind+' · '+row.details,'notice'));
    overview.append(provenance);target.append(overview);
  }
  window.IssueIntelReport=Object.freeze({render});
})();

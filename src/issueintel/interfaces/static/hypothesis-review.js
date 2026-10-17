/* Saved hypothesis roles and gaps. This view never calls a model or infers truth. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t;
  const node=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const text=value=>typeof value==='string';
  const digest=value=>text(value)&&/^[a-f0-9]{64}$/.test(value);
  const strings=value=>Array.isArray(value)&&value.every(text)&&new Set(value).size===value.length;
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'
    ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  const same=(left,right)=>JSON.stringify(canonical(left))===JSON.stringify(canonical(right));
  const gaps={missing_counter_in_retrieved_scope:'Thiếu nguồn phản bác trong phạm vi đã tra',no_code_support_citation:'Chưa có citation hỗ trợ từ code',
    model_review_not_recorded:'Chưa ghi nhận nhận xét model',model_review_unsupported:'Model chưa thấy nguồn hỗ trợ đủ',
    model_review_contradicted:'Model đánh giá có nguồn phản bác',model_review_uncertain:'Model còn chưa chắc chắn'};
  let active=null,selection=null;

  function matchesView(view,job,report,expected){
    if(!view||view.schema!=='hypothesis_review_v1'||!report||report.schema!=='investigation_report_v1'
      ||!/^[a-f0-9]{24}$/.test(job||'')||view.source?.job_id!==job||report.source?.job_id!==job
      ||!digest(view.sha256)||!digest(report.sha256)||view.source.report_sha256!==report.sha256
      ||view.correctness!=='unverified'||view.independent_human!==false||view.evaluation_performed!==false||view.model_calls!==0
      ||!Array.isArray(view.hypotheses)||view.hypotheses.length>5||!Array.isArray(view.pairs)||view.pairs.length>10
      ||!Array.isArray(view.checks)||!Array.isArray(view.evidence)||!strings(view.limitations)
      ||!Array.isArray(report.hypotheses)||!Array.isArray(report.evidence)||!Array.isArray(report.next_checks)
      ||!same(view.issue,{case_id:report.issue?.case_id,repository:report.issue?.repository,issue_url:report.issue?.issue_url})
      ||!same(view.checks,report.next_checks))return false;
    for(const [key,value] of Object.entries(report.source))if(view.source[key]!==value)return false;
    if(!['pack_sha256','query_sha256','snapshot_sha256'].every(key=>digest(view.source[key]))
      ||!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(view.source.commit_sha||'')
      ||!expected||['pack_sha256','query_sha256','snapshot_sha256','commit_sha'].some(key=>view.source[key]!==expected[key]))return false;
    const evidence=new Map(),hypotheses=new Set(),checks=new Set(),refs=(values,known)=>strings(values)&&values.every(id=>known.has(id));
    for(const row of report.evidence){if(!text(row?.id)||evidence.has(row.id)||!text(row.kind)||!text(row.locator)||!digest(row.sha256)||!digest(row.excerpt_sha256))return false;evidence.set(row.id,row);}
    if(report.hypotheses.length!==view.hypotheses.length)return false;
    const used=new Set();
    for(const [index,row] of view.hypotheses.entries()){
      const original=report.hypotheses[index],review=original?.verification;
      if(!row||!text(row.id)||hypotheses.has(row.id)||!review||!text(row.statement)||!refs(row.support_ids,evidence)||!refs(row.counter_ids,evidence)
        ||!same(['id','statement','support_ids','counter_ids','counter_status'].map(key=>row[key]),['id','statement','support_ids','counter_ids','counter_status'].map(key=>original[key]))
        ||!['found','not_found'].includes(row.counter_status)||Boolean(row.counter_ids.length)!==(row.counter_status==='found')
        ||row.citation_integrity!=='valid'||row.semantic_support!=='unverified'||!refs(row.model_review?.evidence_ids,evidence)
        ||!same(row.model_review,{assessment:review.model_assessment,reason:review.model_reason,evidence_ids:review.model_evidence_ids,independent_human:false})
        ||review.citation_integrity!=='valid'||review.semantic_support!=='unverified'||review.independent_human!==false
        ||![null,'supported','unsupported','contradicted','uncertain'].includes(row.model_review.assessment)
        ||!(row.model_review.reason===null||text(row.model_review.reason))||!strings(row.check_ids)||!strings(row.gap_codes))return false;
      const expectedGaps=[];
      if(row.counter_status==='not_found')expectedGaps.push('missing_counter_in_retrieved_scope');
      if(!row.support_ids.some(id=>evidence.get(id).kind==='code'))expectedGaps.push('no_code_support_citation');
      if(row.model_review.assessment===null)expectedGaps.push('model_review_not_recorded');
      else if(row.model_review.assessment!=='supported')expectedGaps.push('model_review_'+row.model_review.assessment);
      if(!same(row.gap_codes,expectedGaps)||!same(row.check_ids,view.checks.filter(check=>check.hypothesis_ids.includes(row.id)).map(check=>check.id)))return false;
      hypotheses.add(row.id);[...row.support_ids,...row.counter_ids,...row.model_review.evidence_ids].forEach(id=>used.add(id));
    }
    for(const row of view.checks){
      if(!text(row?.id)||checks.has(row.id)||!text(row.text)||row.kind!=='proposed_check'||row.execution_status!=='not_executed_by_issueintel'
        ||!refs(row.hypothesis_ids,hypotheses)||!refs(row.evidence_ids,evidence))return false;
      checks.add(row.id);row.evidence_ids.forEach(id=>used.add(id));
    }
    if(!same(view.evidence,report.evidence.filter(row=>used.has(row.id))))return false;
    const pairs=[];
    for(let left=0;left<view.hypotheses.length;left++)for(let right=left+1;right<view.hypotheses.length;right++){
      const a=view.hypotheses[left],b=view.hypotheses[right],intersection=(xs,ys,inventory=view.evidence)=>inventory.filter(row=>xs.includes(row.id)&&ys.includes(row.id)).map(row=>row.id);
      pairs.push({left_hypothesis_id:a.id,right_hypothesis_id:b.id,shared_support_ids:intersection(a.support_ids,b.support_ids),shared_counter_ids:intersection(a.counter_ids,b.counter_ids),
        left_support_right_counter_ids:intersection(a.support_ids,b.counter_ids),left_counter_right_support_ids:intersection(a.counter_ids,b.support_ids),shared_check_ids:intersection(a.check_ids,b.check_ids,view.checks)});
    }
    return same(view.pairs,pairs);
  }
  function valid(view,job,report,expected){try{return matchesView(view,job,report,expected);}catch(_){return false;}}

  function citations(ids,binding){
    const area=node('div',undefined,'hypothesis-review-citations');
    for(const id of ids){const button=node('button',id,'citation');button.type='button';button.setAttribute('aria-label',t('Xem bằng chứng {id}',{id}));
      button.onclick=()=>{if(active!==binding)return;document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:binding.job,evidence_id:id}}));};area.append(button);}
    return area;
  }
  function role(title,ids,binding,empty='Chưa có nguồn được gán vai trò này.'){
    const area=node('div',undefined,'hypothesis-review-role');area.append(node('h5',t(title)));
    area.append(ids.length?citations(ids,binding):node('p',t(empty),'notice'));return area;
  }
  function checkRows(ids,view,binding){
    const area=node('div',undefined,'hypothesis-review-checks');
    if(!ids.length)area.append(node('p',t('Chưa có bước kiểm tra trong báo cáo nguồn.'),'notice'));
    for(const id of ids){const row=view.checks.find(check=>check.id===id),item=node('article');item.append(node('p',id,'hash-note'),node('p',row.text,'hypothesis-review-source'),citations(row.evidence_ids,binding));area.append(item);}
    return area;
  }
  function detail(row,view,binding){
    const area=node('article',undefined,'hypothesis-review-detail');area.append(node('h4',row.id),node('p',row.statement,'hypothesis-review-source'));
    area.append(role('Nguồn model dùng để hỗ trợ',row.support_ids,binding),role('Nguồn model dùng để phản bác',row.counter_ids,binding,'Chưa có nguồn phản bác trong kết quả này; không chứng minh giả thuyết đúng.'));
    const review=node('details',undefined,'hypothesis-review-model');review.append(node('summary',t('Nhận xét verifier bằng model')),
      node('p',row.model_review.assessment?t(row.model_review.assessment):t('Chưa có nhận xét model'),'notice'));
    if(row.model_review.reason)review.append(node('p',row.model_review.reason,'hypothesis-review-source'));
    review.append(citations(row.model_review.evidence_ids,binding),node('p',t('Citation khớp nguồn; ý nghĩa và nguyên nhân chưa được xác minh. Chưa có kiểm người độc lập.'),'notice'));area.append(review);
    const checks=node('section',undefined,'hypothesis-review-check-list');checks.append(node('h5',t('Bước kiểm tra đã đề xuất')),node('p',t('Đề xuất từ báo cáo nguồn; IssueIntel chưa chạy code hoặc test.'),'notice'),checkRows(row.check_ids,view,binding));area.append(checks);return area;
  }
  function render(target,view,job,report,expected,options={}){
    active=null;
    const area=node('section',undefined,'hypothesis-review');area.append(node('h3',t('Đối chiếu giả thuyết')));target.append(area);
    let matches=false;try{matches=valid(view,job,report,expected);}catch(_){/* Keep the rest of a saved report usable on malformed input. */}
    if(!matches){area.append(node('p',t('Chưa đọc được bản đối chiếu gắn với báo cáo này. Các tab bằng chứng và JSON pack gốc vẫn dùng được.'),'notice'));
      if(options.reload){const retry=node('button',t('Tải lại bản đối chiếu'));retry.type='button';retry.onclick=options.reload;area.append(retry);}return;}
    const binding=Object.freeze({job,sha256:view.sha256});active=binding;
    const tools=node('div',undefined,'hypothesis-review-toolbar');
    for(const [format,label] of [['json','Xuất đối chiếu JSON'],['md','Xuất đối chiếu Markdown']])if(options.export){const button=node('button',t(label));button.type='button';button.onclick=()=>{if(active===binding)options.export(format);};tools.append(button);}
    area.append(node('p',t('Xem nguồn nào còn thiếu và bước nào cần kiểm tra tiếp. Thứ tự theo báo cáo nguồn, không xếp hạng hoặc chọn giả thuyết thắng.'),'notice'),
      node('p',view.issue.repository+' · '+view.issue.case_id,'hypothesis-review-identity'),node('p',t('Commit đã phân tích: ')+view.source.commit_sha,'hash-note'),tools);
    if(!view.hypotheses.length){area.append(node('p',t('Phiên này chỉ truy xuất bằng chứng; chưa sinh giả thuyết.'),'notice'));return;}
    const scroll=node('div',undefined,'hypothesis-review-table-wrap'),table=node('table',undefined,'hypothesis-review-table'),caption=node('caption',t('Giả thuyết, citation và khoảng trống đã ghi nhận'));table.append(caption);
    scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',t('Giả thuyết, citation và khoảng trống đã ghi nhận'));
    const head=node('thead'),heading=node('tr');for(const title of ['Giả thuyết','Hỗ trợ / phản bác','Nhận xét model','Khoảng trống cần xem']){const cell=node('th',t(title));cell.setAttribute('scope','col');heading.append(cell);}head.append(heading);table.append(head);
    const body=node('tbody');
    for(const row of view.hypotheses){const tr=node('tr'),statement=node('th');statement.setAttribute('scope','row');statement.append(node('strong',row.id),node('p',row.statement.length>180?row.statement.slice(0,180)+'…':row.statement,'hypothesis-review-summary'));
      const source=node('td');source.append(node('span',t('Hỗ trợ'),'hypothesis-review-label'),citations(row.support_ids,binding),node('span',t('Phản bác'),'hypothesis-review-label'));
      source.append(row.counter_ids.length?citations(row.counter_ids,binding):node('span',t('chưa tìm thấy'),'notice'));
      const review=node('td',row.model_review.assessment?t(row.model_review.assessment):t('Chưa có nhận xét model')),
        gap=node('td');if(row.gap_codes.length)for(const code of row.gap_codes)gap.append(node('p',t(gaps[code]),'hypothesis-review-gap'));
      else gap.append(node('p',t('Không có cờ khoảng trống thuộc các quy tắc hiện có; chưa xác nhận giả thuyết.'),'notice'));
      tr.append(statement,source,review,gap);body.append(tr);}
    table.append(body);scroll.append(table);area.append(scroll);
    area.append(node('p',t('Hỗ trợ/phản bác là vai trò do model gán cho citation đã lưu. Thiếu phản bác chưa chứng minh đúng; dùng cùng nguồn với vai trò khác chưa chứng minh hai giả thuyết mâu thuẫn.'),'notice'));
    const focus=node('section',undefined,'hypothesis-review-focus');focus.append(node('h4',t(view.hypotheses.length>1?'Chọn hai giả thuyết để đọc cạnh nhau':'Đọc giả thuyết và bước kiểm tra')));area.append(focus);
    const controls=node('div',undefined,'hypothesis-review-selectors'),details=node('div',undefined,'hypothesis-review-details'),overlap=node('section',undefined,'hypothesis-review-overlap');
    const remembered=selection?.job===job&&selection.sha256===view.sha256?selection:null;
    let left=remembered?.left||view.hypotheses[0].id,right=remembered?.right||view.hypotheses[1]?.id;
    const selects=[];
    const refresh=()=>{
      if(active!==binding)return;
      selection={job,sha256:view.sha256,left,right};
      details.replaceChildren(detail(view.hypotheses.find(row=>row.id===left),view,binding));overlap.replaceChildren();
      if(!right)return;
      details.append(detail(view.hypotheses.find(row=>row.id===right),view,binding));
      const reversed=view.hypotheses.findIndex(row=>row.id===left)>view.hypotheses.findIndex(row=>row.id===right),pair=view.pairs.find(row=>row.left_hypothesis_id===(reversed?right:left)&&row.right_hypothesis_id===(reversed?left:right));
      overlap.append(node('h4',t('Nguồn và bước kiểm tra dùng chung')),node('p',t('Chỉ đối chiếu ID trùng nhau trong báo cáo đã lưu; không suy luận tương đương hay mâu thuẫn ngữ nghĩa.'),'notice'));
      const grid=node('div',undefined,'hypothesis-review-overlap-grid');grid.append(role('Cùng dùng để hỗ trợ',pair.shared_support_ids,binding),role('Cùng dùng để phản bác',pair.shared_counter_ids,binding));
      for(const [title,ids] of [[t('{left} hỗ trợ · {right} phản bác',{left,right}),reversed?pair.left_counter_right_support_ids:pair.left_support_right_counter_ids],
        [t('{left} phản bác · {right} hỗ trợ',{left,right}),reversed?pair.left_support_right_counter_ids:pair.left_counter_right_support_ids]]){
        const group=node('div',undefined,'hypothesis-review-role');group.append(node('h5',title),ids.length?citations(ids,binding):node('p',t('Không có ID dùng chung ở vai trò này.'),'notice'));grid.append(group);}
      overlap.append(grid,node('h5',t('Bước kiểm tra có cùng ID')),pair.shared_check_ids.length?checkRows(pair.shared_check_ids,view,binding):node('p',t('Không có bước kiểm tra cùng ID; điều này chưa chứng minh các bước độc lập.'),'notice'));
    };
    if(right){for(const [index,title] of ['Giả thuyết bên trái','Giả thuyết bên phải'].entries()){
      const label=node('label',t(title)),select=node('select');select.id='hypothesis-review-'+job+'-'+index;label.htmlFor=select.id;
      for(const row of view.hypotheses){const option=node('option',row.id+' · '+(row.statement.length>85?row.statement.slice(0,85)+'…':row.statement));option.value=row.id;select.append(option);}select.value=index?right:left;
      select.onchange=()=>{if(active!==binding)return;if(index){right=select.value;if(right===left){left=view.hypotheses.find(row=>row.id!==right).id;selects[0].value=left;}}
        else{left=select.value;if(left===right){right=view.hypotheses.find(row=>row.id!==left).id;selects[1].value=right;}}refresh();};
      selects.push(select);const field=node('div');field.append(label,select);controls.append(field);}
      focus.append(controls);}
    focus.append(details,overlap);refresh();
    const provenance=node('details',undefined,'hypothesis-review-provenance');provenance.append(node('summary',t('Nguồn bản đối chiếu')),
      node('p',t('Bản đối chiếu chỉ đọc báo cáo đã lưu, không gọi model, chạy kiểm tra hoặc tạo nhãn người.'),'notice'),node('p',t('Phiên nguồn: ')+job,'hash-note'));
    for(const [label,value] of [['Pack',view.source.pack_sha256],['Query',view.source.query_sha256],['Snapshot',view.source.snapshot_sha256],['Report',view.source.report_sha256],['Review',view.sha256]])provenance.append(node('p',label+' SHA256 '+value,'hash-note'));
    area.append(provenance);
  }
  document.addEventListener('issueintel:packchange',()=>{active=null;});
  window.IssueIntelHypothesisReview=Object.freeze({render,valid,cancel:()=>{active=null;}});
})();

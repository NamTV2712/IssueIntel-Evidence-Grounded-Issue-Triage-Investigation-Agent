/* Read-only presentation of a source-bound bounded investigation workflow. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t;
  const node=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const text=value=>typeof value==='string';
  const digest=value=>text(value)&&/^[a-f0-9]{64}$/.test(value);
  const strings=value=>Array.isArray(value)&&value.every(text)&&new Set(value).size===value.length;
  const bound=(value,min,max)=>Number.isInteger(value)&&value>=min&&value<=max;
  const reasons={single_file_or_no_missing_counter:'Một file hoặc chưa có cue cần mở rộng',multiple_code_files:'Bằng chứng nằm ở nhiều file',
    missing_counter_evidence:'Giả thuyết còn thiếu nguồn phản bác',snapshot_gaps:'Snapshot có phần không đọc được',broad_missing_counter_evidence:'Nhiều file và thiếu nguồn phản bác'};
  const stops={provider_not_configured:'Chưa bật provider; dừng ở bằng chứng',insufficient_evidence:'Chưa đủ bằng chứng để sinh giả thuyết',
    no_new_queries:'Không còn truy vấn mới trong giới hạn',no_new_evidence:'Không tìm thêm bằng chứng mới',round_limit:'Đã chạm giới hạn vòng tìm thêm',
    controller_stopped:'Controller chọn dừng',controller_skipped_retrieval:'Controller chuyển thẳng sang verifier',
    waiting_for_user:'Đang chờ dữ kiện từ người dùng',generation_limit:'Đã chạm giới hạn sinh giả thuyết'};
  const actions={retrieve:'Tìm thêm bằng chứng',verify:'Chuyển sang verifier',stop:'Dừng điều tra',ask:'Hỏi dữ kiện còn thiếu'};
  const purposes={support:'Tìm nguồn hỗ trợ',counter:'Tìm nguồn phản bác',caller:'Lần theo caller tĩnh',
    documentation:'Tìm tài liệu trong snapshot',history:'Tìm lịch sử trong nguồn đã khóa'};
  const purposeReasons={support:'Tìm code bổ sung trong cùng snapshot; độ liên quan từ vựng chưa xác nhận hỗ trợ ngữ nghĩa.',
    counter:'Tìm theo điều kiện bác bỏ đã đề xuất; kết quả khớp chưa phải phản bác đã xác nhận.',
    caller:'Xem caller tĩnh chính xác của symbol trong code đã trích; chưa biết luồng thực thi.',
    documentation:'Tìm tài liệu trong cùng snapshot về hành vi và ràng buộc có thể áp dụng.',
    history:'Xem context có receipt và changelog trong snapshot; tính sẵn có theo thời gian chưa được xác minh.'};
  const gaps={missing_counter_evidence:'Chưa có nguồn phản bác được gắn',uninspected_callers:'Caller tĩnh chưa được xem',
    uninspected_documentation:'Tài liệu trong snapshot chưa được xem',uninspected_history:'Lịch sử đã khóa nguồn chưa được xem',
    missing_code_support:'Chưa có code hỗ trợ được gắn',support_scope_incomplete:'Phạm vi nguồn hỗ trợ còn thiếu'};
  const purposeGaps={support:['missing_code_support','support_scope_incomplete'],counter:['missing_counter_evidence'],
    caller:['uninspected_callers'],documentation:['uninspected_documentation'],history:['uninspected_history']};
  const unavailable={no_exact_callers_in_available_graph:'Chưa tìm thấy caller chính xác trong graph được cung cấp',
    no_documentation_in_snapshot:'Chưa có tài liệu phù hợp trong snapshot được cung cấp',
    no_history_in_available_sources:'Chưa có lịch sử phù hợp trong nguồn đã khóa',
    no_matching_snapshot_evidence:'Chưa có bằng chứng phù hợp trong snapshot được cung cấp'};
  const purposeUnavailable={support:['no_matching_snapshot_evidence'],counter:['no_matching_snapshot_evidence'],
    caller:['no_exact_callers_in_available_graph','no_matching_snapshot_evidence'],
    documentation:['no_documentation_in_snapshot'],history:['no_history_in_available_sources']};
  const attemptLabels={settled:'Đã ghi nhận usage',failed_bound:'Lời gọi lỗi; giữ cận chi phí',missing_usage_bound:'Thiếu usage; giữ cận chi phí',
    breach:'Usage vượt cận chi phí',blocked:'Đã chặn trước khi gọi',unreceipted_error:'Chưa có receipt hợp lệ'};
  let active=null;

  function gapSearchValid(search,known,added,hypothesisIds,evidence){
    const boundedText=(value,maximum)=>text(value)&&value.length>0&&value.length<=maximum;
    const ids=(values,maximum)=>strings(values)&&values.length<=maximum&&values.every(value=>boundedText(value,2000));
    if(search.policy!=='gap_directed_retrieval_v1'||search.max_queries!==3||search.max_added_evidence!==6
      ||!ids(search.excluded_query_sha256s,10)||search.excluded_query_sha256s.some(value=>!digest(value))
      ||!Array.isArray(search.skipped_queries)||search.skipped_queries.length>15
      ||!['new_candidates_added','no_new_evidence','no_new_queries'].includes(search.stop_reason))return false;
    if(search.skipped_queries.some(query=>!query||!boundedText(query.action_id,2000)||!Object.hasOwn(purposes,query.purpose)
      ||!hypothesisIds.has(query.seed_hypothesis_id)||!digest(query.query_sha256)||query.reason!=='already_executed'||query.executed!==false))return false;
    const actionIds=new Set();
    for(const query of search.queries){
      if(!boundedText(query.action_id,2000)||actionIds.has(query.action_id)||!Object.hasOwn(purposes,query.purpose)
        ||!purposeGaps[query.purpose].includes(query.gap_code)||!boundedText(query.reason,200)||!boundedText(query.query,4000)
        ||!digest(query.query_sha256)||!hypothesisIds.has(query.seed_hypothesis_id)
        ||!ids(query.source_ids,100)||query.source_ids.some(id=>!known.has(id))||!ids(query.target_node_ids,100)
        ||!ids(query.inspected_ids,100)||query.inspected_ids.some(id=>!known.has(id)||evidence.get(id).kind!=='repository_context')
        ||!ids(query.returned_ids,100)||!ids(query.added_ids,6)||query.added_ids.some(id=>!added.has(id))
        ||!['executed','unavailable'].includes(query.status))return false;
      if(query.status==='executed'&&query.unavailable_reason!==null)return false;
      if(query.status==='unavailable'&&(!purposeUnavailable[query.purpose].includes(query.unavailable_reason)||query.returned_ids.length||query.added_ids.length))return false;
      if(query.purpose!=='history'&&query.inspected_ids.length)return false;
      actionIds.add(query.action_id);
    }
    return true;
  }

  function valid(plan,job,pack){
    if(!plan||plan!==pack?.workflow_plan||!((plan.schema==='planned_investigation_v1'&&plan.policy==='bounded_planner_v1')
      ||(plan.schema==='planned_investigation_v2'&&plan.policy==='bounded_planner_v2')
      ||(plan.schema==='planned_investigation_v3'&&plan.policy==='bounded_planner_v3'))
      ||!/^[a-f0-9]{24}$/.test(job||'')||!digest(plan.sha256)||!Array.isArray(pack.evidence)
      ||!digest(plan.source?.query_sha256)||!digest(plan.source?.snapshot_sha256)
      ||plan.source.commit_sha!==pack.snapshot?.commit_sha||plan.source.snapshot_sha256!==pack.snapshot.snapshot_sha256
      ||plan.routing?.kind!=='observable_heuristic'||!strings(plan.routing.reason_codes)||plan.routing.reason_codes.some(value=>!Object.hasOwn(reasons,value))
      ||!bound(plan.routing.max_retrieval_rounds,1,3)||!plan.routing.signals
      ||!['code_files','not_found_hypotheses','parse_error_count','skipped_file_count'].every(key=>bound(plan.routing.signals[key],0,1000000))
      ||!Array.isArray(plan.rounds)||!Object.hasOwn(stops,plan.stop_reason))return false;
    const evidence=new Map();
    for(const item of pack.evidence){if(!text(item.id)||evidence.has(item.id))return false;evidence.set(item.id,item);}
    const issues=pack.evidence.filter(item=>item.kind==='issue');
    if(issues.length!==1||issues[0].sha256!==plan.source.query_sha256)return false;
    const limits=plan.limits;
    if(!limits||limits.max_retrieval_rounds!==plan.routing.max_retrieval_rounds
      ||limits.max_queries_per_round!==3||limits.max_added_per_round!==6||limits.max_added_total!==18
      ||limits.max_generation_passes!==limits.max_retrieval_rounds+1||limits.max_provider_calls!==2*limits.max_generation_passes+1||limits.max_verifier_passes!==1
      ||plan.rounds.length>limits.max_retrieval_rounds||!bound(plan.generation_passes,0,limits.max_generation_passes)||!bound(plan.provider_calls,0,limits.max_provider_calls)
      ||!bound(plan.verifier_passes,0,limits.max_verifier_passes)||!strings(plan.seed_evidence_ids)
      ||!plan.seed_evidence_ids.length||plan.seed_evidence_ids.some(id=>!evidence.has(id)))return false;
    const inherited=plan.schema==='planned_investigation_v3'?(plan.inherited?.counts||{retrieval_rounds:0,generation_passes:0,verifier_passes:0,provider_calls:0,added_evidence:0}):null;
    const used={retrieval_rounds:plan.rounds.length,generation_passes:plan.generation_passes,
      verifier_passes:plan.verifier_passes,provider_calls:plan.provider_calls,added_evidence:pack.evidence.length-plan.seed_evidence_ids.length};
    if(plan.schema==='planned_investigation_v3'){
      if(!digest(plan.policy_sha256)||!(plan.inherited===null||plan.inherited?.counts)
        ||!Object.keys(used).every(key=>bound(inherited[key],0,18)))return false;
      const maximum={retrieval_rounds:limits.max_retrieval_rounds,generation_passes:limits.max_generation_passes,
        verifier_passes:limits.max_verifier_passes,provider_calls:limits.max_provider_calls,added_evidence:limits.max_added_total};
      if(Object.keys(used).some(key=>used[key]+inherited[key]>maximum[key]))return false;
      if(plan.waiting!==null&&(!window.IssueIntelPlannedWaiting?.validPlan?.(plan)))return false;
      if((plan.waiting!==null)!==(plan.stop_reason==='waiting_for_user')
        ||(plan.waiting!==null)!==(plan.final_decision?.action==='ask'))return false;
    }else if(plan.stop_reason==='waiting_for_user'||plan.stop_reason==='generation_limit')return false;
    const known=new Set(plan.seed_evidence_ids);
    const refs=(values,ids)=>strings(values)&&values.every(value=>ids.has(value));
    const hypotheses=(rows,ids)=>Array.isArray(rows)&&rows.every(row=>text(row?.id)&&text(row.statement)
      &&refs(row.support_ids,ids)&&refs(row.counter_ids,ids)&&text(row.falsification_test))&&new Set(rows.map(row=>row.id)).size===rows.length;
    const decision=row=>row&&Object.hasOwn(actions,row.action)&&(plan.schema==='planned_investigation_v3'||row.action!=='ask')&&text(row.reason)&&text(row.controller)&&typeof row.fallback==='boolean';
    if(plan.seed_annotation!==null&&!hypotheses(plan.seed_annotation?.hypotheses,known))return false;
    for(const [index,row] of plan.rounds.entries()){
      if(row.round!==index+1||!decision(row)||!refs(row.input_evidence_ids,known)||row.input_evidence_ids.length!==known.size
        ||!hypotheses(row.input_hypotheses,known)||!(row.input_annotation_sha256===null||digest(row.input_annotation_sha256))
        ||!strings(row.added_ids)||row.added_ids.length>6||row.added_ids.some(id=>known.has(id)||!evidence.has(id))
        ||!(row.output_annotation_sha256===null||digest(row.output_annotation_sha256)))return false;
      const added=new Set(row.added_ids),hypothesisIds=new Set(row.input_hypotheses.map(item=>item.id));
      if(row.search!==null){
        const search=row.search;
        if(row.action!=='retrieve'||search.counter_evidence_confirmed!==false||search.snapshot_sha256!==plan.source.snapshot_sha256
          ||!refs(search.added_ids,added)||search.added_ids.length!==added.size||!Array.isArray(search.queries)||search.queries.length>3
          ||search.queries.some(query=>!text(query.query)||!digest(query.query_sha256)||!hypothesisIds.has(query.seed_hypothesis_id)||!refs(query.added_ids,added)))return false;
        if(plan.schema!=='planned_investigation_v1'&&!gapSearchValid(search,known,added,hypothesisIds,evidence))return false;
      }else if(row.added_ids.length)return false;
      row.added_ids.forEach(id=>known.add(id));
      if(row.output_annotation!==null&&!hypotheses(row.output_annotation?.hypotheses,known))return false;
    }
    return known.size-plan.seed_evidence_ids.length<=18&&known.size===evidence.size&&decision(plan.final_decision)&&plan.final_decision.action!=='retrieve';
  }

  function citations(ids,binding){
    const row=node('div',undefined,'workflow-citations');
    for(const id of ids){const button=node('button',id,'citation');button.type='button';button.setAttribute('aria-label',t('Xem bằng chứng {id}',{id}));
      button.onclick=()=>{if(active!==binding)return;document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:binding.job,evidence_id:id}}));};row.append(button);}
    return row;
  }
  function gapActions(target,search,binding){
    for(const query of search?.queries||[]){
      const action=node('section',undefined,'workflow-routing');
      action.append(node('h4',t(purposes[query.purpose])),node('p',t('Giả thuyết đầu vào: {id}',{id:query.seed_hypothesis_id}),'hash-note'),
        node('p',t('Khoảng trống: {gap}',{gap:t(gaps[query.gap_code])}),'workflow-source-text'),
        node('p',t('Lý do: {reason}',{reason:t(purposeReasons[query.purpose])}),'workflow-source-text'),
        node('p',t(query.status==='executed'?'Đã chạy trên nguồn đã khóa':'Chưa khả dụng trong phạm vi nguồn đã khóa'),'notice'));
      if(query.status==='unavailable')action.append(node('p',t(unavailable[query.unavailable_reason]),'notice'));
      action.append(node('p',t('{returned} ứng viên trả về · {inspected} nguồn lịch sử đã xem · {added} trích đoạn mới',
        {returned:query.returned_ids.length,inspected:query.inspected_ids.length,added:query.added_ids.length}),'notice'));
      if(query.purpose==='history')action.append(node('p',t('Lịch sử là context đã khóa nguồn, chưa xác minh tính sẵn có trước thời điểm issue.'),'notice'));
      if(query.added_ids.length)action.append(citations(query.added_ids,binding));
      const source=node('details');source.append(node('summary',t('Truy vấn và nguồn của hành động')),
        node('p',t('Lý do đã ghi: {reason}',{reason:query.reason}),'workflow-source-text'),
        node('pre',query.query),node('p','Query SHA256 '+query.query_sha256,'hash-note'),
        node('p',t('Định danh hành động: {id}',{id:query.action_id}),'hash-note'),
        node('p',t('Mã khoảng trống: {code}',{code:query.gap_code}),'hash-note'));
      if(query.source_ids.length)source.append(node('p',t('Nguồn dẫn tới khoảng trống'),'notice'),citations(query.source_ids,binding));
      if(query.inspected_ids.length)source.append(node('p',t('Nguồn lịch sử đã xem'),'notice'),citations(query.inspected_ids,binding));
      if(query.target_node_ids.length)source.append(node('p',t('Node đích tĩnh: {ids}',{ids:query.target_node_ids.join(', ')}),'workflow-source-text'));
      if(query.returned_ids.length)source.append(node('p',t('Ứng viên trả về: {ids}',{ids:query.returned_ids.join(', ')}),'workflow-source-text'));
      action.append(source);target.append(action);
    }
  }
  function controllerPanel(area,sidecar,plan,job){
    if(!sidecar)return;
    const panel=node('section',undefined,'workflow-controller');panel.append(node('h4',t('Quyết định Jev / Typesafe')));area.append(panel);
    const sameSource=source=>source&&['query_sha256','snapshot_sha256','commit_sha'].every(key=>source[key]===plan.source[key]);
    const expected=plan.seed_annotation===null?[]:plan.rounds.slice();
    if(plan.seed_annotation!==null&&(!expected.length||expected.at(-1).action==='retrieve'))expected.push(plan.final_decision);
    let matches=sidecar.schema==='typesafe_controller_v1'&&sidecar.provider==='typesafe'&&sidecar.model==='jev-1.13.0'
      &&sameSource(sidecar.source)&&sidecar.workflow_plan_sha256===plan.sha256&&sidecar.job_id===job&&digest(sidecar.sha256)
      &&sidecar.max_decisions===4&&Array.isArray(sidecar.records)&&sidecar.records.length<=4&&sidecar.records.length===expected.length;
    if(matches)matches=sidecar.records.every((record,index)=>{
      const receipt=record?.receipt,chosen=expected[index];
      return record?.decision_index===index+1&&Array.isArray(record.allowed)&&record.allowed.length>=2&&record.allowed.length<=3
        &&new Set(record.allowed).size===record.allowed.length&&record.allowed.every(value=>Object.hasOwn(actions,value))&&record.allowed.includes(record.action)
        &&Object.hasOwn(attemptLabels,record.attempt_status)&&text(record.reason)&&record.reason.length<=80&&typeof record.fallback==='boolean'
        &&digest(record.state_sha256)&&(record.request_sha256===null||digest(record.request_sha256))&&sameSource(record.binding)
        &&record.binding.job_id===job&&record.binding.decision_index===index+1&&chosen.controller==='jev'&&chosen.action===record.action&&chosen.fallback===record.fallback
        &&(receipt===null?['blocked','unreceipted_error'].includes(record.attempt_status):receipt.schema==='typesafe_decision_receipt_v1'
          &&receipt.provider==='typesafe'&&receipt.model===sidecar.model&&receipt.status===record.attempt_status&&digest(receipt.sha256)
          &&receipt.request_sha256===record.request_sha256&&sameSource(receipt.binding)&&receipt.binding.job_id===job
          &&receipt.binding.decision_index===index+1&&receipt.currency==='USD'&&receipt.is_provider_invoice===false
          &&Number.isSafeInteger(receipt.micro_usd_charged_or_reserved)&&receipt.micro_usd_charged_or_reserved>=0);
    });
    if(!matches){panel.append(node('p',t('Không thể gắn quyết định Typesafe với phiên và kế hoạch này. Xem JSON gốc để kiểm tra.'),'notice'));return;}
    panel.append(node('p',t('{count} / {limit} lượt quyết định đã ghi · model {model}',{count:sidecar.records.length,limit:sidecar.max_decisions,model:sidecar.model})),
      node('p',t('Các lượt này tính riêng ngoài call sinh giả thuyết/verifier. Quyết định chọn bước không xác minh chất lượng bằng chứng hoặc nguyên nhân.'),'notice'));
    if(!sidecar.records.length)panel.append(node('p',t('Không gọi Typesafe trong phiên này.'),'notice'));
    for(const record of sidecar.records){
      const row=node('details',undefined,'workflow-controller-record');row.append(node('summary',t('Quyết định {number}: {action}',{number:record.decision_index,action:t(actions[record.action])})),
        node('p',t('Fallback theo luật: {fallback}',{fallback:t(record.fallback?'Có':'Không')}),'notice'),node('p',t(attemptLabels[record.attempt_status]),'notice'));
      if(record.receipt)row.append(node('p',t('USD ghi nhận/giữ chỗ: {usd}; chưa phải hóa đơn.',{usd:(record.receipt.micro_usd_charged_or_reserved/1000000).toFixed(6)}),'notice'));
      row.append(node('p',t('Lý do: {reason}',{reason:record.reason}),'workflow-source-text'));panel.append(row);
    }
    const provenance=node('details');provenance.append(node('summary',t('Nguồn quyết định')),node('p','Decision SHA256 '+sidecar.sha256,'hash-note'),
      node('p','Plan SHA256 '+sidecar.workflow_plan_sha256,'hash-note'));panel.append(provenance);
  }
  function render(target,plan,job,pack,options={}){
    active=null;
    window.IssueIntelPlannedWaiting?.cancel();
    const area=node('section',undefined,'investigation-workflow');area.append(node('h3',t('Điều phối điều tra')));target.append(area);
    if(!plan){area.append(node('p',t('Báo cáo này dùng luồng Quick/Deep cũ, không có kế hoạch Planned đã lưu. Trace gốc bên dưới giữ nguyên; không suy diễn các vòng chưa ghi nhận.'),'notice'));return;}
    let matches=false;try{matches=valid(plan,job,pack);}catch(_){/* A malformed saved artifact must not break the report view. */}
    if(!matches){area.append(node('p',t('Không thể gắn kế hoạch với query và snapshot của báo cáo này. Citation đã ẩn; có thể xem JSON gốc để kiểm tra.'),'notice'));return;}
    const binding={job,plan,pack};active=binding;
    area.append(node('p',t('Kế hoạch hữu hạn theo tín hiệu quan sát; chưa đánh giá độ khó hoặc chất lượng. Nguồn tìm thêm chưa phải phản bác đã xác nhận.'),'notice'));
    const gapDirected=plan.schema!=='planned_investigation_v1';
    if(gapDirected)area.append(node('p',t('Mỗi hành động gắn với một khoảng trống đã ghi. Caller là quan hệ tĩnh; chưa khả dụng chỉ nói về phạm vi nguồn hiện có.'),'notice'));
    const route=node('div',undefined,'workflow-routing');route.append(node('h4',t('Vì sao có giới hạn này?')));
    for(const reason of plan.routing.reason_codes)route.append(node('p',t(reasons[reason]),'workflow-reason'));
    const signal=plan.routing.signals;
    route.append(node('p',t('{files} file code · {missing} giả thuyết thiếu phản bác · {parse} lỗi parse · {skipped} file bị bỏ qua',
      {files:signal.code_files,missing:signal.not_found_hypotheses,parse:signal.parse_error_count,skipped:signal.skipped_file_count}),'notice'));
    area.append(route);
    const counts=plan.schema==='planned_investigation_v3'?(plan.inherited?.counts||{retrieval_rounds:0,generation_passes:0,verifier_passes:0,provider_calls:0,added_evidence:0}):null;
    const local={retrieval_rounds:plan.rounds.length,generation_passes:plan.generation_passes,
      verifier_passes:plan.verifier_passes,provider_calls:plan.provider_calls,added_evidence:pack.evidence.length-plan.seed_evidence_ids.length};
    if(counts){
      area.append(node('h4',t('Ngân sách toàn chuỗi điều tra')),
        node('p',t('Giới hạn giữ nguyên từ phiên gốc; tiếp tục không cấp lại vòng tìm kiếm hoặc call API.'),'notice'));
      const cumulative=node('dl',undefined,'workflow-budgets');
      for(const [key,label,maximum] of [['retrieval_rounds','Vòng tìm thêm',plan.limits.max_retrieval_rounds],
        ['generation_passes','Lượt sinh giả thuyết',plan.limits.max_generation_passes],['verifier_passes','Lượt verifier riêng',plan.limits.max_verifier_passes],
        ['provider_calls','Call sinh và verifier đã ghi nhận',plan.limits.max_provider_calls],['added_evidence','Trích đoạn mới',plan.limits.max_added_total]]){
        const entry=node('div');entry.append(node('dt',t(label)),node('dd',(counts[key]+local[key])+' / '+maximum));cumulative.append(entry);
      }
      area.append(cumulative,node('h4',t('Sử dụng trong phiên đang xem')));
    }
    const budgets=node('dl',undefined,'workflow-budgets');
    for(const [label,value] of [[t('Vòng tìm thêm'),plan.rounds.filter(row=>row.action==='retrieve').length+' / '+plan.limits.max_retrieval_rounds],
      [t('Lượt sinh giả thuyết'),plan.generation_passes+' / '+plan.limits.max_generation_passes],[t('Lượt verifier riêng'),plan.verifier_passes+' / '+plan.limits.max_verifier_passes],
      [t('Call sinh và verifier đã ghi nhận'),plan.provider_calls+' / '+plan.limits.max_provider_calls]]){
      const entry=node('div');entry.append(node('dt',label),node('dd',value));budgets.append(entry);}
    area.append(budgets,node('p',t('Giới hạn sinh/verifier: {calls} call, tối đa 3 truy vấn/6 trích đoạn mới mỗi vòng và 18 trích đoạn mới tổng cộng. Lượt sinh có thể kèm một lần sửa schema. Call Jev/Typesafe tính riêng; xem Token và chi phí để đọc receipt.',{calls:plan.limits.max_provider_calls}),'notice'));
    controllerPanel(area,pack.controller_decisions,plan,job);
    const seed=node('details',undefined,'workflow-seed');seed.append(node('summary',t('Bắt đầu với {count} nguồn bằng chứng',{count:plan.seed_evidence_ids.length})),citations(plan.seed_evidence_ids,binding));area.append(seed);
    const steps=node('ol',undefined,'workflow-rounds');
    for(const row of plan.rounds){
      const item=node('li',undefined,'workflow-round');item.append(node('h4',t('Bước {round}: {action}',{round:row.round,action:t(actions[row.action])})));
      if(gapDirected){
        item.append(node('p',t('{executed} hành động đã chạy · {unavailable} hành động chưa khả dụng · {added} trích đoạn mới',
          {executed:row.search?.queries.filter(query=>query.status==='executed').length||0,
            unavailable:row.search?.queries.filter(query=>query.status==='unavailable').length||0,added:row.added_ids.length}),'notice'));
        gapActions(item,row.search,binding);
      }else{
        item.append(node('p',t('{queries} truy vấn đã chạy · {added} trích đoạn mới',{queries:row.search?.queries.length||0,added:row.added_ids.length}),'notice'));
        if(row.added_ids.length)item.append(citations(row.added_ids,binding));
      }
      const details=node('details');details.append(node('summary',t('Xem quyết định và truy vấn đã lưu')),
        node('p',t('Lý do: {reason}',{reason:row.reason}),'workflow-source-text'),
        node('p',t('Controller: {controller} · fallback theo luật: {fallback}',{controller:row.controller,fallback:t(row.fallback?'Có':'Không')}),'notice'));
      if(!gapDirected)for(const query of row.search?.queries||[])details.append(node('p',t('Giả thuyết đầu vào: {id}',{id:query.seed_hypothesis_id}),'hash-note'),node('pre',query.query));
      if(row.search?.skipped_queries?.length)details.append(node('p',t('{count} truy vấn trùng đã bỏ qua; chưa chạy lại.',{count:row.search.skipped_queries.length}),'notice'));
      item.append(details);steps.append(item);
    }
    area.append(steps);
    const stop=node('div',undefined,'workflow-stop');stop.append(node('strong',t(stops[plan.stop_reason])),node('p',t('Dừng tìm kiếm không xác nhận nguyên nhân hoặc bản sửa. Verifier là nhận xét model riêng, chưa phải kiểm người.'),'notice'));
    area.append(stop,node('p',t('Phiên nguồn: ')+job,'hash-note'));
    const provenance=node('details',undefined,'workflow-provenance');provenance.append(node('summary',t('Nguồn kế hoạch')),
      node('p','Query SHA256 '+plan.source.query_sha256,'hash-note'),node('p','Commit '+plan.source.commit_sha,'hash-note'),
      node('p','Snapshot SHA256 '+plan.source.snapshot_sha256,'hash-note'),node('p','Plan SHA256 '+plan.sha256,'hash-note'));
    area.append(provenance);
    if(plan.schema==='planned_investigation_v3'&&plan.waiting)window.IssueIntelPlannedWaiting?.render(area,plan,job,pack,options);
  }
  document.addEventListener('issueintel:packchange',()=>{active=null;});
  window.IssueIntelWorkflow=Object.freeze({render});
})();

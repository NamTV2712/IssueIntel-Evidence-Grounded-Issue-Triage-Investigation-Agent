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
    controller_stopped:'Controller chọn dừng',controller_skipped_retrieval:'Controller chuyển thẳng sang verifier'};
  const actions={retrieve:'Tìm thêm bằng chứng',verify:'Chuyển sang verifier',stop:'Dừng điều tra'};
  let active=null;

  function valid(plan,job,pack){
    if(!plan||plan!==pack?.workflow_plan||plan.schema!=='planned_investigation_v1'||plan.policy!=='bounded_planner_v1'
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
    const known=new Set(plan.seed_evidence_ids);
    const refs=(values,ids)=>strings(values)&&values.every(value=>ids.has(value));
    const hypotheses=(rows,ids)=>Array.isArray(rows)&&rows.every(row=>text(row?.id)&&text(row.statement)
      &&refs(row.support_ids,ids)&&refs(row.counter_ids,ids)&&text(row.falsification_test))&&new Set(rows.map(row=>row.id)).size===rows.length;
    const decision=row=>row&&Object.hasOwn(actions,row.action)&&text(row.reason)&&text(row.controller)&&typeof row.fallback==='boolean';
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
  function render(target,plan,job,pack){
    active=null;
    const area=node('section',undefined,'investigation-workflow');area.append(node('h3',t('Điều phối điều tra')));target.append(area);
    if(!plan){area.append(node('p',t('Báo cáo này dùng luồng Quick/Deep cũ, không có kế hoạch Planned đã lưu. Trace gốc bên dưới giữ nguyên; không suy diễn các vòng chưa ghi nhận.'),'notice'));return;}
    let matches=false;try{matches=valid(plan,job,pack);}catch(_){/* A malformed saved artifact must not break the report view. */}
    if(!matches){area.append(node('p',t('Không thể gắn kế hoạch với query và snapshot của báo cáo này. Citation đã ẩn; có thể xem JSON gốc để kiểm tra.'),'notice'));return;}
    const binding={job,plan,pack};active=binding;
    area.append(node('p',t('Kế hoạch hữu hạn theo tín hiệu quan sát; chưa đánh giá độ khó hoặc chất lượng. Nguồn tìm thêm chưa phải phản bác đã xác nhận.'),'notice'));
    const route=node('div',undefined,'workflow-routing');route.append(node('h4',t('Vì sao có giới hạn này?')));
    for(const reason of plan.routing.reason_codes)route.append(node('p',t(reasons[reason]),'workflow-reason'));
    const signal=plan.routing.signals;
    route.append(node('p',t('{files} file code · {missing} giả thuyết thiếu phản bác · {parse} lỗi parse · {skipped} file bị bỏ qua',
      {files:signal.code_files,missing:signal.not_found_hypotheses,parse:signal.parse_error_count,skipped:signal.skipped_file_count}),'notice'));
    area.append(route);
    const budgets=node('dl',undefined,'workflow-budgets');
    for(const [label,value] of [[t('Vòng tìm thêm'),plan.rounds.filter(row=>row.action==='retrieve').length+' / '+plan.limits.max_retrieval_rounds],
      [t('Lượt sinh giả thuyết'),plan.generation_passes+' / '+plan.limits.max_generation_passes],[t('Lượt verifier riêng'),plan.verifier_passes+' / '+plan.limits.max_verifier_passes],
      [t('Call provider đã ghi nhận'),plan.provider_calls+' / '+plan.limits.max_provider_calls]]){
      const entry=node('div');entry.append(node('dt',label),node('dd',value));budgets.append(entry);}
    area.append(budgets,node('p',t('Giới hạn: {calls} call provider, tối đa 3 truy vấn/6 trích đoạn mới mỗi vòng và 18 trích đoạn mới tổng cộng. Lượt sinh có thể kèm một lần sửa schema. Chi phí thực tế xem ở Token và chi phí.',{calls:plan.limits.max_provider_calls}),'notice'));
    const seed=node('details',undefined,'workflow-seed');seed.append(node('summary',t('Bắt đầu với {count} nguồn bằng chứng',{count:plan.seed_evidence_ids.length})),citations(plan.seed_evidence_ids,binding));area.append(seed);
    const steps=node('ol',undefined,'workflow-rounds');
    for(const row of plan.rounds){
      const item=node('li',undefined,'workflow-round');item.append(node('h4',t('Bước {round}: {action}',{round:row.round,action:t(actions[row.action])})));
      item.append(node('p',t('{queries} truy vấn đã chạy · {added} trích đoạn mới',{queries:row.search?.queries.length||0,added:row.added_ids.length}),'notice'));
      if(row.added_ids.length)item.append(citations(row.added_ids,binding));
      const details=node('details');details.append(node('summary',t('Xem quyết định và truy vấn đã lưu')),
        node('p',t('Lý do: {reason}',{reason:row.reason}),'workflow-source-text'),
        node('p',t('Controller: {controller} · fallback theo luật: {fallback}',{controller:row.controller,fallback:t(row.fallback?'Có':'Không')}),'notice'));
      for(const query of row.search?.queries||[])details.append(node('p',t('Giả thuyết đầu vào: {id}',{id:query.seed_hypothesis_id}),'hash-note'),node('pre',query.query));
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
  }
  document.addEventListener('issueintel:packchange',()=>{active=null;});
  window.IssueIntelWorkflow=Object.freeze({render});
})();

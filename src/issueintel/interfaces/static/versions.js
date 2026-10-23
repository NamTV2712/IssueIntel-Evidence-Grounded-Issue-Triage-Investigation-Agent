/* Descriptive revision inspection; never updates an investigation's citations. */
(() => {
  'use strict';
  const get=id=>document.getElementById(id), t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  let selected=null, scope=null, result=null, resultJob=null, rows=[], running=null, notice=null;
  let generation=0,resultVersion=0,historyVersion=0,impactFilter='all';
  const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const commit=value=>typeof value==='string'&&/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value);
  const statuses=['unchanged','moved','changed','missing','unavailable','ambiguous'];
  const impactLabels={unchanged:'Đoạn giữ nguyên',moved:'Đoạn khớp ở vị trí khác',changed:'Đoạn nguồn thay đổi',missing:'Không thấy trong phạm vi đã đọc',unavailable:'Chưa đối chiếu đủ',ambiguous:'Nhiều vị trí khớp'};
  const current=(source,version)=>scope===source&&generation===version&&source?.available===true;
  const sameSource=(left,right)=>left&&right&&left.job===right.job&&left.sha===right.sha&&left.commit===right.commit;
  const ancestryLabels={
    same_commit:'Nguồn và đích là cùng commit.',
    source_ancestor:'Commit nguồn là tổ tiên của commit đích.',
    target_ancestor:'Commit đích là tổ tiên của commit nguồn.',
    diverged:'Nguồn và đích nằm trên các nhánh lịch sử khác nhau.',
    unknown:'Chưa xác định được quan hệ lịch sử Git.'
  };
  const containmentLabels={
    contained:'Đích chứa commit tham chiếu trong lịch sử Git.',
    not_contained:'Đích không chứa commit tham chiếu trong lịch sử Git đã kiểm.',
    unknown:'Chưa xác định được đích có chứa commit tham chiếu.'
  };
  function renderReachability(area,reachability){
    if(!reachability)return;
    const section=el('section',undefined,'versions-proof');
    section.append(el('h3',t('Quan hệ lịch sử Git')));
    const relation=reachability.source_to_target||{};
    section.append(el('p',t(ancestryLabels[relation.status]||ancestryLabels.unknown),'versions-status'));
    if(relation.reason)section.append(el('p',t('Lý do: {reason}',{reason:t(relation.reason)}),'field-note'));
    if(relation.ancestry_complete===false)section.append(el('p',t('Lịch sử Git chưa đầy đủ; kết quả thiếu bằng chứng giữ trạng thái chưa biết.'),'notice'));
    const reference=reachability.reference;
    if(reference){
      const card=el('section',undefined,'versions-reference-result');
      card.append(el('h4',t('Commit tham chiếu')),
        el('p',t('Ref đã nhập: {revision}',{revision:reference.requested_revision}),'hash-note'),
        el('p',t('Commit đã resolve: {sha}',{sha:reference.commit_sha||t('Chưa resolve được')}),'hash-note'),
        el('p',t(containmentLabels[reference.status]||containmentLabels.unknown),'versions-status'));
      if(reference.reason)card.append(el('p',t('Lý do: {reason}',{reason:t(reference.reason)}),'field-note'));
      section.append(card);
      const tags=reachability.tags;
      if(tags){
        const tagCard=el('section',undefined,'versions-tags');
        tagCard.append(el('h4',t('Tag local chứa commit tham chiếu')),
          el('p',t('Đã quét {scanned}/{total} tag local; giới hạn {limit}.',{scanned:tags.scanned,total:tags.total,limit:tags.limit}),'field-note'));
        if(tags.truncated)tagCard.append(el('p',t('Danh sách tag bị giới hạn; các tag chưa quét vẫn chưa biết.'),'notice'));
        if(tags.unavailable && (Array.isArray(tags.unavailable)?tags.unavailable.length:true))tagCard.append(el('p',t('Có tag không kiểm được; xem chi tiết trong JSON xuất ra.'),'notice'));
        if(tags.containing?.length){
          const list=el('ul');
          for(const tag of tags.containing){const item=el('li');item.append(el('strong',tag.name),el('span',' · '+tag.commit_sha,'hash-note'));list.append(item);}tagCard.append(list);
        }else tagCard.append(el('p',t('Chưa tìm thấy tag chứa commit trong phạm vi đã kiểm.'),'field-note'));
        tagCard.append(el('p',t('Đây là tag trong Git local, chưa xác minh bản phát hành đã công bố hoặc lỗi đã được sửa.'),'notice'));
        section.append(tagCard);
      }
    }
    area.append(section);
  }
  function validQuote(value,revision){return value&&typeof value.path==='string'&&typeof value.locator==='string'&&typeof value.text==='string'
    &&Number.isInteger(value.start_line)&&value.start_line>0&&Number.isInteger(value.end_line)&&value.end_line>=value.start_line
    &&value.revision===revision&&hash(value.sha256)&&hash(value.file_sha256)&&hash(value.raw_sha256)&&commit(value.blob_sha);}
  function validImpact(value,report,source=scope){
    try{
      if(value?.schema!=='product_evidence_impact_v1'||!hash(value.sha256)||value.source?.job_id!==source?.job
        ||value.source.pack_sha256!==report.source.pack_sha256||!hash(value.source.pack_sha256)
        ||(source.sha&&value.source.pack_sha256!==source.sha)||!hash(value.query_sha256)
        ||!['before','after'].every(side=>value[side]?.commit_sha===report[side].commit_sha&&value[side].snapshot_sha256===report[side].snapshot_sha256&&hash(value[side].snapshot_sha256))
        ||value.fix_status!=='unverified'||value.evaluation_performed!==false||value.model_calls!==0||value.target_text_inventory_included!==false
        ||value.verification_basis!=='declared_captured_local_search'||!Array.isArray(value.entries)||value.entries.length>256
        ||!Array.isArray(value.limitations)||!value.limitations.every(line=>typeof line==='string')||!value.limits||typeof value.limits!=='object')return false;
      const counts=Object.fromEntries(statuses.map(status=>[status,0])),seen=new Set();
      for(const row of value.entries){if(typeof row.evidence_id!=='string'||seen.has(row.evidence_id)||!statuses.includes(row.status)||typeof row.reason!=='string'
        ||!validQuote(row.before,value.before.commit_sha)||!Array.isArray(row.hypotheses?.support_ids)||!Array.isArray(row.hypotheses.counter_ids)
        ||![...row.hypotheses.support_ids,...row.hypotheses.counter_ids].every(id=>typeof id==='string')
        ||!['admitted','missing','skipped'].includes(row.after?.path_status)||!Array.isArray(row.after.matches)||row.after.matches.length>64
        ||!row.after.matches.every(quote=>validQuote(quote,value.after.commit_sha)&&quote.text===row.before.text&&quote.sha256===row.before.sha256)
        ||!(row.after.original_slot===null||validQuote(row.after.original_slot,value.after.commit_sha))
        ||typeof row.after.search?.performed!=='boolean'||typeof row.after.search.complete!=='boolean')return false;
        seen.add(row.evidence_id);counts[row.status]++;}
      return statuses.every(status=>value.counts?.[status]===counts[status])&&value.coverage?.reported===value.entries.length
        &&['source_code_evidence_total','source_noncode_omitted','after_admitted_files','after_skipped_files','searches_complete','searches_limited'].every(key=>Number.isInteger(value.coverage[key])&&value.coverage[key]>=0);
    }catch(_){return false;}
  }
  function validResult(value,source=scope){
    if(!value||value.source?.job_id!==source?.job||(source.sha&&value.source.pack_sha256!==source.sha)
      ||!commit(value.before?.commit_sha)||!commit(value.after?.commit_sha)||(source.commit&&value.before.commit_sha!==source.commit)
      ||!Array.isArray(value.files)||value.files.length>200||!value.counts||typeof value.counts!=='object')return false;
    return value.evidence_impact===undefined||validImpact(value.evidence_impact,value,source);
  }
  function quoteCard(quote,label){const card=el('section',undefined,'impact-quote');card.append(el('h5',t(label)),el('p',quote.locator,'locator'),el('pre',quote.text),
    el('p',t('Commit: ')+quote.revision,'hash-note'),el('p','Excerpt SHA256 '+quote.sha256,'hash-note'));
    const hashes=el('details');hashes.append(el('summary',t('Hash file và Git blob')),el('p','File SHA256 '+quote.file_sha256,'hash-note'),el('p','Raw SHA256 '+quote.raw_sha256,'hash-note'),el('p','Git blob '+quote.blob_sha,'hash-note'));card.append(hashes);return card;}
  function renderImpact(area,value){
    const section=el('section',undefined,'evidence-impact');section.id='evidence-impact-summary';
    section.append(el('h3',t('Thay đổi ảnh hưởng bằng chứng nào?')),el('p',t('Đối chiếu đoạn trích đã dẫn, không chuyển citation cũ sang commit mới và không xác nhận bản sửa.'),'notice'));
    const counts=el('dl',undefined,'impact-counts');for(const status of statuses){const cell=el('div');cell.append(el('dt',t(impactLabels[status])),el('dd',String(value.counts[status])));counts.append(cell);}section.append(counts);
    const coverage=value.coverage;section.append(el('p',t('Đã đối chiếu {reported}/{total} đoạn code; bỏ ngoài {noncode} nguồn không phải code.',{reported:coverage.reported,total:coverage.source_code_evidence_total,noncode:coverage.source_noncode_omitted}),'notice'),
      el('p',t('Đích: {admitted} file đã đọc, {skipped} file ngoài phạm vi; {complete} lượt tìm đủ phạm vi, {limited} lượt bị giới hạn.',{admitted:coverage.after_admitted_files,skipped:coverage.after_skipped_files,complete:coverage.searches_complete,limited:coverage.searches_limited}),'notice'),
      el('p',t('Không thấy hoặc chỉ một vị trí khớp chỉ áp dụng cho phạm vi đã đọc. Chưa replay độc lập toàn bộ repository đích.'),'notice'));
    const filterLabel=el('label',t('Lọc trạng thái đoạn trích')),filter=el('select');filter.id='evidence-impact-filter';filterLabel.htmlFor=filter.id;
    for(const status of ['all',...statuses]){const option=el('option',status==='all'?t('Mọi đoạn trích'):t(impactLabels[status]));option.value=status;filter.append(option);}filter.value=impactFilter;
    const rendered=result,version=generation;filter.onchange=()=>{if(result!==rendered||version!==generation)return;if(!['all',...statuses].includes(filter.value))return;impactFilter=filter.value;render();document.getElementById('evidence-impact-filter')?.focus?.();};section.append(filterLabel,filter);
    const items=el('div',undefined,'impact-items');items.id='evidence-impact-items';const visible=value.entries.filter(row=>impactFilter==='all'||row.status===impactFilter);
    if(!visible.length)items.append(el('p',t('Không có đoạn trích thuộc bộ lọc này.'),'notice'));
    for(const row of visible){const entry=el('details',undefined,'impact-entry');entry.dataset.evidenceImpactId=row.evidence_id;entry.dataset.evidenceImpactStatus=row.status;
      entry.append(el('summary',row.evidence_id+' · '+t(impactLabels[row.status])+' · '+row.before.locator));
      entry.append(el('p',t('Giả thuyết dùng nguồn hỗ trợ: ')+(row.hypotheses.support_ids.join(', ')||t('Không có')),'notice'),el('p',t('Giả thuyết dùng nguồn phản bác: ')+(row.hypotheses.counter_ids.join(', ')||t('Không có')),'notice'));
      const quotes=el('div',undefined,'impact-quotes');quotes.append(quoteCard(row.before,'Đoạn nguồn đã dẫn'));
      const target=el('section',undefined,'impact-target');
      if(row.after.original_slot)target.append(quoteCard(row.after.original_slot,'Đoạn tại tọa độ cũ ở đích; chưa phải ánh xạ ngữ nghĩa'));
      for(const match of row.after.matches)target.append(quoteCard(match,'Vị trí khớp chính xác ở đích'));
      if(!row.after.original_slot&&!row.after.matches.length)target.append(el('p',t('Chưa có đoạn đích đủ điều kiện để hiển thị.'),'notice'));
      if(row.after.skipped_reason)target.append(el('p',t('Nguồn đích ngoài phạm vi: ')+row.after.skipped_reason,'notice'));quotes.append(target);entry.append(quotes);
      const details=el('details',undefined,'impact-search');details.append(el('summary',t('Phạm vi tìm và giới hạn')),el('pre',JSON.stringify({reason:row.reason,path_status:row.after.path_status,same_path_file:row.after.same_path_file,search:row.after.search},null,2)));entry.append(details);items.append(entry);}
    section.append(items);const limits=el('details');limits.append(el('summary',t('Giới hạn và nguồn đối chiếu')),el('pre',JSON.stringify({source:value.source,limits:value.limits,limitations:value.limitations,sha256:value.sha256},null,2)));section.append(limits);area.append(section);
  }
  async function exportImpact(format){
    const source=scope,version=generation,saved=result,job=resultJob;if(!job||!saved?.evidence_impact||!validImpact(saved.evidence_impact,saved,source))return;
    const matches=()=>current(source,version)&&result===saved&&resultJob===job;
    try{const response=await fetch('/api/jobs/'+job+'/evidence-impact.'+format,{headers:{'X-IssueIntel-Token':document.querySelector('meta[name="issueintel-session"]').content}});
      if(!response.ok||response.headers.get('X-IssueIntel-Source-Pack-SHA256')!==saved.source.pack_sha256||response.headers.get('X-IssueIntel-Evidence-Impact-SHA256')!==saved.evidence_impact.sha256)throw Error('Changed impact export');
      let body;if(format==='json'){const value=await response.json();if(value.sha256!==saved.evidence_impact.sha256||!validImpact(value,saved,source))throw Error('Changed impact body');body=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});}else body=await response.blob();
      if(!matches())return;const url=URL.createObjectURL(body),link=el('a');link.href=url;link.download='issueintel-'+job+'-evidence-impact.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(_){if(matches()){notice='Chưa xuất được đối chiếu đoạn trích đúng nguồn; mở lại bản đã lưu để kiểm tra.';render();}}
  }
  function render(){
    get('versions-message').textContent=notice?t(notice):'';
    get('versions-submit').disabled=!!running||!scope?.available;
    get('versions-cancel').hidden=!running||!sameSource(running.source,scope);
    get('versions-cancel').disabled=!running?.id||!sameSource(running.source,scope);
    const area=get('versions-result');area.replaceChildren();
    if(result&&validResult(result)){
      area.append(el('p',t('Thay đổi code chưa chứng minh lỗi đã được sửa. Không chạy code/test hoặc gọi LLM.'),'notice'),
        el('p','Before '+result.before.commit_sha,'hash-note'),el('p','After '+result.after.commit_sha,'hash-note'));
      renderReachability(area,result.git_reachability);
      const counts=el('dl',undefined,'repository-counts');
      for(const [status,count] of Object.entries(result.counts)){const cell=el('div');cell.append(el('dt',t(status)),el('dd',String(count)));counts.append(cell);}area.append(counts);
      area.append(el('p',t('Ngoài phạm vi: {before} file nguồn, {after} file đích. Bỏ bớt {omitted} mục do giới hạn hiển thị.',{before:result.before_skipped,after:result.after_skipped,omitted:result.files_omitted}),'notice'));
      const saved=result,version=generation,download=el('button',t('Xuất đối chiếu JSON'));download.type='button';download.onclick=()=>{
        if(result!==saved||version!==generation||!validResult(saved))return;
        const url=URL.createObjectURL(new Blob([JSON.stringify(saved,null,2)],{type:'application/json'}));
        const link=el('a');link.href=url;link.download='issueintel-versions.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      };area.append(download);
      if(result.evidence_impact){const exports=el('div',undefined,'impact-actions');for(const format of ['json','md']){const button=el('button',t(format==='json'?'Xuất tác động bằng chứng JSON':'Xuất tác động bằng chứng Markdown'));button.id='evidence-impact-'+format;button.type='button';button.onclick=()=>{if(saved===result&&version===generation)return exportImpact(format);};exports.append(button);}area.append(exports);renderImpact(area,result.evidence_impact);}
      else area.append(el('p',t('Bản đối chiếu cũ chỉ có diff file; chưa có kết quả đối chiếu từng đoạn trích.'),'notice'));
      for(const file of result.files){
        const card=el('details',undefined,'evidence-card');card.append(el('summary',file.path+' · '+t(file.status)+(file.in_source_evidence?' · '+t('Trong bằng chứng nguồn'):'')));
        for(const side of ['before','after'])if(file[side])card.append(el('p',side+' · SHA256 '+file[side].sha256,'hash-note'));
        if(file.unavailable_before || file.unavailable_after)card.append(el('p',t('File ngoài phạm vi đọc; không kết luận đã xóa.')+' '+(file.unavailable_before || '')+' '+(file.unavailable_after || ''),'notice'));
        if(file.diff){card.append(el('pre',file.diff.text));if(file.diff.truncated)card.append(el('p',t('Diff bị giới hạn; xem metadata trước khi kết luận.'),'notice'));}
        area.append(card);
      }
      area.append(el('p','SHA256 '+result.sha256,'hash-note'));
    }
    const history=get('versions-history');history.replaceChildren();
    for(const row of rows){
      const button=el('button',t('Mở đối chiếu: {id} · {status}',{id:row.id,status:t(row.status)}),'history-row');button.type='button';
      button.disabled=row.status!=='completed';button.onclick=async()=>{
        const source=scope,version=generation,read=++resultVersion;
        try{const report=await api('/api/jobs/'+row.id+'/versions.json');if(current(source,version)&&read===resultVersion){if(!validResult(report,source))throw Error('Changed version source');result=report;resultJob=row.id;impactFilter='all';notice=null;render();}}
        catch(_){if(current(source,version)&&read===resultVersion){notice='Không đọc được đối chiếu; kiểm tra nguồn và hash.';render();}}
      };history.append(button);
    }
  }
  async function loadHistory(source,version=generation){const read=++historyVersion;
    try{const found=await api('/api/jobs/'+source.job+'/versions');if(current(source,version)&&read===historyVersion){if(!Array.isArray(found)||found.length>20)throw Error('Invalid version history');rows=found;render();}}
    catch(_){if(current(source,version)&&read===historyVersion){notice='Không đọc được đối chiếu; kiểm tra nguồn và hash.';render();}}
  }
  document.addEventListener('issueintel:packchange',event=>{
    const detail=event.detail,available=typeof detail.intake?.versions_available==='boolean'?detail.intake.versions_available:detail.available===true;
    const sha=hash(detail.intake?.source_pack_sha256)?detail.intake.source_pack_sha256:null;
    get('versions-panel').hidden=!available;
    if(selected===detail.job_id&&scope?.sha===sha&&scope.available===available)return;
    generation++;resultVersion++;selected=detail.job_id;scope={job:selected,sha,available,commit:detail.pack?.snapshot?.commit_sha||null};
    result=null;resultJob=null;rows=[];notice=null;impactFilter='all';get('versions-reference').value='';render();
    if(available)loadHistory(scope);
  });
  document.addEventListener('issueintel:languagechange',render);
  get('versions-form').onsubmit=async event=>{
    event.preventDefault();if(running || !scope?.available)return;
    const source=scope,version=generation,read=++resultVersion,revision=get('versions-revision').value.trim(), reference=get('versions-reference').value.trim();
    const request={job_id:source.job,revision};if(source.sha)request.source_pack_sha256=source.sha;if(reference)request.reference_revision=reference;
    const operation={source,id:null};running=operation;notice='Đang đọc hai snapshot; không gọi LLM.';render();
    try{
      const job=await api('/api/versions',request);if(typeof job?.id!=='string')throw Error('Invalid version job');operation.id=job.id;
      if(current(source,version))render();
      for(;;){
        const state=await api('/api/jobs/'+job.id);
        if(['completed','failed','cancelled','interrupted'].includes(state.status)){
          if(state.status==='completed'){
            const report=await api('/api/jobs/'+job.id+'/versions.json');
            if(current(source,version)&&read===resultVersion){if(!validResult(report,source))throw Error('Changed version source');result=report;resultJob=job.id;impactFilter='all';notice='Đã lưu đối chiếu phiên bản; tình trạng bản sửa chưa xác minh.';}
          }else if(current(source,version)&&read===resultVersion)notice='Đối chiếu chưa hoàn tất; kiểm tra revision và dữ liệu nguồn.';
          break;
        }
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    }catch(_){if(current(source,version)&&read===resultVersion)notice='Đối chiếu chưa hoàn tất; kiểm tra revision và dữ liệu nguồn.';}
    finally{if(running===operation)running=null;render();if(scope?.available)await loadHistory(scope);}
  };
  get('versions-cancel').onclick=async()=>{
    if(running?.id&&sameSource(running.source,scope))try{await api('/api/jobs/'+running.id+'/cancel',{});}catch(_){}
  };
  window.IssueIntelEvidenceImpact=Object.freeze({validImpact,validResult});
})();

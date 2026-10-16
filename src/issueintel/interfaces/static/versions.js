/* Descriptive revision inspection; never updates an investigation's citations. */
(() => {
  'use strict';
  const get=id=>document.getElementById(id), t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  let selected=null, result=null, rows=[], running=null, notice=null;
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
  function render(){
    get('versions-message').textContent=notice?t(notice):'';
    get('versions-submit').disabled=!!running;
    get('versions-cancel').hidden=!running;
    const area=get('versions-result');area.replaceChildren();
    if(result?.source.job_id===selected){
      area.append(el('p',t('Thay đổi code chưa chứng minh lỗi đã được sửa. Không chạy code/test hoặc gọi LLM.'),'notice'),
        el('p','Before '+result.before.commit_sha,'hash-note'),el('p','After '+result.after.commit_sha,'hash-note'));
      renderReachability(area,result.git_reachability);
      const counts=el('dl',undefined,'repository-counts');
      for(const [status,count] of Object.entries(result.counts)){const cell=el('div');cell.append(el('dt',t(status)),el('dd',String(count)));counts.append(cell);}area.append(counts);
      area.append(el('p',t('Ngoài phạm vi: {before} file nguồn, {after} file đích. Bỏ bớt {omitted} mục do giới hạn hiển thị.',{before:result.before_skipped,after:result.after_skipped,omitted:result.files_omitted}),'notice'));
      const download=el('button',t('Xuất đối chiếu JSON'));download.type='button';download.onclick=()=>{
        const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
        const link=el('a');link.href=url;link.download='issueintel-versions.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      };area.append(download);
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
        const source=selected;
        try{const report=await api('/api/jobs/'+row.id+'/versions.json');if(source===selected){result=report;notice=null;render();}}
        catch(_){if(source===selected){notice='Không đọc được đối chiếu; kiểm tra nguồn và hash.';render();}}
      };history.append(button);
    }
  }
  async function loadHistory(source){
    try{const found=await api('/api/jobs/'+source+'/versions');if(source===selected){rows=found;render();}}
    catch(_){if(source===selected){notice='Không đọc được đối chiếu; kiểm tra nguồn và hash.';render();}}
  }
  document.addEventListener('issueintel:packchange',event=>{
    get('versions-panel').hidden=!event.detail.available;
    if(selected===event.detail.job_id)return;
    selected=event.detail.job_id;result=null;rows=[];notice=null;get('versions-reference').value='';render();
    if(event.detail.available)loadHistory(selected);
  });
  document.addEventListener('issueintel:languagechange',render);
  get('versions-form').onsubmit=async event=>{
    event.preventDefault();if(running || !selected)return;
    const source=selected, revision=get('versions-revision').value.trim(), reference=get('versions-reference').value.trim();
    const request={job_id:source,revision};if(reference)request.reference_revision=reference;
    running='starting';notice='Đang đọc hai snapshot; không gọi LLM.';render();
    try{
      const job=await api('/api/versions',request);running=job.id;
      if(source===selected)render();
      for(;;){
        const state=await api('/api/jobs/'+job.id);
        if(['completed','failed','cancelled','interrupted'].includes(state.status)){
          if(state.status==='completed'){
            const report=await api('/api/jobs/'+job.id+'/versions.json');
            if(source===selected){result=report;notice='Đã lưu đối chiếu phiên bản; tình trạng bản sửa chưa xác minh.';}
          }else if(source===selected)notice='Đối chiếu chưa hoàn tất; kiểm tra revision và dữ liệu nguồn.';
          break;
        }
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    }catch(_){if(source===selected)notice='Đối chiếu chưa hoàn tất; kiểm tra revision và dữ liệu nguồn.';}
    finally{running=null;render();await loadHistory(selected);}
  };
  get('versions-cancel').onclick=async()=>{
    if(running && running!=='starting')try{await api('/api/jobs/'+running+'/cancel',{});}catch(_){}
  };
})();

/* Explicit GitHub release observations; never an automatic fix verdict. */
(() => {
  'use strict';
  const get=id=>document.getElementById(id),t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const commit=value=>typeof value==='string'&&/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value);
  const tag=value=>typeof value==='string'&&/^[A-Za-z0-9_./-]{1,200}$/.test(value);
  const identifier=value=>typeof value==='string'&&/^[a-f0-9]{24}$/.test(value);
  let binding=null,allowed=false,version=0,rows=[],result=null,notice=null,inflight=null,opening=false;
  const same=(left,right)=>!!left&&!!right&&['job_id','pack_sha256','commit_sha','snapshot_sha256','query_sha256'].every(key=>left[key]===right[key]);
  function valid(value,expected){
    return value?.schema==='github_release_check_v1'&&sha(value.sha256)&&same(value.source,expected)&&value.repository?.name===expected.repository
      &&tag(value.requested?.tag)&&commit(value.requested?.reference_commit)
      &&['contained','not_contained','unknown'].includes(value.containment?.status)
      &&value.fix_status==='unverified'&&value.historical_publication_binding==='unverified'
      &&value.model_calls===0&&value.evaluation_performed===false
      &&(value.release===null||(value.release?.tag===value.requested.tag&&['published','not_published'].includes(value.release.publication_status)
        &&typeof value.release.draft==='boolean'&&typeof value.release.prerelease==='boolean'))
      &&(value.tag===null||((value.tag?.commit_sha===null||commit(value.tag?.commit_sha))&&commit(value.tag?.object_sha)));
  }
  function releaseLink(value){
    const name=value.repository?.name,release=value.release;
    if(typeof name!=='string'||!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(name)||release?.tag!==value.requested.tag||typeof release.url!=='string')return null;
    try{
      const link=new URL(release.url),prefix='/'+name+'/releases/tag/';
      if(link.origin!=='https://github.com'||link.username||link.password||link.search||link.hash||!link.pathname.startsWith(prefix))return null;
      return decodeURIComponent(link.pathname.slice(prefix.length))===value.requested.tag?link.href:null;
    }catch(_){return null;}
  }
  function renderResult(area,value){
    const card=el('section',undefined,'release-observation');
    card.append(el('h3',t('Quan sát bản phát hành đã lưu')),
      el('p',t('Tag đã yêu cầu: {tag}',{tag:value.requested.tag}),'release-status'),
      el('p',t('Commit tham chiếu đã kiểm: {sha}',{sha:value.requested.reference_commit}),'hash-note'),
      el('p',t('Thời điểm quan sát: {time}',{time:value.observed_at||t('Chưa biết')}),'field-note'));
    const release=value.release;
    if(release){
      card.append(el('p',release.name||release.tag,'release-name'));
      const status=release.publication_status==='published'&&!release.draft?'Đã công bố trên GitHub tại lúc quan sát.':'Metadata chưa cho thấy bản phát hành đã công bố.';
      card.append(el('p',t(status),'notice'));
      if(release.draft)card.append(el('p',t('Bản nháp · chưa công bố'),'badge'));
      if(release.prerelease)card.append(el('p',t('Bản phát hành thử (prerelease)'),'badge'));
      if(release.published_at)card.append(el('p',t('Ngày công bố theo GitHub: {time}',{time:release.published_at}),'field-note'));
      const url=releaseLink(value);
      if(url){const link=el('a',t('Mở bản phát hành trên GitHub'));link.href=url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);}
    }else card.append(el('p',t('Chưa có metadata bản phát hành đủ để kết luận việc công bố.'),'notice'));
    if(value.tag?.commit_sha)card.append(el('p',t('Commit tag hiện tại: {sha}',{sha:value.tag.commit_sha}),'hash-note'));
    const published=release?.publication_status==='published'&&!release.draft;
    const resolved=commit(value.tag?.commit_sha);
    const status=value.containment.status;
    const conclusion=published&&resolved&&status==='contained'?'Bản phát hành đang công bố chứa commit tham chiếu trong lịch sử Git hiện được quan sát.'
      :published&&resolved&&status==='not_contained'?'Tag của bản phát hành hiện không chứa commit tham chiếu trong lịch sử đã kiểm.'
      :'Chưa đủ bằng chứng để kết luận bản phát hành đã công bố chứa commit tham chiếu.';
    card.append(el('p',t(conclusion),'release-status'));
    if(value.containment.reason)card.append(el('p',t('Lý do: {reason}',{reason:t(value.containment.reason)}),'field-note'));
    card.append(el('p',t('Quan hệ commit không chứng minh lỗi đã sửa. Tag hiện tại không chứng minh nội dung tag tại ngày công bố.'),'notice'));
    const details=el('details',undefined,'release-provenance');details.append(el('summary',t('Nguồn và giới hạn quan sát')),
      el('p',t('Phiên nguồn: ')+value.source.job_id,'hash-note'),
      el('p',t('Commit nguồn đã lưu: {sha}',{sha:value.source.commit_sha}),'hash-note'),
      el('p','Pack SHA256 '+value.source.pack_sha256,'hash-note'),
      el('p','Snapshot SHA256 '+value.source.snapshot_sha256,'hash-note'),
      el('p','Query SHA256 '+value.source.query_sha256,'hash-note'),
      el('p',t('Không gọi model, chạy test hoặc đánh giá benchmark trong lượt kiểm này.'),'notice'));
    if(Array.isArray(value.limitations))for(const limitation of value.limitations)details.append(el('p',t(limitation),'notice'));
    details.append(el('p','SHA256 '+value.sha256,'hash-note'));card.append(details);
    const download=el('button',t('Xuất quan sát phát hành JSON'));download.type='button';
    const savedVersion=version,savedBinding={...binding};
    download.onclick=()=>{
      if(savedVersion!==version||!same(savedBinding,binding)||!valid(value,binding))return;
      const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));
      const link=el('a');link.href=url;link.download='issueintel-release-observation.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };card.append(download);area.append(card);
  }
  function render(){
    get('releases-panel').hidden=!allowed;
    get('releases-message').textContent=inflight&&!same(inflight.binding,binding)?t('Một lượt kiểm của phiên trước vẫn đang hoàn tất; chờ trước khi tạo lượt mới.'):notice?t(notice):'';
    get('releases-submit').disabled=!allowed||!!inflight||opening;
    get('releases-tag').disabled=!!inflight;get('releases-reference').disabled=!!inflight;
    get('releases-cancel').hidden=!inflight||!inflight.id||!same(inflight.binding,binding);
    get('releases-cancel').disabled=!!inflight?.cancelling;
    const area=get('releases-result');area.replaceChildren();
    if(result&&valid(result,binding))renderResult(area,result);
    const history=get('releases-history');history.replaceChildren();
    if(!rows.length)history.append(el('p',t('Chưa có quan sát phát hành đã lưu cho phiên này.'),'field-note'));
    for(const row of rows){
      if(!identifier(row.id))continue;
      const button=el('button',t('Mở quan sát phát hành: {id} · {status}',{id:row.id,status:t(row.status)}),'history-row');button.type='button';button.disabled=row.status!=='completed'||opening||!!inflight;
      const expected={...binding},current=version;
      button.onclick=async()=>{
        if(current!==version||!same(expected,binding)||opening||inflight)return;
        opening=true;notice='Đang đọc quan sát đã lưu; không gọi GitHub hoặc model.';render();
        try{
          const value=await api('/api/jobs/'+row.id+'/releases.json');
          if(current!==version)return;
          if(!valid(value,expected))throw Error('Source mismatch');result=value;notice=null;
        }catch(_){if(current===version)notice='Không đọc được quan sát phát hành; kiểm tra nguồn và hash.';}
        finally{if(current===version){opening=false;render();}}
      };history.append(button);
    }
  }
  async function loadHistory(expected,current){
    try{const found=await api('/api/jobs/'+expected.job_id+'/releases');if(current!==version)return;
      if(!Array.isArray(found))throw Error('Invalid release history');rows=found;render();
    }catch(_){if(current===version){notice='Không đọc được quan sát phát hành; kiểm tra nguồn và hash.';render();}}
  }
  document.addEventListener('issueintel:packchange',event=>{
    const detail=event.detail||{},candidate={job_id:detail.job_id,pack_sha256:detail.intake?.source_pack_sha256,
      commit_sha:detail.pack?.snapshot?.commit_sha,snapshot_sha256:detail.pack?.snapshot?.snapshot_sha256,query_sha256:detail.intake?.query_sha256,
      repository:typeof detail.pack?.case?.repository==='string'?detail.pack.case.repository.toLowerCase():null};
    const available=detail.release_available===true&&identifier(candidate.job_id)&&sha(candidate.pack_sha256)&&commit(candidate.commit_sha)&&sha(candidate.snapshot_sha256)&&sha(candidate.query_sha256)
      &&typeof candidate.repository==='string'&&/^[a-z0-9_.-]+\/[a-z0-9_.-]+$/.test(candidate.repository);
    if(allowed===available&&same(candidate,binding)&&candidate.repository===binding.repository)return;
    binding=candidate;allowed=available;version++;rows=[];result=null;notice=null;opening=false;
    get('releases-tag').value='';get('releases-reference').value='';render();
    if(allowed)loadHistory({...binding},version);
  });
  document.addEventListener('issueintel:languagechange',render);
  get('releases-form').onsubmit=async event=>{
    event.preventDefault();if(!allowed||inflight||opening)return;
    const requestedTag=get('releases-tag').value.trim(),reference=get('releases-reference').value.trim();
    if(!tag(requestedTag)||!commit(reference)){notice='Nhập một tag hợp lệ và commit đầy đủ 40 hoặc 64 ký tự hex viết thường.';render();return;}
    const current=version,expected={...binding},attempt={binding:expected,id:null};inflight=attempt;
    notice='Đang đọc GitHub API cho tag đã chọn; không gọi model.';render();
    try{
      const job=await api('/api/releases',{job_id:expected.job_id,tag:requestedTag,reference_commit:reference});
      if(!identifier(job?.id))throw Error('Invalid release job');attempt.id=job.id;if(current===version)render();
      for(;;){
        const row=await api('/api/jobs/'+job.id);
        if(row.id!==job.id||!['queued','running','completed','failed','cancelled','interrupted'].includes(row.status))throw Error('Invalid release job');
        if(['completed','failed','cancelled','interrupted'].includes(row.status)){
          if(row.status==='completed'&&current===version){
            const value=await api('/api/jobs/'+job.id+'/releases.json');
            if(current===version){if(!valid(value,expected)||value.requested.tag!==requestedTag||value.requested.reference_commit!==reference)throw Error('Invalid release source');
              result=value;notice='Đã lưu quan sát phát hành; tình trạng bản sửa vẫn chưa xác minh.';}
          }else if(current===version)notice='Kiểm tra phát hành chưa hoàn tất; dữ liệu nguồn và receipt vẫn được giữ.';
          break;
        }
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    }catch(_){if(current===version)notice='Kiểm tra phát hành chưa hoàn tất; dữ liệu nguồn và receipt vẫn được giữ.';}
    finally{if(inflight===attempt)inflight=null;render();if(current===version&&allowed)await loadHistory(expected,current);}
  };
  get('releases-cancel').onclick=async()=>{
    const attempt=inflight;if(!attempt?.id||attempt.cancelling||!same(attempt.binding,binding))return;
    attempt.cancelling=true;render();
    try{await api('/api/jobs/'+attempt.id+'/cancel',{});}catch(_){if(same(attempt.binding,binding))notice='Chưa gửi được yêu cầu hủy; tác vụ có thể vẫn đang chạy.';}
    finally{if(inflight===attempt){attempt.cancelling=false;render();}}
  };
})();

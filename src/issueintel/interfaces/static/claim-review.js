/* Explicit claim critique over saved product sources. Never runs target code. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t,cache=new Map(),sha=x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x),job=x=>typeof x==='string'&&/^[a-f0-9]{24}$/.test(x);
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const statuses=['queued','running','completed','failed','interrupted','cancelled'];let active=null,timer=null;
  const kinds={source_fact:'Dữ kiện trong nguồn',mechanism:'Cơ chế được đề xuất',behavior_prediction:'Dự đoán hành vi',causal:'Nhận định nhân quả'};
  function cancel(){active=null;if(timer!==null){clearTimeout(timer);timer=null;}}
  const current=b=>active===b&&b.target.isConnected!==false;
  function valid(value,b){
    try{
      if(value?.schema!=='product_claim_review_v1'||!sha(value.sha256)||value.source?.schema!=='claim_review_source_v1'
        ||value.source.source_job_id!==b.id||value.source.source_pack_sha256!==b.report.source.pack_sha256||value.source.report_sha256!==b.report.sha256
        ||value.source.query_sha256!==b.report.source.query_sha256||value.source.snapshot_sha256!==b.report.source.snapshot_sha256
        ||value.source.commit_sha!==b.report.source.commit_sha||value.rubric?.version!=='claim_sufficiency_v1'
        ||value.causal_verified!==false||value.independent_human!==false||value.evaluation_performed!==false
        ||!Array.isArray(value.assessment)||value.assessment.length!==b.report.hypotheses.length||!Array.isArray(value.reviews))return false;
      const hypotheses=new Map(b.report.hypotheses.map(h=>[h.id,h])),evidence=new Map(b.pack.evidence.map(e=>[e.id,e])),seen=new Set(),characters=new Map();
      const span=(ref,text)=>{if(!characters.has(text))characters.set(text,Array.from(text));const chars=characters.get(text);
        return Number.isInteger(ref.start)&&Number.isInteger(ref.end)&&ref.start>=0&&ref.end>ref.start&&ref.end<=chars.length&&chars.slice(ref.start,ref.end).join('')===ref.quote;};
      const refs=values=>Array.isArray(values)&&values.every(ref=>{const e=evidence.get(ref.evidence_id);return e&&span(ref,e.text)&&e.sha256===ref.evidence_sha256&&sha(ref.quote_sha256)&&ref.locator===e.locator&&ref.revision===(e.revision??null);});
      for(const review of value.assessment){const h=hypotheses.get(review.hypothesis_id);
        if(!h||seen.has(h.id)||review.statement!==h.statement||review.semantic_support!=='unverified'||review.causal_verified!==false||review.independent_human!==false
          ||!Array.isArray(review.claims)||!Array.isArray(review.unreviewed_fragments))return false;seen.add(h.id);
        for(const c of review.claims){if(!span(c,h.statement)||!Object.hasOwn(kinds,c.kind)||!['supported','unsupported','contradicted','uncertain'].includes(c.assessment)||c.semantic_support!=='unverified'
          ||c.causal_verified!==false||c.independent_human!==false||!refs(c.support)||!refs(c.counter)||!Array.isArray(c.gaps)
          ||!Array.isArray(c.assumptions)||!c.assumptions.every(a=>refs(a.citations)))return false;}
      }
      return true;
    }catch(_){return false;}
  }
  function button(label,fn,b){const item=node('button',t(label));item.type='button';item.onclick=()=>{if(current(b)&&!item.disabled)return fn();};return item;}
  function changed(b){if(active?.entry===b.entry)paint(active);}
  function refs(values,b){const area=node('div',undefined,'claim-sources');
    for(const ref of values){const item=node('details');item.append(node('summary',ref.evidence_id+' · '+ref.locator+' · '+ref.start+'–'+ref.end),
      node('pre',ref.quote),node('p',(ref.revision||t('Không có revision code'))+' · SHA256 '+ref.quote_sha256,'hash-note'));
      item.append(button('Mở bằng chứng nguồn',()=>document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:b.id,evidence_id:ref.evidence_id}})),b));area.append(item);}
    if(!values.length)area.append(node('p',t('Chưa có đoạn nguồn chính xác trong phạm vi được cung cấp.'),'notice'));return area;
  }
  function paint(b){
    if(!current(b))return;const {entry,target}=b;target.replaceChildren();const area=node('section',undefined,'claim-review');target.append(area);
    area.append(node('h3',t('Kiểm chứng từng nhận định')),node('p',t('Nhận xét tự động, chưa xác minh ngữ nghĩa hoặc nguyên nhân. Nguồn đúng không có nghĩa nhận định đã đúng.'),'notice'),
      node('p',b.report.issue.repository+' · '+b.report.source.commit_sha,'hash-note'));
    const controls=node('div',undefined,'claim-controls'),load=button('Mở các bản kiểm nhận định đã lưu',()=>loadSaved(b),b);load.disabled=entry.busy;controls.append(load);
    const label=node('label',undefined,'checkbox'),consent=node('input');consent.type='checkbox';consent.checked=entry.consent===true;consent.disabled=entry.busy||!!entry.job||!b.available;
    consent.onchange=()=>{if(current(b)){entry.consent=consent.checked;paint(b);}};
    label.append(consent,node('span',t('Tôi đồng ý gửi issue, giả thuyết và đoạn code đã lưu tới Groq để kiểm nhận định, có chi phí API.')));
    const generate=button('Kiểm nhận định bằng Groq',()=>submit(b),b);generate.disabled=entry.busy||!!entry.job||!b.available||!entry.consent;
    controls.append(node('p',t('Một tác vụ cho mỗi pack nguồn, tối đa hai call kể cả sửa output. Không chạy code; đọc và xuất bản đã lưu không gọi model.'),'field-note'),label,generate);
    if(!b.available)controls.append(node('p',t('Cần điều tra product với ít nhất hai giả thuyết; benchmark không dùng kiểm nhận định này.'),'notice'));area.append(controls);
    if(entry.history){for(const row of entry.history){const open=button('Mở bản kiểm nhận định',()=>openJob(b,row.id),b);open.disabled=entry.busy;const line=node('div',undefined,'claim-job');line.append(open,node('span',row.id+' · '+t(row.status),'hash-note'));area.append(line);}}
    if(entry.job){area.append(node('p',entry.job.id+' · '+t(entry.job.status),'hash-note'));
      if(entry.job.accounting&&window.IssueIntelUsage){const usage=node('div');window.IssueIntelUsage.render(usage,entry.job.accounting);area.append(usage);}
      if(['queued','running'].includes(entry.job.status)){const stop=button('Hủy kiểm nhận định',()=>stopJob(b),b);area.append(stop);}
      if(['failed','interrupted','cancelled'].includes(entry.job.status))area.append(node('p',t('Tác vụ dừng; receipt được giữ. Mở lại tác vụ đã lưu, không tự tạo lượt trả phí thay thế.'),'notice'));
    }
    if(entry.bundle&&valid(entry.bundle,b)){
      const toolbar=node('div',undefined,'claim-controls');for(const type of ['json','md'])toolbar.append(button(type==='json'?'Xuất kiểm nhận định JSON':'Xuất kiểm nhận định Markdown',()=>download(b,type),b));area.append(toolbar);
      area.append(node('p','Rubric: '+entry.bundle.rubric.version+' · '+t('Vị trí trích dẫn tính theo Unicode code point, đầu gồm/cuối không gồm.'),'notice'));
      for(const review of entry.bundle.assessment){const section=node('article',undefined,'claim-hypothesis');section.append(node('h4',review.hypothesis_id+' · '+t(review.assessment)),node('p',review.statement,'claim-statement'),node('p',review.rationale,'notice'));
        if(!review.claims.length)section.append(node('p',t('Chưa tách được nhận định để kiểm. Không coi toàn bộ giả thuyết là đã được kiểm chứng.'),'notice'));
        for(const claim of review.claims){const card=node('article',undefined,'claim-card');card.append(node('h5',claim.id+' · '+t(kinds[claim.kind])),
          node('p',t('Model nêu')+': '+t(claim.model_assessment)+' · '+t('Sau cổng mức đủ bằng chứng')+': '+t(claim.assessment),'claim-assessment'),
          node('blockquote',claim.quote),node('p',claim.reason,'claim-reason'));
          const sources=node('div',undefined,'claim-source-columns');for(const [key,caption] of [['support','Đoạn nguồn hỗ trợ'],['counter','Đoạn nguồn phản bác']]){const group=node('div');group.append(node('h6',t(caption)),refs(claim[key],b));sources.append(group);}card.append(sources);
          const assumptions=node('details');assumptions.append(node('summary',t('Giả định cần kiểm tra')));
          for(const assumption of claim.assumptions)assumptions.append(node('p',assumption.condition+' · '+t(assumption.status),'claim-reason'),refs(assumption.citations,b));
          if(!claim.assumptions.length)assumptions.append(node('p',t('Model không ghi giả định; điều này chưa chứng minh không có giả định ẩn.'),'notice'));card.append(assumptions);
          const gaps=node('ul',undefined,'claim-gaps');for(const gap of claim.gaps){const item=node('li');item.append(node('code',gap.code),node('p',t(gap.reason)));gaps.append(item);}card.append(gaps);section.append(card);
        }
        if(review.unreviewed_fragments.length){const pending=node('details');pending.append(node('summary',t('Phần câu gốc chưa được kiểm nhận định')));for(const fragment of review.unreviewed_fragments)pending.append(node('blockquote',fragment.quote));section.append(pending);}area.append(section);
      }
      const provenance=node('details');provenance.append(node('summary',t('Nguồn và rubric kiểm nhận định')),node('pre',JSON.stringify({source:entry.bundle.source.sha256,report:entry.bundle.source.report_sha256,pack:entry.bundle.source.source_pack_sha256,review:entry.bundle.sha256,rubric:entry.bundle.rubric,provenance:entry.bundle.provenance,limitations:entry.bundle.limitations},null,2)));area.append(provenance);
    }
    if(entry.notice){const status=node('p',t(entry.notice),'notice');status.setAttribute('role','status');area.append(status);}
  }
  async function loadSaved(b){if(!current(b)||b.entry.busy)return;const e=b.entry;e.busy=true;e.notice=null;paint(b);
    try{const value=await api('/api/jobs/'+b.id+'/claim-reviews');if(value?.source_job_id!==b.id||value.source_pack_sha256!==b.report.source.pack_sha256||!Array.isArray(value.review_jobs)||typeof value.available!=='boolean'
      ||value.review_jobs.some(r=>!job(r.id)||!statuses.includes(r.status)||r.mode!=='claim_review'))throw Error('Invalid claim review history');e.history=value.review_jobs;b.available=value.available;if(active?.entry===e)active.available=value.available;}
    catch(_){e.notice='Chưa đọc được bản kiểm nhận định khớp nguồn. Không có lời gọi model mới.';}
    finally{e.busy=false;changed(b);}
  }
  async function poll(b,id){
    if(b.entry.polling)return;b.entry.polling=true;
    try{const row=await api('/api/jobs/'+id);if(!job(row?.id)||row.id!==id||row.mode!=='claim_review'||!statuses.includes(row.status))throw Error('Invalid claim review job');
      b.entry.job=row;if(row.status==='completed'){const bundle=await api('/api/jobs/'+id+'/claim-review.json');if(!valid(bundle,b))throw Error('Claim source changed');b.entry.bundle=bundle;b.entry.busy=false;changed(b);return;}
      if(['queued','running'].includes(row.status)){changed(b);const attached=active?.entry===b.entry?active:null;if(attached)timer=setTimeout(()=>{timer=null;if(current(attached))return poll(attached,id);},1000);else b.entry.busy=false;return;}
      b.entry.busy=false;changed(b);
    }catch(_){b.entry.busy=false;b.entry.notice='Chưa đọc được bản kiểm nhận định khớp nguồn. Không có lời gọi model mới.';changed(b);}
    finally{b.entry.polling=false;}
  }
  async function openJob(b,id){if(!current(b)||b.entry.busy||!job(id))return;b.entry.bundle=null;b.entry.busy=true;b.entry.consent=false;b.entry.notice=null;paint(b);await poll(b,id);}
  async function submit(b){const e=b.entry;if(!current(b)||e.busy||e.job||!e.consent||!b.available)return;e.busy=true;e.consent=false;e.notice=null;paint(b);
    try{const row=await api('/api/claim-reviews',{source_job_id:b.id,source_pack_sha256:b.report.source.pack_sha256,confirmation:'generate_claim_review_with_api_cost'});if(!job(row?.id))throw Error('Invalid submitted job');e.job=row;await poll(b,row.id);}
    catch(_){e.busy=false;e.notice='Chưa xác nhận tác vụ kiểm nhận định. Mở danh sách đã lưu trước khi thử lại; cùng nguồn chỉ trả tác vụ cũ.';changed(b);}
  }
  async function stopJob(b){if(!current(b)||!job(b.entry.job?.id))return;try{await api('/api/jobs/'+b.entry.job.id+'/cancel',{});b.entry.notice='Đã yêu cầu hủy; lời gọi đang gửi có thể vẫn tính phí.';}
    catch(_){b.entry.notice='Chưa gửi được yêu cầu hủy; mở lại tác vụ để kiểm tra trạng thái.';}finally{if(current(b))paint(b);}}
  async function download(b,type){if(!current(b)||!valid(b.entry.bundle,b))return;const value=b.entry.bundle,identifier=b.entry.job.id;
    try{const response=await fetch('/api/jobs/'+identifier+'/claim-review.'+type,{headers:{'X-IssueIntel-Token':document.querySelector('meta[name="issueintel-session"]').content}});
      if(!response.ok||response.headers.get('X-IssueIntel-Report-SHA256')!==b.report.sha256||response.headers.get('X-IssueIntel-Claim-Review-SHA256')!==value.sha256)throw Error('Unbound claim export');
      let blob;if(type==='json'){const result=await response.json();if(!valid(result,b)||result.sha256!==value.sha256)throw Error('Unbound claim JSON');blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'});}else blob=await response.blob();
      if(!current(b)||b.entry.bundle!==value)return;const url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download='issueintel-'+identifier+'-claim-review.'+type;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(_){if(current(b)){b.entry.notice='Chưa xuất được bản kiểm nhận định khớp nguồn đang xem.';paint(b);}}
  }
  function render(target,report,id,pack,options={}){
    cancel();if(!job(id)||!sha(report?.sha256)||report.source.job_id!==id||!sha(report.source.pack_sha256)||!pack?.snapshot
      ||pack.snapshot.commit_sha!==report.source.commit_sha||pack.snapshot.snapshot_sha256!==report.source.snapshot_sha256)return;
    const key=id+':'+report.source.pack_sha256+':'+report.sha256;if(!cache.has(key))cache.set(key,{busy:false,consent:false,job:null,bundle:null,history:null,notice:null});
    const b={target,report,id,pack,available:options.available===true&&report.hypotheses.length>=2,entry:cache.get(key)};active=b;paint(b);
    if(b.entry.busy&&b.entry.job&&['queued','running'].includes(b.entry.job.status))poll(b,b.entry.job.id);
  }
  document.addEventListener('issueintel:languagechange',()=>{for(const entry of cache.values())entry.consent=false;if(active)paint(active);});
  window.IssueIntelClaimReview={render,cancel,valid};
})();

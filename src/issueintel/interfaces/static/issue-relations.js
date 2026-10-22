/* Explicit, source-bound comparison of two different product issues. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t,cache=new Map(),sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),job=v=>typeof v==='string'&&/^[a-f0-9]{24}$/.test(v);
  const dimensions=['symptoms','mechanism','version','differences'],classifications=['related','possible_duplicate','insufficient'],statuses=['queued','running','completed','failed','interrupted','cancelled'];
  const judgmentsByDimension={symptoms:['similar','different','unknown'],mechanism:['similar','different','unknown'],version:['compatible','different','unknown'],differences:['non_material','material','unknown']};
  const captions={symptoms:'Triệu chứng',mechanism:'Cơ chế',version:'Phiên bản',differences:'Điểm khác biệt'};
  const verdicts={related:'Có liên quan',possible_duplicate:'Ứng viên có thể trùng',insufficient:'Chưa đủ dữ kiện'};
  const meanings={similar:'Tương tự',different:'Khác nhau',unknown:'Chưa biết',compatible:'Tương thích theo nguồn',non_material:'Không thấy khác biệt trọng yếu',material:'Khác biệt trọng yếu',
    demonstrated:'Đã nêu căn cứ',missing:'Thiếu căn cứ',addressed:'Đã giải thích',unresolved:'Chưa giải quyết',not_supplied:'Chưa có trong nguồn',source_supported:'Được nguồn hỗ trợ',unverified:'Chưa xác minh',
    code:'Code tĩnh',reported_mechanism:'Cơ chế được nguồn mô tả',reported:'Phiên bản do nguồn nêu'};
  let active=null,timer=null;
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const selected=b=>b.entry.pairs.get(b.entry.selected),current=b=>active===b&&b.target.isConnected!==false;
  function cancel(){if(active)for(const p of active.entry.pairs.values())p.consent=false;active=null;if(timer!==null){clearTimeout(timer);timer=null;}}
  function selector(value){
    if(value?.kind==='investigation'&&job(value.job_id)&&sha(value.pack_sha256)&&Object.keys(value).length===3)return {kind:value.kind,job_id:value.job_id,pack_sha256:value.pack_sha256};
    if(value?.kind==='synced_issue'&&typeof value.key==='string'&&value.key.length>0&&sha(value.record_sha256)&&sha(value.receipt_sha256)&&Object.keys(value).length===4)return {kind:value.kind,key:value.key,record_sha256:value.record_sha256,receipt_sha256:value.receipt_sha256};
    return null;
  }
  function sideValid(side){
    return side?.schema==='issue_relation_side_v1'&&['investigation','synced_issue'].includes(side.kind)&&sha(side.sha256)&&typeof side.repository==='string'&&side.repository.length>0
      &&typeof side.issue_key==='string'&&!!selector(side.reference)&&side.reference.kind===side.kind&&Array.isArray(side.sources)&&side.sources.length>0&&side.sources.length<=60
      &&new Set(side.sources.map(r=>r.id)).size===side.sources.length&&side.sources.some(r=>r.id===side.issue_source_id)
      &&side.sources.every(r=>typeof r.id==='string'&&typeof r.text==='string'&&sha(r.sha256)&&typeof r.kind==='string'&&typeof r.locator==='string'&&(r.revision===null||typeof r.revision==='string'));
  }
  function sourceValid(source,b){
    return source?.schema==='issue_relation_source_v1'&&sha(source.sha256)&&sideValid(source.left)&&sideValid(source.right)
      &&source.left.kind==='investigation'&&source.left.reference.job_id===b.id&&source.left.reference.pack_sha256===b.packSha
      &&source.left.commit_sha===b.pack.snapshot.commit_sha&&source.left.snapshot_sha256===b.pack.snapshot.snapshot_sha256
      &&source.left.repository===source.right.repository&&source.left.issue_key!==source.right.issue_key;
  }
  function references(refs,source,bound){
    return Array.isArray(refs)&&refs.every(ref=>{
      if(!['left','right'].includes(ref.side))return false;const row=source[ref.side].sources.find(r=>r.id===ref.source_id);
      if(!row||!Number.isInteger(ref.start)||!Number.isInteger(ref.end)||ref.start<0||ref.end<=ref.start)return false;
      const chars=Array.from(row.text);if(ref.end>chars.length||chars.slice(ref.start,ref.end).join('')!==ref.quote||!ref.quote.trim())return false;
      return !bound||ref.source_sha256===row.sha256&&sha(ref.quote_sha256)&&ref.locator===row.locator&&ref.revision===row.revision&&ref.kind===row.kind;
    });
  }
  function judgments(value,source,bound){
    return value&&classifications.includes(value.classification)&&typeof value.reason==='string'&&Array.isArray(value.dimensions)&&value.dimensions.length===4
      &&value.dimensions.every((d,i)=>d.name===dimensions[i]&&judgmentsByDimension[d.name].includes(d.judgment)
        &&typeof d.explanation==='string'&&references(d.support,source,bound)&&references(d.counter,source,bound)
        &&['demonstrated','unknown','missing'].includes(d.contextual_fit)&&['demonstrated','unknown','missing'].includes(d.inferential_link)&&['addressed','unresolved','not_supplied'].includes(d.counter_resolution))
      &&value.mechanism_basis&&['left','right'].every(s=>['code','reported_mechanism','unknown'].includes(value.mechanism_basis[s]))
      &&value.versions&&['left','right'].every(s=>['reported','unknown'].includes(value.versions[s]?.status)&&references(value.versions[s].citations,source,bound))
      &&Array.isArray(value.assumptions)&&value.assumptions.every(a=>typeof a.condition==='string'&&['source_supported','unverified'].includes(a.status)&&references(a.citations,source,bound))
      &&Array.isArray(value.unknowns)&&value.unknowns.every(v=>typeof v==='string');
  }
  function valid(bundle,b){
    try{return bundle?.schema==='product_issue_relation_v1'&&sha(bundle.sha256)&&sourceValid(bundle.source,b)&&judgments(bundle.comparison,bundle.source,false)&&judgments(bundle.assessment,bundle.source,true)
      &&bundle.assessment.model_classification===bundle.comparison.classification&&bundle.assessment.semantic_relation==='unverified'
      &&Array.isArray(bundle.assessment.gaps)&&bundle.assessment.gaps.every(g=>typeof g.code==='string'&&typeof g.reason==='string')
      &&['duplicate_verified','causal_verified','independent_human','evaluation_performed'].every(k=>bundle[k]===false)
      &&['duplicate_verified','causal_verified','independent_human'].every(k=>bundle.assessment[k]===false)&&typeof bundle.rubric?.version==='string';}catch(_){return false;}
  }
  const button=(label,action,b)=>{const e=node('button',t(label));e.type='button';e.onclick=()=>{if(current(b)&&!e.disabled)return action();};return e;};
  function changed(b){if(active?.entry===b.entry)paint(active);}
  const pairVisible=(b,pair)=>active?.entry===b.entry&&active.target.isConnected!==false&&selected(active)===pair;
  function pairChanged(b,pair){if(pairVisible(b,pair))paint(active);}
  function sourceCards(source){
    const columns=node('div',undefined,'relation-pair');
    for(const side of ['left','right']){const value=source[side],card=node('section',undefined,'relation-source');card.append(node('h4',t(side==='left'?'Issue đang xem':'Issue ứng viên')),node('p',value.issue_key,'relation-identity'),
      node('p',value.repository,'hash-note'),node('p',value.commit_sha||t('Không có snapshot code ở nguồn này'),'hash-note'));
      for(const row of value.sources){const detail=node('details');detail.append(node('summary',row.id+' · '+t(row.kind==='code'?'Code tĩnh':row.id===value.issue_source_id?'Mô tả issue':'Context repository hiện tại')+' · '+row.locator),node('pre',row.text),node('p','SHA256 '+row.sha256+' · '+(row.revision||t('Chưa biết')),'hash-note'));card.append(detail);}
      card.append(node('p','Source SHA256 '+value.sha256,'hash-note'));columns.append(card);}
    return columns;
  }
  function quotes(values,source){
    const area=node('div',undefined,'relation-quotes');
    for(const ref of values){const row=source[ref.side].sources.find(r=>r.id===ref.source_id),detail=node('details');detail.append(node('summary',t(ref.side==='left'?'Issue đang xem':'Issue ứng viên')+' · '+ref.source_id+' · '+row.locator+' · '+ref.start+'–'+ref.end),
      node('blockquote',ref.quote),node('p',t(row.kind==='code'?'Code tĩnh':'Nguồn báo cáo, chưa xác minh')+' · '+(ref.revision||t('Chưa biết')),'hash-note'),node('p','SHA256 '+ref.quote_sha256,'hash-note'));area.append(detail);}
    if(!values.length)area.append(node('p',t('Chưa có đoạn nguồn chính xác trong phạm vi được cung cấp.'),'notice'));return area;
  }
  function resultCard(bundle,b){
    const a=bundle.assessment,area=node('section',undefined,'relation-result');
    area.append(node('h3',t(verdicts[a.classification])),node('p',t('Model nêu')+': '+t(verdicts[a.model_classification])+' · '+t('Sau cổng mức đủ bằng chứng')+': '+t(verdicts[a.classification]),'relation-verdict'),
      node('p',a.reason),node('p',t('Nhận xét tự động, chưa xác nhận duplicate hoặc nguyên nhân. Cùng thông báo lỗi chưa đủ chứng minh cùng cơ chế.'),'notice'));
    for(const d of a.dimensions){const card=node('article',undefined,'relation-dimension');card.append(node('h4',t(captions[d.name])+' · '+t(meanings[d.judgment])),node('p',d.explanation),
      node('p',t('Áp dụng cho ngữ cảnh')+': '+t(meanings[d.contextual_fit])+' · '+t('Mắt xích suy luận')+': '+t(meanings[d.inferential_link])+' · '+t('Xử lý phản bác')+': '+t(meanings[d.counter_resolution]),'notice'));
      const columns=node('div',undefined,'relation-pair');for(const [key,label] of [['support','Đoạn nguồn hỗ trợ'],['counter','Đoạn nguồn phản bác']]){const group=node('section');group.append(node('h5',t(label)),quotes(d[key],bundle.source));columns.append(group);}card.append(columns);area.append(card);}
    const versions=node('div',undefined,'relation-pair');for(const side of ['left','right']){const v=a.versions[side],section=node('section',undefined,'relation-source');section.append(node('h4',t(side==='left'?'Phiên bản issue đang xem':'Phiên bản issue ứng viên')),
      node('p',v.status==='reported'?v.value:t('Chưa biết')),node('p',t('Căn cứ cơ chế')+': '+t(meanings[a.mechanism_basis[side]]),'notice'),quotes(v.citations,bundle.source));versions.append(section);}area.append(versions);
    const assumptions=node('section');assumptions.append(node('h4',t('Giả định cần kiểm tra')));for(const value of a.assumptions)assumptions.append(node('p',value.condition+' · '+t(meanings[value.status])),quotes(value.citations,bundle.source));
    if(!a.assumptions.length)assumptions.append(node('p',t('Model không ghi giả định; điều này chưa chứng minh không có giả định ẩn.'),'notice'));area.append(assumptions);
    const unknowns=node('section');unknowns.append(node('h4',t('Dữ kiện chưa biết và khoảng trống')));const list=node('ul');for(const value of a.unknowns)list.append(node('li',value));for(const gap of a.gaps){const item=node('li');item.append(node('code',gap.code),node('p',t(gap.reason)));list.append(item);}unknowns.append(list);
    if(!a.unknowns.length&&!a.gaps.length)unknowns.append(node('p',t('Chưa ghi khoảng trống; kết quả vẫn là nhận xét tự động chưa xác minh.'),'notice'));area.append(unknowns);
    const sources=node('details');sources.append(node('summary',t('Hai phía nguồn đã khóa')),sourceCards(bundle.source));area.append(sources);
    const metadata=node('details');metadata.append(node('summary',t('Nguồn và rubric đối chiếu')),node('pre',JSON.stringify({source:bundle.source.sha256,comparison:bundle.sha256,rubric:bundle.rubric,provenance:bundle.provenance,limitations:bundle.limitations},null,2)));area.append(metadata);return area;
  }
  function paint(b){
    if(!current(b))return;const e=b.entry;b.target.replaceChildren();const area=node('section',undefined,'issue-relations');b.target.append(area);
    area.append(node('h3',t('Đối chiếu hai issue')),node('p',t('Tìm ứng viên từ phiên local và issue GitHub đã đồng bộ. Chỉ gửi cặp nguồn đã xem trước khi bạn đồng ý chi phí.'),'notice'),
      node('p',t('Tìm trong nguồn đã lưu có giới hạn; không tự đồng bộ GitHub hoặc tải issue mới.'),'field-note'));
    if(!b.available){area.append(node('p',t('Đối chiếu này chỉ dành cho điều tra product; không dùng Main Dev hoặc nguồn strict.'),'notice'));return;}
    const form=node('form',undefined,'relation-search'),field=node('div'),id='relation-query-'+b.id,label=node('label',t('Từ khóa tìm issue ứng viên')),input=node('input');label.htmlFor=id;input.id=id;input.type='search';input.maxLength=2000;input.value=e.query;input.oninput=()=>{if(current(b))e.query=input.value;};input.disabled=e.searching;
    field.append(label,input);const search=button('Tìm ứng viên để đối chiếu',()=>searchCandidates(b),b);search.type='submit';search.disabled=e.searching||e.previewBusy;form.onsubmit=ev=>{ev.preventDefault();if(!e.searching&&!e.previewBusy)searchCandidates(b);};form.append(field,search);area.append(form);
    const history=button('Mở các bản đối chiếu đã lưu',()=>loadHistory(b),b);history.disabled=e.historyBusy;area.append(history);
    if(e.warnings?.includes('synced_issue_index_unavailable'))area.append(node('p',t('Chưa đọc được đầy đủ chỉ mục issue GitHub; danh sách ứng viên có thể thiếu nguồn đã đồng bộ.'),'notice'));
    if(e.searching)area.append(node('p',t('Đang tìm trong nguồn đã lưu; không gọi model.'),'notice'));
    if(e.candidates){const items=node('div',undefined,'relation-candidates');if(!e.candidates.length)items.append(node('p',t('Chưa tìm thấy ứng viên phù hợp trong phạm vi này.'),'notice'));
      for(const c of e.candidates){const card=node('article',undefined,'relation-candidate');card.append(node('h4',c.title),node('p',c.issue_key,'hash-note'),node('p',t(c.kind==='investigation'?'Phiên điều tra đã lưu':'Issue GitHub đã đồng bộ')+' · '+c.repository,'notice'),
        node('p',t('Ứng viên liên quan · chưa xác nhận duplicate'),'badge'));const displayedTurn=e.request,choose=button('Xem cặp nguồn',()=>{if(displayedTurn===e.request&&e.candidates.includes(c))return preview(b,c.selector);},b);choose.disabled=e.previewBusy||e.searching;card.append(choose);items.append(card);}area.append(items);}
    if(e.previewBusy)area.append(node('p',t('Đang khóa hai phía nguồn; không gọi model.'),'notice'));
    if(e.history){const list=node('section',undefined,'relation-history');list.append(node('h4',t('Các bản đối chiếu đã lưu')));if(!e.history.length)list.append(node('p',t('Chưa có bản đối chiếu cho phiên nguồn này.'),'notice'));
      for(const row of e.history){const line=node('div',undefined,'relation-saved-row');line.append(button('Mở bản đối chiếu',()=>openSaved(b,row.id),b),node('span',row.id+' · '+t(row.status),'hash-note'));list.append(line);}area.append(list);}
    const pair=selected(b);
    if(pair){const panel=node('section',undefined,'relation-selected');const title=node('h3',t('Cặp nguồn đang chọn'));title.setAttribute('tabindex','-1');panel.append(title);
      if(pair.preview){panel.append(sourceCards(pair.preview.source),node('p','Comparison source SHA256 '+pair.preview.comparison_source_sha256,'hash-note'),
        node('p',t('Một tác vụ cho mỗi cặp nguồn đã khóa, tối đa hai call kể cả sửa output. Lỗi hoặc hủy không tự tạo lượt trả phí thay thế.'),'field-note'));
        const controls=node('div',undefined,'relation-actions'),consentLabel=node('label',undefined,'checkbox'),consent=node('input');consent.type='checkbox';consent.checked=pair.consent===true;consent.disabled=pair.busy||!!pair.job||pair.ackUnknown;
        consent.onchange=()=>{if(current(b)&&selected(b)===pair&&consent.isConnected!==false&&!consent.disabled){pair.consent=consent.checked;generate.disabled=pair.busy||!!pair.job||!pair.consent||pair.ackUnknown;}};consentLabel.append(consent,node('span',t('Tôi đồng ý gửi mô tả hai issue và các đoạn nguồn đã khóa tới Groq để đối chiếu, có chi phí API.')));
        const generate=button('Đối chiếu cặp issue bằng Groq',()=>submit(b,pair),b);generate.disabled=pair.busy||!!pair.job||!pair.consent||pair.ackUnknown;controls.append(consentLabel,generate);panel.append(controls);}
      if(pair.job){panel.append(node('p',pair.job.id+' · '+t(pair.job.status),'hash-note'));if(pair.job.accounting&&window.IssueIntelUsage){const usage=node('div');window.IssueIntelUsage.render(usage,pair.job.accounting);panel.append(usage);}
        if(['queued','running'].includes(pair.job.status))panel.append(button('Hủy đối chiếu issue',()=>stop(b,pair),b));
        if(['failed','interrupted','cancelled'].includes(pair.job.status))panel.append(node('p',t('Đối chiếu đã dừng; receipt được giữ. Mở lại tác vụ đã lưu để xem, không tự thử API lại.'),'notice'));}
      if(pair.bundle&&valid(pair.bundle,b)){const exports=node('div',undefined,'relation-actions');for(const format of ['json','md'])exports.append(button(format==='json'?'Xuất đối chiếu issue JSON':'Xuất đối chiếu issue Markdown',()=>download(b,pair,format),b));panel.append(exports,resultCard(pair.bundle,b));}
      if(pair.notice){const note=node('p',t(pair.notice),'notice');note.setAttribute('role','status');note.setAttribute('aria-live','polite');panel.append(note);}
      area.append(panel);if(e.focusPreview){e.focusPreview=false;title.focus?.({preventScroll:true});}}
    if(e.notice){const note=node('p',t(e.notice),'notice');note.setAttribute('role','status');note.setAttribute('aria-live','polite');area.append(note);}
  }
  async function searchCandidates(b){
    const e=b.entry;if(!current(b)||e.searching||e.previewBusy)return;const turn=++e.request;e.searching=true;e.previewBusy=false;e.selected=null;e.candidates=null;e.warnings=[];e.notice=null;paint(b);
    try{const query=e.query.trim(),value=await api('/api/issue-relation-candidates',{source_job_id:b.id,source_pack_sha256:b.packSha,...(query?{query}:{})});
      if(!current(b)||turn!==e.request)return;if(value?.schema!=='issue_relation_candidates_v1'||value.source_job_id!==b.id||value.source_pack_sha256!==b.packSha||value.model_calls!==0||!Array.isArray(value.candidates)
        ||!value.candidates.every(c=>!!selector(c.selector)&&c.kind===c.selector.kind&&c.relationship==='candidate_unverified'&&typeof c.title==='string'&&typeof c.issue_key==='string'&&typeof c.repository==='string')
        ||!Array.isArray(value.warnings)||!value.warnings.every(w=>typeof w==='string'))throw Error('Unbound candidate search');e.candidates=value.candidates;e.warnings=value.warnings;
    }catch(_){if(current(b)&&turn===e.request)e.notice='Chưa tìm được ứng viên khớp nguồn đang xem. Không có lời gọi model mới.';}
    finally{if(turn===e.request){e.searching=false;changed(b);}}
  }
  async function preview(b,candidate){
    const e=b.entry;if(!current(b)||e.searching||e.previewBusy||!selector(candidate))return;const turn=++e.request;e.previewBusy=true;e.selected=null;e.notice=null;paint(b);
    try{const value=await api('/api/issue-relations/preview',{source_job_id:b.id,source_pack_sha256:b.packSha,candidate:selector(candidate)});
      if(!current(b)||turn!==e.request)return;if(value?.schema!=='issue_relation_preview_v1'||value.source_job_id!==b.id||value.source_pack_sha256!==b.packSha||value.available!==true||value.model_calls!==0||value.max_provider_calls!==2||!sha(value.policy_sha256)
        ||value.confirmation!=='compare_issue_pair_with_api_cost'||!equal(selector(value.candidate),selector(candidate))||!sourceValid(value.source,b)||value.comparison_source_sha256!==value.source.sha256)throw Error('Unbound comparison preview');
      const key=value.comparison_source_sha256;if(!e.pairs.has(key))e.pairs.set(key,{preview:value,consent:false,busy:false,job:null,bundle:null,ackUnknown:false,polling:false});else e.pairs.get(key).preview=value;
      e.selected=key;selected(b).consent=false;e.focusPreview=true;
    }catch(_){if(current(b)&&turn===e.request)e.notice='Chưa khóa được đúng cặp nguồn. Tìm và xem trước lại; không có lời gọi model mới.';}
    finally{if(turn===e.request){e.previewBusy=false;changed(b);const pair=selected(b);if(current(b)&&pair?.job&&['queued','running'].includes(pair.job.status))poll(b,pair);}}
  }
  async function loadHistory(b){
    const e=b.entry;if(!current(b)||e.historyBusy)return;e.historyBusy=true;e.notice=null;paint(b);
    try{const value=await api('/api/jobs/'+b.id+'/issue-relations');if(!current(b))return;
      if(value?.schema!=='issue_relation_history_v1'||value.source_job_id!==b.id||value.source_pack_sha256!==b.packSha||!Array.isArray(value.relation_jobs)||!value.relation_jobs.every(r=>job(r.id)&&r.mode==='issue_relation'&&statuses.includes(r.status)))throw Error('Unbound comparison history');e.history=value.relation_jobs;
    }catch(_){if(current(b))e.notice='Chưa đọc được đối chiếu khớp nguồn đang xem. Không có lời gọi model mới.';}
    finally{e.historyBusy=false;changed(b);}
  }
  async function poll(b,pair){
    if(pair.polling)return;pair.polling=true;
    try{const row=await api('/api/jobs/'+pair.job.id);if(row?.id!==pair.job.id||row.mode!=='issue_relation'||!statuses.includes(row.status))throw Error('Unbound comparison job');pair.job=row;
      if(row.status==='completed'){const bundle=await api('/api/jobs/'+row.id+'/issue-relation.json');if(!valid(bundle,b)||pair.preview&&bundle.source.sha256!==pair.preview.comparison_source_sha256)throw Error('Unbound saved pair');pair.bundle=bundle;pair.busy=false;pairChanged(b,pair);return;}
      if(['queued','running'].includes(row.status)){pairChanged(b,pair);const attached=pairVisible(b,pair)?active:null;if(attached)timer=setTimeout(()=>{timer=null;if(current(attached)&&selected(attached)===pair)return poll(attached,pair);},1000);else pair.busy=false;return;}
      pair.busy=false;pairChanged(b,pair);
    }catch(_){pair.busy=false;pair.notice='Chưa đọc được đối chiếu khớp nguồn đang xem. Không có lời gọi model mới.';pairChanged(b,pair);}
    finally{pair.polling=false;}
  }
  async function openSaved(b,identifier){if(!current(b)||!job(identifier))return;const key='saved:'+identifier,e=b.entry;++e.request;e.searching=false;e.previewBusy=false;if(!e.pairs.has(key))e.pairs.set(key,{job:{id:identifier,status:'queued'},bundle:null,preview:null,busy:true,polling:false,consent:false});const pair=e.pairs.get(key);pair.notice=null;pair.bundle=null;pair.busy=true;e.selected=key;e.notice=null;paint(b);await poll(b,pair);}
  async function submit(b,pair){
    if(!current(b)||selected(b)!==pair||!pair.preview||pair.busy||pair.job||!pair.consent||pair.ackUnknown)return;pair.busy=true;pair.consent=false;pair.notice=null;b.entry.notice=null;paint(b);const p=pair.preview;
    try{const row=await api('/api/issue-relations',{source_job_id:b.id,source_pack_sha256:b.packSha,candidate:p.candidate,comparison_source_sha256:p.comparison_source_sha256,policy_sha256:p.policy_sha256,confirmation:'compare_issue_pair_with_api_cost'});
      if(!job(row?.id)||row.mode!=='issue_relation'||!statuses.includes(row.status))throw Error('Unbound comparison submit');pair.job=row;await poll(b,pair);
    }catch(_){pair.busy=false;pair.ackUnknown=true;pair.notice='Chưa xác nhận tác vụ đối chiếu. Mở danh sách đã lưu trước khi thao tác tiếp; không tự tạo lượt API thay thế.';pairChanged(b,pair);}
  }
  async function stop(b,pair){if(!current(b)||selected(b)!==pair||!job(pair.job?.id))return;try{await api('/api/jobs/'+pair.job.id+'/cancel',{});pair.notice='Đã yêu cầu hủy; lời gọi đang gửi có thể vẫn tính phí.';}catch(_){pair.notice='Chưa gửi được yêu cầu hủy; mở lại tác vụ để kiểm tra trạng thái.';}finally{pairChanged(b,pair);}}
  async function download(b,pair,format){
    if(!current(b)||selected(b)!==pair||!valid(pair.bundle,b))return;const value=pair.bundle,identifier=pair.job.id;
    try{const response=await fetch('/api/jobs/'+identifier+'/issue-relation.'+format,{headers:{'X-IssueIntel-Token':document.querySelector('meta[name="issueintel-session"]').content}});
      if(!response.ok||response.headers.get('X-IssueIntel-Source-Pack-SHA256')!==b.packSha||response.headers.get('X-IssueIntel-Issue-Relation-SHA256')!==value.sha256)throw Error('Unbound relation export');
      let blob;if(format==='json'){const result=await response.json();if(!valid(result,b)||result.sha256!==value.sha256)throw Error('Changed comparison export');blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'});}else blob=await response.blob();
      if(!current(b)||selected(b)!==pair||pair.bundle!==value)return;const url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download='issueintel-'+identifier+'-issue-relation.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(_){pair.notice='Chưa xuất được đối chiếu đúng cặp nguồn đang xem.';pairChanged(b,pair);}
  }
  function render(target,report,identifier,pack,options={}){
    cancel();if(!job(identifier)||!sha(report?.source?.pack_sha256)||report.source.job_id!==identifier||!pack?.snapshot||report.source.commit_sha!==pack.snapshot.commit_sha||report.source.snapshot_sha256!==pack.snapshot.snapshot_sha256){target.replaceChildren(node('p',t('Chưa đọc được metadata nguồn khớp báo cáo để đối chiếu. Mở lại phiên đã lưu; không có lời gọi model mới.'),'notice'));return;}
    const key=identifier+':'+report.source.pack_sha256;if(!cache.has(key))cache.set(key,{query:'',pairs:new Map(),selected:null,candidates:null,history:null,searching:false,previewBusy:false,historyBusy:false,request:0,notice:null});
    const b={target,id:identifier,pack,packSha:report.source.pack_sha256,available:options.available===true,entry:cache.get(key)};active=b;paint(b);const pair=selected(b);if(pair?.job&&['queued','running'].includes(pair.job.status))poll(b,pair);
  }
  document.addEventListener('issueintel:languagechange',()=>{for(const e of cache.values())for(const p of e.pairs.values())p.consent=false;if(active)paint(active);});
  window.IssueIntelIssueRelations={render,cancel,valid,sourceValid};
})();

/* Read the server's captured accounting projection; never sum receipts in the client. */
(() => {
  const t=window.IssueIntelUI.t,cache=new Map(),bindings=new Map();
  const providers=['groq','typesafe'],sha=v=>/^[a-f0-9]{64}$/.test(v||''),job=v=>/^[a-f0-9]{24}$/.test(v||'');
  const gapLabels={
    tracking_unavailable:'Phiên này chưa ghi receipt đầy đủ; chi phí chưa biết.',
    attempt_count_unknown:'Chưa biết số lượt đã thử nên chưa xác nhận đủ receipt.',
    attempt_receipt_count_mismatch:'Số lượt đã thử không khớp số receipt ghi được.',
    invalid_receipt:'Có receipt không hợp lệ; phần chi phí này chưa được cộng vào tổng đã biết.',
    receipt_identity_conflict:'Các bản ghi cùng reservation mâu thuẫn; không tự chọn một số tiền.',
    missing_parent_binding:'Thiếu liên kết nguồn của phiên trước; tổng cả chuỗi có thể thiếu.',
    lineage_source_unavailable:'Chưa đọc được nguồn của một phiên trong chuỗi điều tra.',
    sidecar_source_unavailable:'Chưa đọc được nguồn của một tác vụ phụ liên quan.',
    sidecar_registry_unavailable:'Chưa đọc được danh sách tác vụ phụ đã gắn nguồn.',
    source_pack_unavailable:'Pack nguồn chưa có hoặc chưa kiểm được; phạm vi chi phí chưa đầy đủ.',
    attempt_inventory_invalid:'Danh sách lượt thử không hợp lệ; chưa xác nhận được tổng chi phí.',
    receipt_usage_breach:'Usage vượt cận đã ghi; cần kiểm lại ledger chi phí.'
  };
  const natural=v=>Number.isSafeInteger(v)&&v>=0;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const session=()=>document.querySelector('meta[name="issueintel-session"]').content;
  const current=b=>bindings.get(b.target)===b&&b.target.isConnected!==false&&session()===b.session;
  const money=v=>natural(v)?'$'+Math.floor(v/1000000)+'.'+String(v%1000000).padStart(6,'0'):t('Chưa biết');
  const integerFields=['captured_receipt_records','reservations','duplicate_captures','conflicting_reservations','invalid_receipts','retained_bounds','usage_anomalies','unsettled_reservations','known_micro_usd_charged_or_reserved'];
  function summaryValid(v,provider){
    return v&&v.provider===provider&&['applicable','tracking_available','inventory_complete','all_usage_settled'].every(k=>typeof v[k]==='boolean')
      &&Array.isArray(v.owner_job_ids)&&v.owner_job_ids.every(job)&&integerFields.every(k=>natural(v[k]))
      &&(v.attempted_calls===null||natural(v.attempted_calls))&&Array.isArray(v.gap_codes)&&v.gap_codes.every(k=>typeof k==='string')
      &&(v.inventory_complete?v.complete_micro_usd_charged_or_reserved===v.known_micro_usd_charged_or_reserved:v.complete_micro_usd_charged_or_reserved===null);
  }
  function scopeValid(v){return v&&Array.isArray(v.job_ids)&&v.job_ids.every(job)&&providers.every(p=>summaryValid(v.providers?.[p],p))
    &&typeof v.inventory_complete==='boolean'&&typeof v.all_usage_settled==='boolean'&&natural(v.known_micro_usd_charged_or_reserved)
    &&(v.inventory_complete?v.complete_micro_usd_charged_or_reserved===v.known_micro_usd_charged_or_reserved:v.complete_micro_usd_charged_or_reserved===null);}
  function valid(value,b){
    try{return value?.schema==='investigation_usage_v1'&&sha(value.sha256)&&value.source?.selected_job_id===b.id
      &&(value.source.selected_pack_sha256===null||sha(value.source.selected_pack_sha256))&&(!b.packSha||value.source.selected_pack_sha256===b.packSha)
      &&sha(value.source.snapshot_sha256)&&value.snapshot?.sha256===value.source.snapshot_sha256
      &&value.currency==='USD'&&value.captured_receipts_only===true&&value.is_provider_invoice===false&&value.absence_of_uncaptured_calls_verified===false
      &&value.consistency_basis==='declared_captured_local_accounting'&&scopeValid(value.current)&&scopeValid(value.lineage)
      &&value.current.job_ids.length===1&&value.current.job_ids[0]===b.id&&value.lineage.job_ids.includes(b.id)
      &&Array.isArray(value.owners)&&value.owners.every(o=>job(o.job_id)&&typeof o.mode==='string'&&typeof o.job_status==='string'
        &&sha(o.request_sha256)&&sha(o.job_sha256)&&(o.pack_sha256===null||sha(o.pack_sha256))&&providers.every(p=>summaryValid(o.providers?.[p],p)))
      &&Array.isArray(value.gaps)&&value.gaps.every(g=>typeof g.code==='string'&&typeof g.detail==='string'&&(g.job_id===null||job(g.job_id))&&(g.provider===null||providers.includes(g.provider)))
      &&Array.isArray(value.limitations)&&value.limitations.every(l=>typeof l==='string');}catch(_){return false;}
  }
  function button(label,action,b){const n=node('button',t(label));n.type='button';n.setAttribute('data-usage-action',label);n.onclick=()=>{if(current(b)&&!n.disabled)return action();};return n;}
  function paintEntry(entry){for(const b of bindings.values())if(b.entry===entry&&current(b))paint(b);}
  function field(list,label,value){const row=node('div');row.append(node('dt',t(label)),node('dd',value));list.append(row);}
  function scopeCard(scope,label){
    const card=node('section',undefined,'investigation-usage-scope');card.append(node('h4',t(label)));
    const total=node('dl',undefined,'investigation-usage-totals');
    field(total,'Tổng trong phạm vi đã ghi',money(scope.complete_micro_usd_charged_or_reserved));
    field(total,'Phần chi phí đã biết',money(scope.known_micro_usd_charged_or_reserved));card.append(total);
    card.append(node('p',t(scope.inventory_complete?'Đủ bản ghi trong phạm vi cục bộ đã khai báo.':'Chưa đủ bản ghi; phần đã biết không phải tổng đầy đủ.'),'notice'),
      node('p',t(scope.all_usage_settled?'Các reservation đã ghi đều đã quyết toán usage.':'Còn cận giữ chỗ hoặc usage chưa quyết toán.'),'notice'));
    for(const provider of providers){
      const v=scope.providers[provider],section=node('section',undefined,'investigation-usage-provider');
      section.append(node('h5',provider==='groq'?'Groq':'Jev / Typesafe'));
      const amounts=node('dl',undefined,'investigation-usage-provider-values');
      field(amounts,'Tổng provider trong phạm vi',money(v.complete_micro_usd_charged_or_reserved));field(amounts,'Phần chi phí đã biết',money(v.known_micro_usd_charged_or_reserved));
      field(amounts,'Lượt thử đã khai báo',v.attempted_calls===null?t('Chưa biết'):String(v.attempted_calls));
      field(amounts,'Reservation hợp lệ',String(v.reservations));field(amounts,'Còn giữ cận trên',String(v.retained_bounds));
      field(amounts,'Chưa quyết toán usage',String(v.unsettled_reservations));section.append(amounts);
      if(!v.applicable)section.append(node('p',t('Provider không áp dụng trong phạm vi đã khai báo.'),'notice'));
      if(!v.inventory_complete)section.append(node('p',t('Chi phí provider chưa đầy đủ; không coi phần thiếu là 0.'),'notice'));
      if(v.conflicting_reservations||v.invalid_receipts||v.usage_anomalies)section.append(node('p',t('Có bản ghi chi phí bất nhất; xem khoảng trống trước khi diễn giải tổng.'),'notice'));
      const detail=node('details');detail.append(node('summary',t('Bản ghi và phiên sở hữu')),node('pre',JSON.stringify({owner_job_ids:v.owner_job_ids,captured_receipt_records:v.captured_receipt_records,duplicate_captures:v.duplicate_captures,conflicting_reservations:v.conflicting_reservations,invalid_receipts:v.invalid_receipts,usage_anomalies:v.usage_anomalies,gap_codes:v.gap_codes},null,2)));section.append(detail);card.append(section);
    }
    const jobs=node('details');jobs.append(node('summary',t('Các phiên trong phạm vi')));const list=node('ul');for(const id of scope.job_ids)list.append(node('li',id));jobs.append(list);card.append(jobs);return card;
  }
  function paint(b){
    if(!current(b))return;const focused=document.activeElement,focusAction=b.target.contains?.(focused)?focused?.getAttribute?.('data-usage-action'):null;
    const e=b.entry,area=node('section',undefined,'investigation-usage');b.target.replaceChildren(area);
    area.append(node('h3',t('Chi phí chuỗi điều tra')),node('p',t('Chi phí của phiên: {id}',{id:b.id}),'hash-note'),
      node('p',t('Chỉ đọc receipt cục bộ đã ghi; làm mới và xuất không gọi model hoặc đồng bộ provider.'),'notice'));
    const actions=node('div',undefined,'investigation-usage-actions'),refresh=button('Làm mới chi phí đã ghi',()=>{if(!e.loading)return load(b,true);},b);refresh.setAttribute('aria-disabled',String(e.loading));actions.append(refresh);
    if(e.value&&valid(e.value,b))for(const format of ['json','md'])actions.append(button(format==='json'?'Xuất chi phí chuỗi JSON':'Xuất chi phí chuỗi Markdown',()=>download(b,format),b));area.append(actions);
    if(focusAction)Array.from(actions.children).find(n=>n.getAttribute('data-usage-action')===focusAction)?.focus?.({preventScroll:true});
    const note=node('p',e.loading?t('Đang đọc snapshot chi phí cục bộ…'):e.error?t(e.error):'',e.error?'error':'notice');note.setAttribute('role','status');note.setAttribute('aria-live','polite');area.append(note);
    if(!e.value||!valid(e.value,b))return;const v=e.value;
    area.append(node('p',t('Ảnh chụp kế toán cục bộ; không phải hóa đơn provider và không chứng minh không có call ngoài bản ghi.'),'notice'));
    const scopes=node('div',undefined,'investigation-usage-scopes');scopes.append(scopeCard(v.current,'Phiên hiện tại'),scopeCard(v.lineage,'Toàn chuỗi điều tra'));area.append(scopes);
    area.append(node('p',t('Chuỗi chỉ gồm các phiên và tác vụ phụ được server gắn nguồn. Không cộng các nhánh khác hoặc chi phí của issue ứng viên.'),'notice'));
    const gaps=node('details',undefined,'investigation-usage-gaps');gaps.open=v.gaps.length>0;gaps.append(node('summary',t('Khoảng trống kế toán: {count}',{count:v.gaps.length})));
    const list=node('ul');for(const g of v.gaps){const friendly=Object.hasOwn(gapLabels,g.code),row=node('li');row.append(node('code',g.code),node('p',friendly?t(gapLabels[g.code]):g.detail),node('p',[g.job_id,g.provider].filter(Boolean).join(' · '),'hash-note'));
      if(friendly){const detail=node('details');detail.append(node('summary',t('Chi tiết kỹ thuật gốc')),node('p',g.detail));row.append(detail);}list.append(row);}gaps.append(list);area.append(gaps);
    const provenance=node('details');provenance.append(node('summary',t('Phạm vi, phiên sở hữu và hash đã ghi')),node('p','Accounting SHA256 '+v.sha256,'hash-note'),node('p','Snapshot SHA256 '+v.source.snapshot_sha256,'hash-note'),
      node('p','Pack SHA256 '+(v.source.selected_pack_sha256||t('Chưa có pack hoàn tất')),'hash-note'),node('pre',JSON.stringify({source:v.source,owners:v.owners,snapshot:v.snapshot,limitations:v.limitations},null,2)));area.append(provenance);
  }
  async function load(b,force=false){
    if(!current(b))return;const e=b.entry;if(e.loading){if(force)e.refreshRequested=true;return e.pending;}if(e.value&&!force)return;
    const turn=++e.turn;e.loading=true;e.error=null;paintEntry(e);
    e.pending=(async()=>{try{const value=await api('/api/jobs/'+b.id+'/investigation-usage');if(session()!==b.session||turn!==e.turn)return;
      if(!valid(value,b))throw Error('Unbound accounting snapshot');e.value=value;
    }catch(_){if(session()===b.session&&turn===e.turn)e.error='Chưa đọc được snapshot chi phí đúng phiên. Chi phí thiếu vẫn là chưa biết.';}
    finally{if(turn===e.turn){e.loading=false;paintEntry(e);
      if(e.refreshRequested){e.refreshRequested=false;const next=Array.from(bindings.values()).find(value=>value.entry===e&&current(value));if(next)return load(next,true);}
    }}})();return e.pending;
  }
  async function download(b,format){
    if(!current(b)||!valid(b.entry.value,b))return;const value=b.entry.value;
    try{const response=await fetch('/api/jobs/'+b.id+'/investigation-usage.'+format,{headers:{'X-IssueIntel-Token':b.session}});
      if(!response.ok||response.headers.get('X-IssueIntel-Accounting-SHA256')!==value.sha256||(response.headers.get('X-IssueIntel-Source-Pack-SHA256')||null)!==value.source.selected_pack_sha256)throw Error('Accounting export changed');
      let blob;if(format==='json'){const result=await response.json();if(!valid(result,b)||result.sha256!==value.sha256||result.source.snapshot_sha256!==value.source.snapshot_sha256)throw Error('Accounting export binding changed');blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'});}else blob=await response.blob();
      if(!current(b)||b.entry.value!==value)return;const url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download='issueintel-'+b.id+'-investigation-usage.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(_){if(current(b)&&b.entry.value===value){b.entry.error='Chưa xuất được đúng snapshot chi phí. Làm mới trước khi xuất lại.';paintEntry(b.entry);}}
  }
  function cancel(target){if(target){for(const bound of bindings.keys())if(bound===target||target.contains?.(bound))bindings.delete(bound);}else bindings.clear();}
  function render(target,identifier,options={}){
    cancel(target);const packSha=options.packSha||null;
    if(!job(identifier)||(packSha!==null&&!sha(packSha))){target.replaceChildren(node('p',t('Chưa có định danh phiên hợp lệ để đọc chi phí.'),'notice'));return;}
    const token=session(),key=identifier+':'+(packSha||'')+':'+token;
    if(!cache.has(key))cache.set(key,{value:null,loading:false,error:null,turn:0,pending:null,refreshRequested:false,revision:options.revision});
    const e=cache.get(key),changed=options.revision!==undefined&&e.revision!==options.revision;if(options.revision!==undefined)e.revision=options.revision;
    const b={target,id:identifier,packSha,session:token,entry:e};bindings.set(target,b);paint(b);load(b,changed);
  }
  document.addEventListener('issueintel:languagechange',()=>{for(const b of bindings.values())if(current(b))paint(b);});
  window.IssueIntelInvestigationUsage={render,cancel,valid};
})();

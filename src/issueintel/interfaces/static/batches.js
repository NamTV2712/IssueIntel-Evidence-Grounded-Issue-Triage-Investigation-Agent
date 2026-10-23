/* Product batches keep durable execution identity separate from the viewed report. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t,get=id=>document.getElementById(id);
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),id=v=>typeof v==='string'&&/^[a-f0-9]{24}$/.test(v);
  const key=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(v);
  const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
  const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
  const itemStates=['pending','queued','running','completed','failed','cancelled','interrupted'];
  const batchStates=[...itemStates.filter(s=>s!=='pending'),'paused','partial_success'];
  const labels={pending:'Chưa đưa vào hàng đợi',queued:'Đã vào hàng đợi',running:'running',completed:'completed',failed:'failed',cancelled:'cancelled',interrupted:'interrupted',paused:'Đã tạm dừng',partial_success:'Hoàn tất một phần'};
  let preview=null,previewText=null,selected=null,historyRows=[],historyErrors=[],draftVersion=0,view=0,historyVersion=0;
  let previewBusy=false,posting=false,reading=false,actionBusy=false,historyBusy=false,consent=false,continueConsent=false;
  let pendingCreate=null,actionUnknown=false,notice=null,timer=null,historyLoaded=false;
  const acceptedPreviews=new Set();
  const approvedPreviewOpen=new Map();
  const normalManifest=value=>({...value,items:value.items.map(row=>{const request={...row.request,mode:row.request.mode||'local',provider:row.request.provider||'none',depth:row.request.depth||'quick'};
    delete request.controller;if(request.mode==='local'){request.repository=request.repository.trim();request.revision=request.revision.trim();}
    if(request.mode==='github'){const path=new URL(request.issue_url).pathname.split('/');request.issue_url='https://github.com/'+path[1].toLowerCase()+'/'+path[2].toLowerCase()+'/issues/'+Number(path[4]);request.revision=(request.revision||'HEAD').trim();}
    return {...row,request};})});
  function validPreview(value,manifest){
    try{return value?.schema==='product_batch_preview_v1'&&hash(value.sha256)&&hash(value.manifest_sha256)&&hash(value.policy_sha256)
      &&same(value.manifest,normalManifest(manifest))&&Array.isArray(value.items)&&value.items.length===manifest.items.length&&value.items.length>=1&&value.items.length<=20
      &&value.items.every((row,i)=>row.key===manifest.items[i].key&&hash(row.request_sha256)&&Number.isInteger(row.call_upper_bound)&&row.call_upper_bound>=0&&row.call_upper_bound<=9
        &&(row.error===null?hash(row.source_sha256)&&row.source?.sha256===row.source_sha256&&row.resolved_request&&['local','github','example'].includes(row.resolved_request.mode)
          &&['none','groq'].includes(row.resolved_request.provider)&&['quick','deep','planned'].includes(row.resolved_request.depth)
          &&/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(row.source.revision||'')&&typeof row.source.case?.repository==='string'&&typeof row.source.case.problem_statement==='string'
          &&same(row.resolved_request,{...value.manifest.items[i].request,...(row.resolved_request.mode==='example'?{}:{revision:row.source.revision})})
          :row.source===null&&row.resolved_request===null&&row.source_sha256===null&&typeof row.error?.code==='string'&&typeof row.error.message==='string'))
      &&value.can_start===value.items.every(row=>row.error===null)&&value.requires_api===value.manifest.items.some(row=>row.request.provider==='groq')
      &&value.currency==='USD'&&value.cost_bound_micro_usd===(value.requires_api?null:0)&&value.concurrency===1&&value.model_calls===0;
    }catch(_){return false;}
  }
  function validRead(value,expected=null){
    try{
      if(value?.schema!=='product_batch_v1'||!id(value.id)||!key(value.run_key)||!hash(value.sha256)
        ||!['manifest_sha256','preview_sha256','policy_sha256'].every(k=>hash(value[k]))||typeof value.requires_api!=='boolean'
        ||!batchStates.includes(value.status)||!['running','paused','cancelled'].includes(value.desired_state)||value.concurrency!==1||value.model_calls!==0
        ||!Array.isArray(value.items)||value.items.length<1||value.items.length>20||value.counts?.total!==value.items.length
        ||!Number.isFinite(Date.parse(value.created_at))||!Number.isFinite(Date.parse(value.updated_at)))return false;
      if(expected&&['id','run_key','manifest_sha256','preview_sha256','policy_sha256'].some(k=>expected[k]!==undefined&&value[k]!==expected[k]))return false;
      if(expected?.items&&(!same(value.items.map(row=>['key','job_id','request_sha256','source_sha256','mode','provider','depth','revision'].map(k=>row[k])),
        expected.items.map(row=>['key','job_id','request_sha256','source_sha256','mode','provider','depth','revision'].map(k=>row[k])))))return false;
      const counts=Object.fromEntries(itemStates.map(k=>[k,0])),seen=new Set();
      for(const row of value.items){if(!key(row.key)||row.key.length>64||seen.has(row.key)||!(row.job_id===null||id(row.job_id))
        ||typeof row.job_available!=='boolean'||(row.job_available&&!id(row.job_id))||!hash(row.request_sha256)||!hash(row.source_sha256)||!['local','github','example'].includes(row.mode)
        ||!['none','groq'].includes(row.provider)||!['quick','deep','planned'].includes(row.depth)||!itemStates.includes(row.status)
        ||typeof row.stage!=='string'||!(row.error===null||typeof row.error==='string')||!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(row.revision||''))return false;
        seen.add(row.key);counts[row.status]++;}
      return value.requires_api===value.items.some(row=>row.provider==='groq')&&itemStates.every(k=>value.counts[k]===counts[k]);
    }catch(_){return false;}
  }
  const binding=row=>({manifest_sha256:row.manifest_sha256,preview_sha256:row.preview_sha256});
  const active=row=>row&&(row.counts.running+row.counts.queued>0||(row.desired_state==='running'&&row.counts.pending>0));
  function stopPolling(){if(timer!==null)clearTimeout(timer);timer=null;}
  function say(message){notice=message;get('batch-message').textContent=message?t(message):'';}
  function button(caption,identifier,action,disabled=false){const n=node('button',t(caption));n.type='button';if(identifier)n.id=identifier;n.disabled=disabled;n.onclick=()=>{if(!n.disabled)return action();};return n;}
  function controls(){const locked=posting||pendingCreate!==null;
    get('batch-manifest').disabled=locked;get('batch-file').disabled=locked;get('batch-example').disabled=locked;
    get('batch-preview').disabled=locked||previewBusy;get('batch-history-refresh').disabled=historyBusy;
  }
  function invalidate(){draftVersion++;preview=null;previewText=null;consent=false;previewBusy=false;say(null);renderPreview();controls();}
  function renderPreview(){
    const area=get('batch-preview-content');area.replaceChildren();
    if(pendingCreate){area.append(node('p',pendingCreate.manifest.run_key,'batch-identity'),node('p',t('Chưa xác nhận được lô đã gửi. Giữ nguyên run_key; chỉ đối soát, không tự tạo lô hoặc gửi lại.'),'notice'),
      button('Đối soát yêu cầu đã gửi','batch-reconcile',reconcile,posting||historyBusy));return;}
    if(!preview)return;const currentPreview=preview,currentDraft=draftVersion;
    area.append(node('h3',t('Nguồn đã chụp cho lô')),node('p',preview.manifest.run_key+' · '+preview.items.length+' '+t('mục điều tra'),'batch-identity'),
      node('p',t('Chạy tối đa một mục mỗi lúc. Nguồn đã chụp chưa phải proof lịch sử; mỗi kết quả giữ receipt riêng.'),'notice'));
    const list=node('div',undefined,'batch-items');
    for(const row of preview.items){const request=preview.manifest.items.find(item=>item.key===row.key).request,card=node('article',undefined,'batch-item');card.dataset.batchPreviewKey=row.key;
      card.append(node('h4',row.key+' · '+request.mode+' · '+request.provider+' / '+request.depth),node('p',t('Cận call model: {count}',{count:row.call_upper_bound}),'notice'));
      if(row.error)card.append(node('p',row.error.code+': '+row.error.message,'error'));
      else{card.append(node('p',row.source.case.repository,'batch-source'),node('p',t('Commit đã chụp: ')+row.source.revision,'hash-note'));
        const detail=node('details');detail.append(node('summary',t('Xem issue và nguồn đã chụp')),node('pre',row.source.case.problem_statement),node('p','SHA256 '+row.source_sha256,'hash-note'));card.append(detail);}
      list.append(card);}
    if(acceptedPreviews.has(preview.sha256)){const details=node('details',undefined,'batch-approved-preview'),sha=preview.sha256;details.id='batch-approved-preview';details.open=approvedPreviewOpen.get(sha)===true;
      details.ontoggle=()=>approvedPreviewOpen.set(sha,details.open);details.append(node('summary',t('Xem lại {count} nguồn đã duyệt',{count:preview.items.length})),list);area.append(details);
    }else area.append(list);
    area.append(node('p',preview.requires_api?t('Có mục dùng Groq. Tổng USD chưa biết; cận từng call và ledger vẫn được kiểm khi chạy.'):t('Lô offline: 0 call model, 0 USD model.'),'notice'));
    if(preview.requires_api){const label=node('label',undefined,'checkbox'),input=node('input');input.id='batch-groq-consent';input.type='checkbox';input.checked=consent;input.disabled=posting;
      const p=preview,version=draftVersion;input.onchange=()=>{if(preview===p&&version===draftVersion&&!posting){consent=input.checked;const start=get('batch-start');if(start)start.disabled=acceptedPreviews.has(preview.sha256)||!consent||!preview.can_start;}};
      label.append(input,node('span',t('Tôi duyệt toàn bộ nguồn của manifest này và đồng ý gọi Groq, có chi phí API.')));area.append(label);}
    area.append(button('Tạo và chạy lô đã duyệt','batch-start',()=>{if(preview===currentPreview&&draftVersion===currentDraft)return start();},posting||acceptedPreviews.has(preview.sha256)||!preview.can_start||(preview.requires_api&&!consent)),
      node('p','Manifest SHA256 '+preview.manifest_sha256,'hash-note'),node('p','Preview SHA256 '+preview.sha256,'hash-note'));
  }
  function renderHistory(){const area=get('batch-history-list');area.replaceChildren();
    if(!historyRows.length&&!historyErrors.length){area.append(node('p',t('Chưa có lô điều tra đã lưu.'),'notice'));return;}
    for(const row of historyRows){const item=node('button');item.type='button';item.onclick=()=>open(row);item.className='batch-history-row';item.dataset.batchId=row.id;item.dataset.batchStatus=row.status;
      item.replaceChildren(node('strong',row.run_key),node('span',t(labels[row.status])),node('small',row.counts.completed+'/'+row.counts.total+' · '+row.id));area.append(item);}
    if(historyErrors.length){const notice=node('section',undefined,'batch-history-unavailable');notice.append(node('p',t('Không đọc được {count} lô đã lưu; các lô hợp lệ vẫn mở được. Không tự chạy lại.',{count:historyErrors.length}),'notice'));
      const list=node('ul');for(const error of historyErrors)list.append(node('li',error.id+' · '+error.code,'hash-note'));notice.append(list);area.append(notice);}
  }
  function updateHistory(row){
    const previous=historyRows.find(value=>value.id===row.id);if(previous&&!validRead(row,previous))return;
    historyRows=previous?historyRows.map(value=>value.id===row.id?row:value):[row,...historyRows].slice(0,50);
    historyErrors=historyErrors.filter(error=>error.id!==row.id);renderHistory();
  }
  function renderDetail(){const area=get('batch-detail'),focused=area.contains?.(document.activeElement)?document.activeElement.id:null;area.replaceChildren();if(!selected)return;
    const row=selected,version=view,current=()=>selected===row&&view===version;area.dataset.batchId=row.id;area.dataset.batchStatus=row.status;
    area.append(node('h3',t('Lô đang xem')+' · '+row.run_key),node('p',t(labels[row.status])+' · '+row.id,'batch-identity'));
    const counts=node('dl',undefined,'batch-counts');for(const status of itemStates){const cell=node('div');cell.append(node('dt',t(labels[status])),node('dd',String(row.counts[status])));counts.append(cell);}area.append(counts);
    const actions=node('div',undefined,'batch-actions'),locked=actionBusy||reading;
    actions.append(button('Làm mới trạng thái lô','batch-refresh',()=>current()&&refresh(),locked));
    if(actionUnknown)actions.append(node('p',t('Thao tác chưa được xác nhận. Làm mới trạng thái trước khi thao tác tiếp; không tự gửi lại.'),'notice'));
    if(row.desired_state==='running'&&active(row))actions.append(button('Tạm dừng nhận mục mới','batch-pause',()=>current()&&act('pause'),locked||actionUnknown));
    if(row.desired_state!=='cancelled'&&row.counts.pending+row.counts.queued>0&&(row.desired_state==='paused'||row.status==='interrupted'||row.counts.running===0)){
      if(row.requires_api){const label=node('label',undefined,'checkbox'),input=node('input');input.id='batch-continue-consent';input.type='checkbox';input.checked=continueConsent;input.disabled=locked||actionUnknown;
        input.onchange=()=>{if(selected===row&&view===version&&!input.disabled){continueConsent=input.checked;get('batch-continue').disabled=!continueConsent;}};
        label.append(input,node('span',t('Tôi đồng ý tiếp tục các mục Groq chưa chạy của đúng lô này, có chi phí API.')));actions.append(label);}
      actions.append(button('Tiếp tục mục chưa chạy','batch-continue',()=>current()&&act('continue'),locked||actionUnknown||(row.requires_api&&!continueConsent)));}
    if((active(row)||row.counts.pending>0)&&row.desired_state!=='cancelled')actions.append(button('Hủy các mục còn lại','batch-cancel',()=>current()&&act('cancel'),locked||actionUnknown));area.append(actions);
    if(row.desired_state==='cancelled'&&active(row))area.append(node('p',t('Đã yêu cầu hủy lô; đang chờ các mục đang chạy hoặc trong hàng đợi dừng lại.'),'notice'));
    area.append(node('p',t('Tạm dừng không hủy call đang chạy. Hủy được kiểm tại ranh giới stage; call đã gửi vẫn có thể tính phí. Mục lỗi/ngắt không được thử lại tự động.'),'notice'));
    const list=node('div',undefined,'batch-items');
    for(const item of row.items){const card=node('article',undefined,'batch-item');card.dataset.batchKey=item.key;
      card.append(node('h4',item.key+' · '+t(labels[item.status])),node('p',item.mode+' · '+item.provider+' / '+item.depth+' · '+item.stage,'notice'),node('p',item.revision,'hash-note'));
      if(item.job_id)card.append(node('p',item.job_id,'hash-note'));if(item.error)card.append(node('p',item.error,'error'));
      const buttons=node('div',undefined,'batch-actions');
      if(item.job_id&&item.job_available)buttons.append(button('Mở phiên điều tra','batch-item-open-'+item.key,()=>{
        if(view===version&&selected===row)document.dispatchEvent(new CustomEvent('issueintel:batchchildopen',{detail:{batch_id:row.id,job_id:item.job_id,status:item.status}}));},false));
      if(['pending','queued','running'].includes(item.status)&&row.desired_state!=='cancelled')buttons.append(button('Hủy mục này','batch-item-cancel-'+item.key,()=>current()&&act('item_cancel',item.key),locked||actionUnknown));
      card.append(buttons);list.append(card);}
    area.append(list,node('p','Manifest SHA256 '+row.manifest_sha256,'hash-note'),node('p','Preview SHA256 '+row.preview_sha256,'hash-note'));
    if(focused)get(focused)?.focus?.({preventScroll:true});
  }
  async function inspect(event){event?.preventDefault();if(previewBusy||posting||pendingCreate)return;
    const text=get('batch-manifest').value,version=++draftVersion;let manifest;
    preview=null;previewText=null;consent=false;previewBusy=true;renderPreview();controls();say('Đang chụp nguồn và kiểm manifest; không gọi model…');
    try{if(new TextEncoder().encode(text).length>512*1024)throw Error('Manifest bound');manifest=JSON.parse(text);
      const value=await api('/api/batches/preview',{manifest_json:text});if(version!==draftVersion||text!==get('batch-manifest').value)return;
      if(!validPreview(value,manifest))throw Error('Invalid batch preview');preview=value;previewText=text;say(value.can_start?'Đã chụp nguồn. Xem từng mục trước khi duyệt chạy.':'Có mục chưa chụp được nguồn. Sửa manifest rồi xem trước lại.');
    }catch(_){if(version===draftVersion)say('Chưa xem trước được. Kiểm JSON, 1–20 mục product và nguồn/revision; không có call model.');}
    finally{if(version===draftVersion){previewBusy=false;renderPreview();controls();}}
  }
  async function start(){if(!preview||!preview.can_start||posting||pendingCreate||acceptedPreviews.has(preview.sha256)||previewText!==get('batch-manifest').value||(preview.requires_api&&!consent))return;
    const p=preview,version=draftVersion,ownedView=view,body={manifest:p.manifest,preview:p,confirmed:true,allow_api_calls:p.requires_api&&consent};
    posting=true;consent=false;pendingCreate=body;renderPreview();controls();say('Đang ghi lô điều tra đã duyệt…');
    try{const row=await api('/api/batches',body);if(!validRead(row,{run_key:p.manifest.run_key,manifest_sha256:p.manifest_sha256,preview_sha256:p.sha256,policy_sha256:p.policy_sha256}))throw Error('Invalid batch acknowledgement');
      pendingCreate=null;acceptedPreviews.add(p.sha256);if(version===draftVersion&&view===ownedView){await select(row);say('Đã lưu lô. Mỗi mục giữ một phiên riêng; không tự mở báo cáo.');}await history();
    }catch(_){say('Chưa xác nhận được lô đã gửi. Giữ nguyên run_key; chỉ đối soát, không tự tạo lô hoặc gửi lại.');}
    finally{posting=false;renderPreview();controls();}
  }
  async function history(){const version=++historyVersion;historyBusy=true;controls();
    try{const value=await api('/api/batches');if(version!==historyVersion)return;
      const errors=value?.errors??[];
      if(value?.schema!=='product_batch_history_v1'||!Array.isArray(value.batches)||value.batches.length>50||!value.batches.every(row=>validRead(row))
        ||!Array.isArray(errors)||errors.length>50||errors.some(error=>!error||Object.keys(error).length!==2||!id(error.id)||error.code!=='source_unavailable'
          ||value.batches.some(row=>row.id===error.id))||new Set(errors.map(error=>error.id)).size!==errors.length)throw Error('Invalid batch history');
      historyRows=value.batches;historyErrors=errors;historyLoaded=true;renderHistory();
    }catch(_){if(version===historyVersion)say('Chưa đọc được danh sách lô; không có lô hoặc call mới được tạo.');}
    finally{if(version===historyVersion){historyBusy=false;controls();}}
  }
  async function reconcile(){if(!pendingCreate||posting)return;const request=pendingCreate,ownedView=view;await history();if(pendingCreate!==request||view!==ownedView)return;
    const row=historyRows.find(value=>value.run_key===request.manifest.run_key);
    if(!row){say('Chưa tìm thấy lô đã gửi. Giữ nguyên run_key và đối soát lại; không tự gửi lại.');return;}
    if(!validRead(row,{manifest_sha256:request.preview.manifest_sha256,preview_sha256:request.preview.sha256,policy_sha256:request.preview.policy_sha256})){say('run_key đã gắn với nguồn khác. Không tạo lô thay thế; kiểm danh sách đã lưu.');return;}
    pendingCreate=null;acceptedPreviews.add(request.preview.sha256);renderPreview();controls();await open(row);
  }
  async function select(row){stopPolling();view++;selected=row;reading=false;actionBusy=false;actionUnknown=false;continueConsent=false;updateHistory(row);renderDetail();schedule();}
  async function open(row){if(!validRead(row))return;await select(row);await refresh();}
  function schedule(){stopPolling();if(active(selected)){const version=view,identifier=selected.id;timer=setTimeout(()=>{timer=null;if(view===version&&selected?.id===identifier)return refresh();},1000);}}
  async function refresh(){if(!selected||reading||actionBusy)return;const old=selected,version=view;reading=true;renderDetail();
    try{const row=await api('/api/batches/'+old.id);if(view!==version||selected?.id!==old.id)return;if(!validRead(row,old))throw Error('Changed batch binding');
      selected=row;actionUnknown=false;updateHistory(row);renderDetail();
    }catch(_){if(view===version)say('Chưa đọc được trạng thái khớp lô đang xem; không thử lại công việc.');}
    finally{if(view===version){reading=false;renderDetail();schedule();}}
  }
  async function act(action,itemKey=null){if(!selected||reading||actionBusy||actionUnknown)return;const old=selected,version=view;
    if(action==='continue'&&old.requires_api&&!continueConsent)return;
    actionBusy=true;continueConsent=false;stopPolling();renderDetail();
    const body=binding(old);if(action==='continue')body.allow_api_calls=old.requires_api;
    const route='/api/batches/'+old.id+(action==='item_cancel'?'/items/'+encodeURIComponent(itemKey)+'/cancel':'/'+action);
    try{const row=await api(route,body);if(view!==version||selected?.id!==old.id)return;if(!validRead(row,old))throw Error('Changed batch acknowledgement');selected=row;updateHistory(row);}
    catch(_){if(view===version){actionUnknown=true;say('Thao tác chưa được xác nhận. Làm mới trạng thái trước khi thao tác tiếp; không tự gửi lại.');}}
    finally{if(view===version){actionBusy=false;renderDetail();schedule();}}
  }
  get('batch-form').onsubmit=inspect;get('batch-manifest').oninput=()=>{if(!pendingCreate&&!posting)invalidate();};
  get('batch-example').onclick=()=>{if(posting||pendingCreate)return;get('batch-manifest').value=JSON.stringify({schema:'product_batch_manifest_v1',run_key:'example-batch-1',items:[{key:'example-quick',request:{mode:'example',provider:'none',depth:'quick'}},{key:'example-deep',request:{mode:'example',provider:'none',depth:'deep'}}]},null,2);invalidate();get('batch-manifest').focus?.();};
  get('batch-file').onchange=async event=>{if(posting||pendingCreate)return;const file=event.target.files?.[0];if(!file)return;const version=++draftVersion;
    preview=null;previewText=null;consent=false;previewBusy=false;renderPreview();controls();try{if(file.size>512*1024)throw Error('Manifest bound');const text=await file.text();if(version!==draftVersion||posting||pendingCreate)return;get('batch-manifest').value=text;invalidate();}
    catch(_){if(version===draftVersion)say('Chưa đọc được file JSON cục bộ hoặc file vượt giới hạn.');}finally{event.target.value='';}};
  get('batch-history-refresh').onclick=history;
  get('batch-panel').ontoggle=()=>{if(get('batch-panel').open&&!historyLoaded&&!historyBusy)return history();};
  document.addEventListener('issueintel:languagechange',()=>{consent=false;continueConsent=false;renderPreview();renderDetail();renderHistory();say(notice);controls();});
  window.IssueIntelBatches=Object.freeze({validPreview,validRead});
  controls();
})();

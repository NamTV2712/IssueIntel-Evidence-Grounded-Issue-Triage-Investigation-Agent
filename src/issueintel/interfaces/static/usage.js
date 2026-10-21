/* Accounting receipts contain no prompts or credentials. Never infer missing costs as zero. */
(() => {
  const t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
  function renderGeneration(container,usage){
    container.replaceChildren();
    if(!usage){container.append(el('p',t('Chưa đọc được metadata chi phí.'),'notice'));return;}
    container.append(el('p',t('Chi phí của phiên: {id}',{id:usage.job_id}),'hash-note'));
    if(!usage.tracking_available){container.append(el('p',t('Phiên cũ hoặc provider chưa ghi receipt; chi phí chưa biết, không coi là 0.'),'notice'));return;}
    const summary=el('dl',undefined,'usage-summary');
    for(const [label,value] of [[t('Lượt gọi/giữ chỗ'),usage.reservations],[t('Token ghi nhận/giữ chỗ'),usage.tokens_charged_or_reserved],[t('USD ghi nhận/giữ chỗ'),'$'+usage.usd_charged_or_reserved]]){const cell=el('div');cell.append(el('dt',label),el('dd',String(value)));summary.append(cell);}container.append(summary);
    container.append(el('p',usage.retained_bounds?t('Có {count} lượt còn giữ cận trên; đây chưa phải hóa đơn thực tế.',{count:usage.retained_bounds}):t('Các giá trị lấy từ receipt cục bộ; không thay thế hóa đơn provider.'),'notice'));
    if(usage.usage_anomalies)container.append(el('p',t('Usage vượt cận hoặc bất nhất; ledger đã khóa lời gọi tiếp theo để kiểm tra.'),'error'));
    for(const receipt of usage.receipts){
      const item=el('details',undefined,'usage-receipt');item.append(el('summary',t(receipt.stage)+' · '+t(receipt.status)),el('p',receipt.model,'hash-note'),
        el('p',t('Cận token: vào {input}, ra {output}',{input:receipt.input_bound,output:receipt.output_bound}),'notice'),
        el('p',t('Ngày bảng giá: {date}',{date:receipt.price_date}),'notice'),el('pre',JSON.stringify(receipt,null,2)));container.append(item);
    }
    const exportButton=el('button',t('Xuất receipt chi phí JSON'));exportButton.type='button';exportButton.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(usage,null,2)],{type:'application/json'}));const link=el('a');link.href=url;link.download='issueintel-'+usage.job_id+'-usage.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};container.append(exportButton);
  }
  function renderTypesafe(container,job){
    if(!job||!(job.controller==='jev'||Object.hasOwn(job,'typesafe_receipts')||Object.hasOwn(job,'typesafe_decisions')))return;
    const panel=el('section',undefined,'workflow-controller');panel.append(el('h3',t('Chi phí quyết định Jev / Typesafe')));container.append(panel);
    panel.append(el('p',t('Chi phí của phiên: {id}',{id:job.id}),'hash-note'),el('p',t('Receipt Typesafe được tính riêng với Groq; số tiền ghi nhận/giữ chỗ chưa phải hóa đơn.'),'notice'));
    const values=job.typesafe_receipts;
    const recovery=job.typesafe_recovery;
    if(recovery!==undefined){
      const valid=recovery?.schema==='typesafe_stage_recovery_v1'&&recovery.owner_job_id===job.id&&/^[a-f0-9]{24}$/.test(recovery.origin_job_id||'')
        &&recovery.max_calls===4&&recovery.automatic_retry===false&&recovery.billing_verified===false
        &&[recovery.inherited_calls,recovery.new_calls].every(n=>Number.isInteger(n)&&n>=0&&n<=4)
        &&recovery.inherited_calls+recovery.new_calls<=4;
      if(!valid){panel.append(el('p',t('Chưa kiểm được receipt Typesafe của phiên này; chi phí chưa biết. Không cộng vào chi phí Groq.'),'notice'));return;}
      panel.append(el('p',t('Lượt thử Jev: {inherited} đã ghi trước đó + {current} mới trong phiên này / {max}; giới hạn gốc giữ nguyên.',
        {inherited:recovery.inherited_calls,current:recovery.new_calls,max:recovery.max_calls}),'notice'),
        el('p',t('Quyết định dùng lại giữ receipt ở phiên đã tạo; không cộng vào chi phí phiên phục hồi.'),'notice'));
      if(recovery.new_calls===0&&values&&typeof values==='object'&&!Array.isArray(values)&&!Object.keys(values).length){
        panel.append(el('p',t('Phiên này không tạo lượt thử Jev mới; quyết định và chi phí trước đó giữ ở phiên nguồn.'),'notice'));return;
      }
    }
    if(!values||typeof values!=='object'||Array.isArray(values)||!Object.keys(values).length){
      panel.append(el('p',t('Chưa có receipt Typesafe; chưa coi chi phí là 0. Các lượt bị chặn có thể chưa gửi API.'),'notice'));return;
    }
    const statuses={reserved:'Đang giữ chỗ chi phí',settled:'Đã ghi nhận usage',failed_bound:'Lời gọi lỗi; giữ cận chi phí',
      missing_usage_bound:'Thiếu usage; giữ cận chi phí',breach:'Usage vượt cận chi phí'};
    const receipts=Object.entries(values);
    const valid=receipts.length<=4&&(!recovery||receipts.length<=recovery.new_calls)&&/^[a-f0-9]{24}$/.test(job.id||'')&&receipts.every(([id,receipt])=>receipt
      &&receipt.schema==='typesafe_decision_receipt_v1'&&receipt.provider==='typesafe'&&receipt.model==='jev-1.13.0'
      &&receipt.reservation_id===id&&/^[a-f0-9]{32}$/.test(id)&&receipt.binding?.job_id===job.id
      &&Object.hasOwn(statuses,receipt.status)&&Number.isSafeInteger(receipt.micro_usd_charged_or_reserved)&&receipt.micro_usd_charged_or_reserved>=0
      &&[receipt.input_tokens,receipt.output_tokens].every(value=>value===null||Number.isSafeInteger(value)&&value>=0)
      &&/^[a-f0-9]{64}$/.test(receipt.sha256||'')&&receipt.currency==='USD'&&receipt.is_provider_invoice===false);
    const total=valid?receipts.reduce((sum,[,receipt])=>sum+receipt.micro_usd_charged_or_reserved,0):null;
    if(!valid||!Number.isSafeInteger(total)){
      panel.append(el('p',t('Chưa kiểm được receipt Typesafe của phiên này; chi phí chưa biết. Không cộng vào chi phí Groq.'),'notice'));return;
    }
    panel.append(el('p',t('{count} receipt Typesafe · USD ghi nhận/giữ chỗ {usd}',{count:receipts.length,usd:(total/1000000).toFixed(6)})));
    const decisions=Array.isArray(job.typesafe_decisions)?job.typesafe_decisions:[];
    if(decisions.length&&decisions.length<=4&&decisions.every(row=>typeof row?.fallback==='boolean'&&row.binding?.job_id===job.id)){
      panel.append(el('p',t('{count} lượt quyết định dùng fallback theo luật',{count:decisions.filter(row=>row.fallback).length}),'notice'));
    }
    for(const [,receipt] of receipts){
      const item=el('details',undefined,'usage-receipt');item.append(el('summary',t('Quyết định {number}',{number:receipt.binding.decision_index})+' · '+t(statuses[receipt.status])),
        el('p',receipt.model,'hash-note'),el('p',t('Token Typesafe: vào {input}, ra {output}',{input:receipt.input_tokens??t('Chưa biết'),output:receipt.output_tokens??t('Chưa biết')}),'notice'),
        el('p',t('USD ghi nhận/giữ chỗ: {usd}; chưa phải hóa đơn.',{usd:(receipt.micro_usd_charged_or_reserved/1000000).toFixed(6)}),'notice'),
        el('p',t('Ngày bảng giá: {date}',{date:receipt.price_date}),'notice'),el('pre',JSON.stringify(receipt,null,2)));panel.append(item);
    }
    if(receipts.some(([,receipt])=>receipt.status==='breach'))panel.append(el('p',t('Usage Typesafe vượt cận; cần kiểm ledger trước khi gọi tiếp.'),'error'));
    const button=el('button',t('Xuất receipt Typesafe JSON'));button.type='button';button.onclick=()=>{
      const output={schema:'typesafe_usage_export_v1',job_id:job.id,is_provider_invoice:false,receipts:receipts.map(([,receipt])=>receipt),
        ...(recovery?{recovery}:{})};
      const url=URL.createObjectURL(new Blob([JSON.stringify(output,null,2)],{type:'application/json'}));const link=el('a');link.href=url;link.download='issueintel-'+job.id+'-typesafe-usage.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };panel.append(button);
  }
  function render(container,usage,job){
    renderGeneration(container,usage);
    if(job&&(job.controller==='jev'||Object.hasOwn(job,'typesafe_receipts')||Object.hasOwn(job,'typesafe_decisions'))){
      container.prepend(el('h3',t('Chi phí sinh giả thuyết / verifier')));
    }
    renderTypesafe(container,job);
  }
  window.IssueIntelUsage={render};
})();

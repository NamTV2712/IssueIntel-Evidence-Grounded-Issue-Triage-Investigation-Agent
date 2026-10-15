/* Accounting receipts contain no prompts or credentials. Never infer missing costs as zero. */
(() => {
  const t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
  function render(container,usage){
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
  window.IssueIntelUsage={render};
})();

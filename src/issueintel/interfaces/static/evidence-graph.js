/* Accessible saved-citation graph. Source/model text is never parsed as HTML. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t, NS='http://www.w3.org/2000/svg';
  const cache=new Map(),pending=new Map();let generation=0;
  const element=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
  const svgElement=(tag,attrs,text)=>{const node=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;return node;};
  const relations=['retrieved_context','claimed_support','claimed_counter','model_assessment','critic_citation'];
  async function render(container,jobId){
    const version=++generation;container.replaceChildren(element('p',t('Đang đọc đồ thị đã lưu…'),'notice'));
    try{
      if(!cache.has(jobId)){
        if(!pending.has(jobId))pending.set(jobId,api('/api/jobs/'+jobId+'/evidence-graph'));
        try{const graph=await pending.get(jobId);if(graph.source.job_id!==jobId)throw Error('Source binding mismatch');cache.set(jobId,graph);if(cache.size>8)cache.delete(cache.keys().next().value);}
        finally{pending.delete(jobId);}
      }
      if(version!==generation)return;
      draw(container,cache.get(jobId),jobId);
    }catch(_){if(version===generation)container.replaceChildren(element('p',t('Không đọc được đồ thị; kiểm tra nguồn và citation.'),'notice'));}
  }
  function draw(container,graph,jobId){
    container.replaceChildren();
    container.append(element('p',t('Đường nối mô tả truy xuất và citation đã lưu, chưa chứng minh nguyên nhân. Verifier bằng model không phải kiểm người.'),'notice'),
      element('p','Commit '+graph.commit_sha,'hash-note'));
    if(!graph.nodes.some(node=>node.kind==='hypothesis'))container.append(element('p',t('Chưa có giả thuyết; đồ thị chỉ hiển thị nguồn đã truy xuất. Mở đồ thị không gọi LLM.'),'notice'));
    if(graph.omitted_nodes || graph.omitted_edges)container.append(element('p',t('Đã giới hạn hiển thị: {nodes} nút và {edges} cạnh bị lược bỏ.',{nodes:graph.omitted_nodes,edges:graph.omitted_edges}),'notice'));
    const label=element('label',t('Loại quan hệ'));label.htmlFor='evidence-graph-filter';
    const filter=element('select');filter.id='evidence-graph-filter';
    for(const relation of ['all',...relations]){const option=element('option',t(relation==='all'?'Mọi quan hệ':relation));option.value=relation;filter.append(option);}
    const toolbar=element('div',undefined,'graph-toolbar'),reset=element('button',t('Bỏ chọn nút')),download=element('button',t('Xuất đồ thị JSON'));
    reset.type=download.type='button';toolbar.append(label,filter,reset,download);container.append(toolbar);
    download.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(graph,null,2)],{type:'application/json'}));const link=element('a');link.href=url;link.download='issueintel-evidence-graph.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    const scroll=element('div',undefined,'graph-scroll');scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',t('Đồ thị bằng chứng; dùng Tab để chọn nút'));
    const columns=[[],[],[]];
    for(const node of graph.nodes)columns[node.kind==='hypothesis'||node.kind==='query'?0:node.kind==='model_review'?2:1].push(node);
    columns[0].sort((a,b)=>Number(a.kind!=='query')-Number(b.kind!=='query'));
    const height=Math.max(180,Math.max(...columns.map(column=>column.length))*82+48);
    const svg=svgElement('svg',{viewBox:`0 0 820 ${height}`,width:820,height,role:'group','aria-label':t('Đồ thị bằng chứng')});scroll.append(svg);container.append(scroll);
    const defs=svgElement('defs',{}),marker=svgElement('marker',{id:'evidence-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:5,markerHeight:5,orient:'auto-start-reverse'});
    marker.append(svgElement('path',{d:'M 0 0 L 10 5 L 0 10 z',class:'graph-arrow'}));defs.append(marker);svg.append(defs);
    const positions=new Map(),buttons=new Map(),paths=[];let selected=null;
    columns.forEach((column,i)=>{
      svg.append(svgElement('text',{x:16+i*280,y:18,class:'graph-lane'},t(['Truy vấn và giả thuyết','Nguồn bằng chứng','Nhận xét model'][i])));
      column.forEach((node,j)=>positions.set(node.id,{x:16+i*280,y:36+j*82,node}));
    });
    for(const edge of graph.edges){
      const a=positions.get(edge.source),b=positions.get(edge.target);if(!a||!b)continue;
      const right=a.x<b.x,x1=a.x+(right?220:0),x2=b.x+(right?0:220),y1=a.y+31,y2=b.y+31;
      const path=svgElement('path',{d:`M ${x1} ${y1} C ${(x1+x2)/2} ${y1}, ${(x1+x2)/2} ${y2}, ${x2} ${y2}`,
        class:'graph-edge edge-'+edge.relation,'marker-end':'url(#evidence-arrow)','aria-hidden':'true'});svg.append(path);paths.push({path,edge});
    }
    const inspector=element('section',undefined,'graph-inspector');inspector.setAttribute('aria-label',t('Chi tiết nút đã chọn'));container.append(inspector);
    function select(id){selected=id;refresh();}
    function refresh(){
      for(const {path,edge} of paths){const show=filter.value==='all'||filter.value===edge.relation;path.setAttribute('visibility',show?'visible':'hidden');path.setAttribute('class','graph-edge edge-'+edge.relation+(selected&&edge.source!==selected&&edge.target!==selected?' graph-dim':''));}
      for(const [id,button] of buttons){button.setAttribute('aria-pressed',String(id===selected));button.setAttribute('class','graph-node'+(id===selected?' graph-selected':''));}
      inspector.replaceChildren();
      if(!selected){inspector.append(element('p',t('Chọn một nút để xem nội dung, hash và các quan hệ. Nét đứt là citation phản bác; nét chấm là nguồn truy xuất.'),'notice'));return;}
      const node=positions.get(selected).node;
      inspector.append(element('h3',node.title),element('span',t(node.kind),'badge'),element('pre',node.text));
      if(node.text_truncated)inspector.append(element('p',t('Trích đoạn bị giới hạn; mở citation trong báo cáo để xem nguồn.'),'notice'));
      if(node.revision)inspector.append(element('p','Commit '+node.revision,'hash-note'));
      if(node.sha256)inspector.append(element('p','SHA256 '+node.sha256,'hash-note'));
      if(node.counter_status)inspector.append(element('p',t('Bằng chứng phản bác: ')+t(node.counter_status),'notice'));
      if(node.falsification_test)inspector.append(element('p',t('Cách kiểm tra: ')+node.falsification_test));
      if(node.assessment)inspector.append(element('p',t(node.assessment)+' · '+t('Nhận xét model'),'notice'));
      if(node.evidence_id){const open=element('button',t('Mở citation trong báo cáo'));open.type='button';open.onclick=()=>document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:jobId,evidence_id:node.evidence_id}}));inspector.append(open);}
      const list=element('ul');
      for(const edge of graph.edges.filter(e=>(e.source===selected||e.target===selected)&&(filter.value==='all'||filter.value===e.relation))){const other=positions.get(edge.source===selected?edge.target:edge.source)?.node;if(!other)continue;const item=element('li'),button=element('button',t(edge.relation)+(edge.source===selected?' → ':' ← ')+other.title);button.type='button';button.onclick=()=>select(other.id);item.append(button);list.append(item);}inspector.append(list);
    }
    for(const [id,{x,y,node}] of positions){
      const button=svgElement('g',{role:'button',tabindex:0,'aria-label':t(node.kind)+' · '+node.title,'aria-pressed':'false',class:'graph-node'});
      button.append(svgElement('rect',{x,y,width:220,height:62,rx:7}),svgElement('text',{x:x+10,y:y+17,class:'graph-kind'},t(node.kind)));
      const title=node.title.length>52?node.title.slice(0,51)+'…':node.title;button.append(svgElement('text',{x:x+10,y:y+36},title.slice(0,26)),svgElement('text',{x:x+10,y:y+51},title.slice(26)));
      button.onclick=()=>select(id);button.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select(id);}};buttons.set(id,button);svg.append(button);
    }
    filter.value='all';filter.onchange=refresh;reset.onclick=()=>select(null);refresh();
    container.append(element('p','Graph SHA256 '+graph.sha256+' · Pack SHA256 '+graph.source.pack_sha256,'hash-note'));
  }
  window.IssueIntelEvidenceGraph={render,cancel:()=>{generation++;}};
})();

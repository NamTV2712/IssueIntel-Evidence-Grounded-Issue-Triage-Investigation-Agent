/* Accessible saved-citation graph. Source/model text is never parsed as HTML. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t, NS='http://www.w3.org/2000/svg';
  const cache=new Map(),pending=new Map();let generation=0;const states=new Map();let cleanup=()=>{};
  const element=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
  const svgElement=(tag,attrs,text)=>{const node=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;return node;};
  const relations=['retrieved_context','claimed_support','claimed_counter','model_assessment','critic_citation'];
  async function render(container,jobId){
    cleanup();cleanup=()=>{};const version=++generation;container.replaceChildren(element('p',t('Đang đọc đồ thị đã lưu…'),'notice'));
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
    const state=states.get(jobId)||{selected:graph.nodes.find(n=>n.kind==='hypothesis')?.id||graph.nodes[0]?.id,mode:'focus',relation:'all'};
    states.set(jobId,state);if(states.size>8)states.delete(states.keys().next().value);
    const workbench=element('div',undefined,'eg-workbench');container.append(workbench);
    const heading=element('div',undefined,'eg-heading'),intro=element('div');
    intro.append(element('span','EVIDENCE MAP','eg-eyebrow'),element('h3',t('Lần theo từng giả thuyết')),element('p',t('Nguồn → giả thuyết → nhận xét. Chọn một nút để kiểm tra liên kết.')));
    const commit=element('span',graph.commit_sha.slice(0,12),'eg-commit');commit.title=graph.commit_sha;heading.append(intro,commit);workbench.append(heading);
    const toolbar=element('div',undefined,'eg-toolbar'),modes=element('div',undefined,'eg-segments');modes.setAttribute('aria-label',t('Chế độ đồ thị'));
    const modeButtons=[];
    for(const [value,key] of [['focus','Theo nút'],['all','Toàn bộ']]){const button=element('button',t(key));button.type='button';button.onclick=()=>{state.mode=value;update();};modeButtons.push({button,value});modes.append(button);}
    const label=element('label',t('Loại quan hệ'));label.htmlFor='evidence-graph-filter';const filter=element('select');filter.id='evidence-graph-filter';
    for(const relation of ['all',...relations]){const option=element('option',t(relation==='all'?'Mọi quan hệ':relation));option.value=relation;filter.append(option);}filter.value=state.relation;
    const expand=element('button',t('Phóng rộng')),download=element('button',t('Xuất đồ thị JSON'));expand.type=download.type='button';
    toolbar.append(modes,label,filter,expand,download);workbench.append(toolbar);
    download.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(graph,null,2)],{type:'application/json'}));const link=element('a');link.href=url;link.download='issueintel-evidence-graph.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    const navigation=element('div',undefined,'eg-navigation');navigation.setAttribute('aria-label',t('Chọn tâm đồ thị'));
    for(const node of graph.nodes.filter(n=>n.kind==='hypothesis'||n.kind==='query')){
      const button=element('button',node.kind==='query'?t('query'):node.hypothesis_id||node.id.split(':').pop());button.type='button';button.title=node.title;button.setAttribute('aria-label',t(node.kind)+' · '+node.title);button.onclick=()=>select(node.id);navigation.append(button);button.dataset.nodeId=node.id;
    }workbench.append(navigation);
    const layout=element('div',undefined,'eg-layout'),map=element('div',undefined,'eg-map'),scroll=element('div',undefined,'eg-scroll'),canvas=element('div',undefined,'eg-canvas');
    scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',t('Đồ thị bằng chứng; dùng Tab để chọn nút'));scroll.append(canvas);map.append(scroll);
    const legend=element('div',undefined,'eg-legend');for(const relation of ['claimed_support','claimed_counter','model_assessment','retrieved_context']){const item=element('span',t(relation),'eg-key eg-key-'+relation);legend.append(item);}map.append(legend);
    const scope=element('p',undefined,'eg-scope');map.append(scope);
    const inspector=element('section',undefined,'eg-inspector');inspector.setAttribute('aria-label',t('Chi tiết nút đã chọn'));layout.append(map,inspector);workbench.append(layout);
    const caution=element('p',t('Citation chưa chứng minh nguyên nhân. Nhận xét model không phải kiểm người.'),'eg-caution');workbench.append(caution);
    if(!graph.nodes.some(n=>n.kind==='hypothesis'))workbench.append(element('p',t('Chưa có giả thuyết; đồ thị chỉ hiển thị nguồn đã truy xuất. Mở đồ thị không gọi LLM.'),'notice'));
    if(graph.omitted_nodes||graph.omitted_edges)workbench.append(element('p',t('Đã giới hạn hiển thị: {nodes} nút và {edges} cạnh bị lược bỏ.',{nodes:graph.omitted_nodes,edges:graph.omitted_edges}),'notice'));
    const provenance=element('details',undefined,'eg-provenance');provenance.append(element('summary',t('Nguồn và hash đồ thị')),element('p','Graph SHA256 '+graph.sha256+' · Pack SHA256 '+graph.source.pack_sha256,'hash-note'));workbench.append(provenance);
    let observer=null,frame=null,dialog=null,disposed=false,nodeElements=new Map(),visibleEdges=[];
    const byId=new Map(graph.nodes.map(n=>[n.id,n]));
    const title=node=>node.kind==='model_review'?node.id.slice('review:'.length)+' · '+t(node.assessment):node.title;
    function select(id){state.selected=id;update();}
    function connections(){return graph.edges.filter(edge=>state.relation==='all'||edge.relation===state.relation);}
    function update(){
      for(const {button,value} of modeButtons)button.setAttribute('aria-pressed',String(state.mode===value));
      for(const button of navigation.children)button.setAttribute('aria-pressed',String(button.dataset.nodeId===state.selected));
      const edges=connections(),visible=new Set(state.mode==='all'?graph.nodes.map(n=>n.id):[state.selected]);
      if(state.mode==='focus')for(const edge of edges)if(edge.source===state.selected||edge.target===state.selected){visible.add(edge.source);visible.add(edge.target);}
      visibleEdges=edges.filter(e=>visible.has(e.source)&&visible.has(e.target)&&(state.mode==='all'||e.source===state.selected||e.target===state.selected));
      canvas.replaceChildren();nodeElements=new Map();
      const svg=svgElement('svg',{'aria-hidden':'true',class:'eg-wires'});canvas.append(svg);
      const defs=svgElement('defs',{});for(const relation of relations){const marker=svgElement('marker',{id:'eg-arrow-'+relation,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:5,markerHeight:5,orient:'auto'});marker.append(svgElement('path',{d:'M 0 0 L 10 5 L 0 10 z',class:'eg-arrow edge-'+relation}));defs.append(marker);}svg.append(defs);
      const lanes=[['code','repository_context'],['query','hypothesis'],['model_review']];
      lanes.forEach((kinds,index)=>{
        const lane=element('div',undefined,'eg-lane');lane.append(element('div',t(['Nguồn bằng chứng','Truy vấn và giả thuyết','Nhận xét model'][index]),'eg-lane-title'));
        const nodes=graph.nodes.filter(n=>visible.has(n.id)&&kinds.includes(n.kind));
        for(const node of nodes){
          const button=element('button',undefined,'eg-node eg-'+node.kind);button.type='button';button.dataset.nodeId=node.id;button.setAttribute('aria-label',t(node.kind)+' · '+node.title);button.setAttribute('aria-pressed',String(node.id===state.selected));
          const head=element('span',undefined,'eg-node-head');head.append(element('span',node.kind==='hypothesis'?'H':node.kind==='model_review'?'V':node.kind==='query'?'?':'⌘','eg-node-icon'),element('span',t(node.kind)),element('span','↗','eg-node-open'));button.append(head);
          const body=node.kind==='query'?node.text:node.kind==='model_review'?node.text:node.kind==='hypothesis'?node.text:node.title;
          button.append(element('span',body,'eg-node-title'));
          const meta=node.kind==='hypothesis'?t('Bằng chứng phản bác: ')+t(node.counter_status):node.kind==='model_review'?t(node.assessment):node.revision?node.revision.slice(0,10):node.kind==='query'?node.title:t('Chưa xác minh lịch sử');
          button.append(element('span',meta,'eg-node-meta'));button.onclick=()=>select(node.id);button.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select(node.id);}};
          lane.append(button);nodeElements.set(node.id,button);
        }
        if(!nodes.length)lane.append(element('p',t('Chưa có liên kết trong phạm vi này'),'eg-lane-empty'));canvas.append(lane);
      });
      scope.textContent=t('Đang xem {nodes} nút · {edges} liên kết',{nodes:visible.size,edges:visibleEdges.length});
      inspector.replaceChildren();const node=byId.get(state.selected);
      if(node){
        inspector.append(element('p',t('Chi tiết nút đã chọn'),'eg-eyebrow'),element('span',t(node.kind),'eg-inspector-kind'),element('h4',title(node)),element('pre',node.text));
        if(node.text_truncated)inspector.append(element('p',t('Trích đoạn bị giới hạn; mở citation trong báo cáo để xem nguồn.'),'notice'));
        if(node.counter_status)inspector.append(element('p',t('Bằng chứng phản bác: ')+t(node.counter_status),'eg-inspector-meta'));
        if(node.falsification_test)inspector.append(element('h5',t('Cách kiểm tra: ')),element('p',node.falsification_test));
        if(node.revision)inspector.append(element('p','Commit '+node.revision,'hash-note'));
        if(node.sha256)inspector.append(element('p','SHA256 '+node.sha256,'hash-note'));
        if(node.evidence_id){const open=element('button',t('Mở citation trong báo cáo'));open.type='button';open.onclick=()=>{closeDialog();document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:jobId,evidence_id:node.evidence_id}}));};inspector.append(open);}
        const list=element('div',undefined,'eg-connections');
        for(const edge of edges.filter(e=>e.source===node.id||e.target===node.id)){const other=byId.get(edge.source===node.id?edge.target:edge.source);if(!other)continue;const button=element('button');button.type='button';button.append(element('span',t(edge.relation)+(edge.source===node.id?' →':' ←'),'eg-connection-type'),element('span',title(other)));button.onclick=()=>select(other.id);list.append(button);}inspector.append(list);
      }
      schedule();
    }
    function wires(){
      if(disposed)return;const svg=canvas.children[0],box=canvas.getBoundingClientRect();
      if(!box.width||!box.height)return;
      for(const path of [...svg.children].filter(child=>child.tagName?.toLowerCase()==='path'))path.remove();
      svg.setAttribute('viewBox',`0 0 ${box.width} ${box.height}`);
      for(const edge of visibleEdges){const a=nodeElements.get(edge.source)?.getBoundingClientRect(),b=nodeElements.get(edge.target)?.getBoundingClientRect();if(!a||!b)continue;
        const right=a.left<b.left,x1=(right?a.right:a.left)-box.left,x2=(right?b.left:b.right)-box.left,y1=a.top+a.height/2-box.top,y2=b.top+b.height/2-box.top;
        svg.append(svgElement('path',{d:`M ${x1} ${y1} C ${x1+(right?36:-36)} ${y1}, ${x2-(right?36:-36)} ${y2}, ${x2} ${y2}`,class:'eg-wire edge-'+edge.relation,'marker-end':'url(#eg-arrow-'+edge.relation+')'}));
      }
    }
    function schedule(){if(frame!==null)cancelAnimationFrame(frame);frame=requestAnimationFrame(wires);}
    function closeDialog(){if(dialog?.open)dialog.close();}
    expand.onclick=()=>{
      if(dialog?.open){closeDialog();return;}
      dialog=element('dialog',undefined,'eg-dialog');dialog.setAttribute('aria-label',t('Đồ thị bằng chứng'));document.body.append(dialog);dialog.append(workbench);expand.textContent=t('Thu gọn');
      dialog.onclose=()=>{if(!disposed){container.append(workbench);expand.textContent=t('Phóng rộng');expand.focus();schedule();}dialog.remove();dialog=null;};dialog.showModal();schedule();
    };
    filter.onchange=()=>{state.relation=filter.value;update();};
    if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(schedule);observer.observe(canvas);}
    cleanup=()=>{disposed=true;observer?.disconnect();if(frame!==null)cancelAnimationFrame(frame);if(dialog){dialog.onclose=null;dialog.close();dialog.remove();}};
    update();
  }
  window.IssueIntelEvidenceGraph={render,cancel:()=>{generation++;cleanup();cleanup=()=>{};}};
})();

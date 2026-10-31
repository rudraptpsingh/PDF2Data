/* Source-bound inspection only. No review decisions or recognition in the view. */
(() => {
  'use strict';
  const payload = document.getElementById('overlay-data');
  if (!payload) return;
  const data = JSON.parse(payload.textContent), enabled = new Set(Object.keys(data.palette));
  const $ = id => document.getElementById(id), panes = new Map(), active = new Map();
  const make = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const all = $('overlay-enabled'), labels = $('overlay-labels'), opacity = $('overlay-opacity');
  function color(node, type) {
    const hex = data.palette[type].color;
    node.style.setProperty('--region-color', hex);
    node.style.setProperty('--region-rgb', [1,3,5].map(i => parseInt(hex.slice(i,i+2),16)).join(','));
  }
  function formatted(region) {
    const box = make('div', undefined, 'overlay-parsed');
    if (!region.preview_html) { box.textContent = region.text || 'No aligned text reading for this source region.'; return box; }
    const template = document.createElement('template'); template.innerHTML = region.preview_html;
    const allowed = new Set(['P','DIV','SPAN','STRONG','EM','B','I','BR','PRE','BLOCKQUOTE','UL','OL','LI','H1','H2','H3','H4','H5','H6','FIGURE','FIGCAPTION','TABLE','THEAD','TBODY','TR','TD','TH','A']);
    for (const node of [...template.content.querySelectorAll('*')]) {
      if (!allowed.has(node.tagName)) { node.remove(); continue; }
      for (const attr of [...node.attributes]) if (!['class','rowspan','colspan'].includes(attr.name) && !(attr.name === 'href' && /^source\.pdf#page=\d+$/.test(attr.value))) node.removeAttribute(attr.name);
    }
    box.append(template.content); return box;
  }
  function connect(page) {
    const chosen = active.get(page.page), item = panes.get(page.page); if (!item) return;
    item.link.replaceChildren();
    if (!chosen || !all.checked || !enabled.has(chosen.region.type)) return;
    const source = item.source.getBoundingClientRect(), box = chosen.button.getBoundingClientRect(), panel = item.pane.getBoundingClientRect();
    if (!source.width || !source.height) return;
    item.link.setAttribute('viewBox', '0 0 '+source.width+' '+source.height);
    const x1=box.right-source.left, y1=box.top+box.height/2-source.top, x2=panel.left-source.left, y2=panel.top+23-source.top;
    const path=document.createElementNS('http://www.w3.org/2000/svg','path'), bend=Math.max(12,(x2-x1)/2);
    path.setAttribute('d',`M${x1},${y1} C${x1+bend},${y1} ${x2-bend},${y2} ${x2},${y2}`);
    path.setAttribute('stroke',data.palette[chosen.region.type].color); item.link.append(path);
  }
  function inspect(region, page, button) {
    const item=panes.get(page.page); if (!item) return;
    item.source.querySelectorAll('.overlay-box').forEach(n => { n.dataset.hovered='false'; n.setAttribute('aria-pressed','false'); });
    button.dataset.hovered='true'; button.setAttribute('aria-pressed','true'); active.set(page.page,{region,button});
    const pane=item.pane; pane.replaceChildren(); pane.classList.remove('empty-pane'); color(pane,region.type);
    const head=make('div',undefined,'overlay-pane-head'), dot=make('span','','overlay-dot'); color(dot,region.type);
    head.append(dot,make('strong',data.palette[region.type].label),make('span','Page '+page.page,'overlay-meta'));
    if (region.block_id && $(region.block_id)) {
      const link=make('a','↗');link.href='#'+region.block_id;link.setAttribute('aria-label','Locate extracted block');
      link.onclick=event=>{event.preventDefault();$('full-mode').click();$(region.block_id).scrollIntoView({block:'center',behavior:'smooth'});};head.append(link);
    }
    pane.append(head,formatted(region));
    const meta=make('div',region.state.replaceAll('_',' '),'overlay-pane-meta');
    if (region.formatting) meta.append(make('span',' · '+region.formatting.alignment_candidate.replaceAll('_',' ')));
    pane.append(meta); connect(page);
  }
  for (const [type,spec] of Object.entries(data.palette)) {
    if (!data.pages.some(p=>p.regions.some(r=>r.type===type))) continue;
    const chip=make('button',undefined,'overlay-chip');chip.type='button';chip.setAttribute('aria-pressed','true');color(chip,type);
    chip.append(make('span','','overlay-dot'),make('span',spec.label));
    chip.onclick=()=>{enabled.has(type)?enabled.delete(type):enabled.add(type);chip.setAttribute('aria-pressed',String(enabled.has(type)));refresh();};$('overlay-legend').append(chip);
  }
  for (const page of data.pages) {
    const section=$('page-'+page.page);if (!section) continue;
    const canvas=section.querySelector('.source-canvas');if (!canvas) continue;
    const source=section.querySelector('.source');source.classList.add('has-overlay-view');
    const plane=make('div',undefined,'overlay-plane');plane.setAttribute('aria-label','Detected source components');
    const pane=make('aside',undefined,'overlay-inspector overlay-pane empty-pane');pane.setAttribute('aria-label','Parsed region');pane.setAttribute('aria-live','polite');
    pane.append(make('strong','Parsed content'),make('p','Hover or select a component.'));
    const link=document.createElementNS('http://www.w3.org/2000/svg','svg');link.classList.add('overlay-link');link.setAttribute('aria-hidden','true');
    panes.set(page.page,{source,pane,link});
    page.regions.forEach((region,i)=>{
      const button=make('button',undefined,'overlay-box');button.type='button';const [x0,y0,x1,y1]=region.bbox_normalized;
      Object.assign(button.style,{left:x0*100+'%',top:y0*100+'%',width:(x1-x0)*100+'%',height:(y1-y0)*100+'%'});
      color(button,region.type);button.dataset.type=region.type;button.dataset.state=region.state;button.setAttribute('aria-pressed','false');
      const label=(i+1)+' · '+data.palette[region.type].label;button.setAttribute('aria-label',label+', '+region.state.replaceAll('_',' '));button.title=label;
      button.append(make('span',label,'overlay-tag'));button.onclick=()=>inspect(region,page,button);button.onmouseenter=()=>inspect(region,page,button);button.onfocus=()=>inspect(region,page,button);plane.append(button);
    });
    canvas.append(plane);source.append(pane,link);
    if(page.unlocated_blocks.length)source.append(make('p',page.unlocated_blocks.length+' blocks have no source coordinates.','overlay-missing'));
  }
  function refresh() {
    document.querySelectorAll('.overlay-plane').forEach(p=>{p.hidden=!all.checked;p.classList.toggle('hide-labels',!labels.checked);p.style.setProperty('--overlay-alpha',Number(opacity.value)/100);});
    document.querySelectorAll('.overlay-box').forEach(b=>{b.hidden=!enabled.has(b.dataset.type);});
    for(const page of data.pages){const item=panes.get(page.page);if(item){item.pane.hidden=!all.checked;item.source.classList.toggle('overlays-off',!all.checked);connect(page);}}
  }
  function redraw(){for(const page of data.pages)connect(page);}
  all.onchange=refresh;labels.onchange=refresh;opacity.oninput=refresh;
  window.addEventListener('resize',redraw);window.addEventListener('scroll',redraw,true);
  for(const id of ['pages','next','prev'])$(id)?.addEventListener(id==='pages'?'change':'click',()=>requestAnimationFrame(redraw));
  refresh();
})();

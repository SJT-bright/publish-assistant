/* Local, static vectors: no fonts, scripts or icons are loaded from the network. */
(() => {
  const shapes={film:'<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 3v18m8-18v18M4 8h4m8 0h4M4 16h4m8 0h4m-9-7 3 2-3 2z"/>',folder:'<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3z"/>',check:'<path d="m6 12 4 4 8-8"/>',layers:'<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',image:'<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',douyin:'<path d="M14 3v12a4 4 0 1 1-4-4m4-8c1 4 3 5 6 5"/>',xiaohongshu:'<path d="M5 4h6v16H5zM14 5h5v14h-5zM8 8h1m-1 4h1m-1 4h1m8-7h1m-1 4h1"/>',tencent:'<path d="M5 7c2-4 7-4 9 0s6 4 7 0M3 17c1 4 6 4 8 0s7-4 9 0"/><path d="m10 8 5 4-5 4z"/>',bilibili:'<rect x="3" y="6" width="18" height="15" rx="4"/><path d="m7 2 4 4m6-4-4 4M8 11v3m8-3v3m-7 3h6"/>',kuaishou:'<rect x="3" y="8" width="13" height="12" rx="3"/><circle cx="6" cy="5" r="2"/><circle cx="13" cy="4" r="3"/><path d="m16 12 5-3v10l-5-3"/>'};
  Object.assign(shapes, {
    toutiao:'<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M7 7h10M7 10h10M7 13h4v5H7zm7 0h3m-3 3h3m-3 2h3"/>',
    xigua:'<path d="M4 16a10 10 0 0 1 16 0zM3 17h18m-15 0 1.4 3h9.2l1.4-3"/><path d="m9 11 .7 1m4-1-.7 1m-2.3-2v1"/>'
  });
  const icon=name=>`<svg class="symbol" viewBox="0 0 24 24" aria-hidden="true">${shapes[name]||shapes.film}</svg>`;
  function tilt(element){
    const reduced=matchMedia('(prefers-reduced-motion: reduce)');
    const fine=matchMedia('(pointer: fine)');
    let target=[0,0],value=[0,0],velocity=[0,0],frame=0,last=0;
    const reset=()=>{target=[0,0];if(reduced.matches||!fine.matches){cancelAnimationFrame(frame);frame=0;value=[0,0];velocity=[0,0];element.style.setProperty('--rx','0deg');element.style.setProperty('--ry','0deg');}else wake();};
    function tick(time){const dt=Math.min((time-last)/1000||1/60,.032);last=time;let moving=false;for(let i=0;i<2;i++){velocity[i]+=(145*(target[i]-value[i])-24.1*velocity[i])*dt;value[i]+=velocity[i]*dt;moving||=Math.abs(target[i]-value[i])>.004||Math.abs(velocity[i])>.004;}element.style.setProperty('--rx',value[0]+'deg');element.style.setProperty('--ry',value[1]+'deg');frame=moving?requestAnimationFrame(tick):0;}
    function wake(){if(!frame){last=0;frame=requestAnimationFrame(tick);}}
    element.addEventListener('pointermove',event=>{if(reduced.matches||!fine.matches||event.pointerType==='touch')return;const b=element.getBoundingClientRect();target=[-(event.clientY-b.top-b.height/2)/b.height*9,(event.clientX-b.left-b.width/2)/b.width*12];wake();});
    element.addEventListener('pointerleave',reset);reduced.addEventListener('change',reset);fine.addEventListener('change',reset);window.addEventListener('blur',reset);
  }
  function mount(){document.querySelectorAll('[data-symbol]').forEach(el=>{el.innerHTML=icon(el.dataset.symbol);});document.querySelectorAll('[data-tilt]').forEach(tilt);}
  window.DraftVisual={icon,mount};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();

/* Local material editing: native disk files, preview, and non-destructive frame covers. */
(() => {
  'use strict';
  const VIDEO = /\.(mp4|mov|m4v|webm|avi|mkv)$/i;
  const IMAGE = /\.(jpe?g|png|webp)$/i;
  const TEXT = /\.(txt|md|text)$/i;
  const coverKey = {common:'cover',landscape:'cover_landscape',portrait:'cover_portrait'};
  const basename = path => String(path || '').split(/[\\/]/).pop();
  const diskPath = file => {
    const path = window.draftDesktop?.filePath(file);
    if (!path) throw new Error('请在 App 中拖入本机文件，或使用选择按钮。');
    return path;
  };
  function mount({request,notice,dirty,getMaterial,loadVideo,setCover,pick}) {
    const $ = id => document.getElementById(id);
    const player=$('videoPlayer'),seek=$('seek'),time=$('frameTime'),durationLabel=$('videoDuration');
    const status=$('frameStatus'),capture=$('captureFrame'),role=$('captureRole'),ratio=$('captureRatio');
    let source='',duration=0,sourceVersion=0,selectedTime=0,draggingSeek=false,capturing=false,seekPending=false,captureRequest=0;
    const coverVersion={common:0,landscape:0,portrait:0};
    const shownCover={};
    const fieldVersion={title:0,description:0},textRequest={title:0,description:0};
    const fail = error => notice(error?.message || String(error));
    function dropZone(element,callback,acceptText=false) {
      const accepted=e=>e.dataTransfer && ([...e.dataTransfer.types].includes('Files') || acceptText&&[...e.dataTransfer.types].includes('text/plain'));
      let entered=0;
      element.addEventListener('dragenter',e=>{if(accepted(e)){e.preventDefault();entered++;element.classList.add('drop-active');}});
      element.addEventListener('dragover',e=>{if(accepted(e)){e.preventDefault();e.dataTransfer.dropEffect='copy';}});
      element.addEventListener('dragleave',()=>{if(--entered<=0){entered=0;element.classList.remove('drop-active');}});
      element.addEventListener('drop',e=>{if(!accepted(e))return;e.preventDefault();e.stopPropagation();entered=0;element.classList.remove('drop-active');Promise.resolve(callback(e.dataTransfer)).catch(fail);});
    }
    // A file dropped outside an editor must never navigate away from unsaved content.
    document.addEventListener('dragover',e=>{if([...e.dataTransfer?.types||[]].includes('Files'))e.preventDefault();});
    document.addEventListener('drop',e=>{if([...e.dataTransfer?.types||[]].includes('Files'))e.preventDefault();});
    const videoDrop=$('videoDrop');
    dropZone(videoDrop,async data=>{
      const files=[...data.files];if(!files.length)return;
      const file=files.find(f=>VIDEO.test(f.name)) || files[0];
      // Finder folders have an empty media type; backend verifies the chosen directory.
      if(!VIDEO.test(file.name) && file.type)throw new Error('请拖入视频文件或素材文件夹。');
      await loadVideo(diskPath(file));
    });
    $('videoFileInput').addEventListener('change',async e=>{try{if(e.target.files[0])await loadVideo(diskPath(e.target.files[0]));}catch(error){fail(error);}finally{e.target.value='';}});
    videoDrop.addEventListener('keydown',e=>{if(e.target===videoDrop&&(e.key==='Enter'||e.key===' ')){e.preventDefault();$('file').click();}});
    async function readFile(file,field) {
      if(!TEXT.test(file.name))throw new Error('请拖入 TXT、Markdown 或纯文字文件。');
      if(file.size>256*1024)throw new Error('文字文件过大，请使用 256 KB 以内的文件。');
      if(window.draftDesktop) return (await request('/api/import-text',{path:diskPath(file),field})).value;
      const bytes=await file.arrayBuffer();let text;
      try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch(_){text=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}
      if(text.includes('\0'))throw new Error('文件不是可读取的纯文字。');
      return field==='title'?text.split(/\r?\n/).find(line=>line.trim())?.trim()||'':text.replace(/^\uFEFF/,'').trim();
    }
    async function applyText(field,read) {
      const sequence=++textRequest[field],version=fieldVersion[field],videoVersion=sourceVersion,previous=$(field).value;
      const text=await read();
      if(sequence!==textRequest[field] || version!==fieldVersion[field] || videoVersion!==sourceVersion || $(field).value!==previous){notice('导入期间素材已修改，保留了你的最新编辑。');return;}
      $(field).value=field==='title'?String(text).split(/\r?\n/).find(line=>line.trim())?.trim()||'':String(text);
      $(field).dispatchEvent(new Event('input',{bubbles:true}));
      notice('');
    }
    for(const field of ['title','description']) {
      $(field).addEventListener('input',()=>fieldVersion[field]++);
      dropZone(document.querySelector(`[data-text-drop="${field}"]`),data=>applyText(field,()=>data.files.length?readFile(data.files[0],field):Promise.resolve(data.getData('text/plain'))),true);
      document.querySelector(`[data-text-pick="${field}"]`).addEventListener('click',async()=>{
        const version=sourceVersion;
        try { const path=await pick('text');if(path&&version===sourceVersion)await applyText(field,async()=>(await request('/api/import-text',{path,field})).value); } catch(error) { if(version===sourceVersion)fail(error); }
      });
    }
    async function importCover(which,path) {
      const sequence=++coverVersion[which],videoVersion=sourceVersion;
      if(!IMAGE.test(path))throw new Error('封面支持 JPG、PNG 和 WebP 图片。');
      const registered=await request('/api/cover',{path});
      if(sequence!==coverVersion[which] || videoVersion!==sourceVersion)return;
      setCover(which,registered,{generated:false});notice('');
    }
    function attachCoverActions(which,slot) {
      if(!window.CoverActions?.attach)return;
      window.CoverActions.attach({
        element:slot,
        getPath:()=>getMaterial()?.[coverKey[which]]||'',
        copy:path=>{
          if(!window.draftDesktop?.copyCover)throw new Error('当前环境不支持复制封面图片。');
          return window.draftDesktop.copyCover(path);
        },
        notice,
        onDropPath:(_host,path)=>importCover(which,path),
      });
    }
    for(const which of Object.keys(coverKey)) {
      const slot=document.querySelector(`[data-cover-slot="${which}"]`);
      attachCoverActions(which,slot);
      dropZone(slot,async data=>{if(data.files.length)await importCover(which,diskPath(data.files[0]));});
      slot.querySelector('[data-cover-pick]').addEventListener('click',async()=>{const version=sourceVersion;try{const path=await pick('cover');if(path&&version===sourceVersion)await importCover(which,path);}catch(error){if(version===sourceVersion)fail(error);}});
      slot.querySelector('[data-cover-clear]').addEventListener('click',()=>{coverVersion[which]++;setCover(which,'');notice('');});
    }
    function controls() {
      seek.disabled=time.disabled=!(duration>0);
      capture.disabled=!(source&&duration>0)||capturing;
      seek.max=String(Math.max(0,duration-.001));time.max=seek.max;
      durationLabel.textContent='/ '+duration.toFixed(2)+' 秒';
    }
    function updateTime(value) {
      selectedTime=Math.max(0,Math.min(duration?duration-.001:0,Number(value)||0));
      if(!draggingSeek)seek.value=String(selectedTime);
      if(document.activeElement!==time)time.value=selectedTime.toFixed(2);
    }
    function seekTo(value) {
      const n=Number(value);if(!Number.isFinite(n))return;
      selectedTime=Math.max(0,Math.min(duration-.001,n));seek.value=String(selectedTime);time.value=selectedTime.toFixed(2);
      try {
        player.currentTime=selectedTime;
        seekPending=!(Number.isFinite(player.currentTime)&&Math.abs(player.currentTime-selectedTime)<.001);
      } catch(_) { seekPending=true; /* ffmpeg can still capture a valid source time. */ }
    }
    seek.addEventListener('pointerdown',()=>{draggingSeek=true;});
    document.addEventListener('pointerup',()=>{draggingSeek=false;});
    seek.addEventListener('input',()=>seekTo(seek.value));
    time.addEventListener('change',()=>seekTo(time.value));
    player.addEventListener('timeupdate',()=>{if(seekPending)return;if(Number.isFinite(player.currentTime))updateTime(player.currentTime);});
    player.addEventListener('seeked',()=>{seekPending=false;if(Number.isFinite(player.currentTime))updateTime(player.currentTime);});
    player.addEventListener('loadedmetadata',()=>{
      if(source&&Number.isFinite(player.duration)&&player.duration>0){duration=player.duration;controls();}
    });
    player.addEventListener('error',()=>{if(source)status.textContent='此编码暂时无法在播放器预览，可按秒数截帧；也可重新导出 H.264 MP4。';});
    function restrictRatios() {
      const allowed=role.value==='landscape'?['16:9','4:3','original']:role.value==='portrait'?['3:4','9:16','original']:['original','16:9','4:3','3:4','9:16'];
      for(const option of ratio.options)option.disabled=!allowed.includes(option.value);
      ratio.value=allowed[0];
    }
    role.addEventListener('change',restrictRatios);restrictRatios();
    capture.addEventListener('click',async()=>{
      if(capturing||!source||!duration)return;
      const video=source,version=sourceVersion,which=role.value,coverSeq=coverVersion[which],sequence=++captureRequest;
      const live=Number.isFinite(player.currentTime)&&!player.error?player.currentTime:selectedTime;
      const timestamp=seekPending?selectedTime:live;
      capturing=true;controls();status.textContent='正在截取 '+timestamp.toFixed(2)+' 秒的画面…';notice('');
      try {
        const frame=await request('/api/frame',{path:video,time:timestamp,role:which,ratio:ratio.value});
        if(sequence!==captureRequest)return;
        if(version!==sourceVersion || coverSeq!==coverVersion[which]){status.textContent='素材已改变，保留了当前选择。';return;}
        coverVersion[which]++;setCover(which,frame.path,{generated:true,frame});
        status.textContent='已截取 '+frame.time.toFixed(2)+' 秒 · '+frame.width+' × '+frame.height+'，用作'+{common:'通用',landscape:'横版',portrait:'竖版'}[which]+'封面。';
      } catch(error) {if(sequence===captureRequest){status.textContent='截帧未完成，原素材和封面已保留。';fail(error);}} finally {if(sequence===captureRequest){capturing=false;controls();}}
    });
    function update(material) {
      for(const which of Object.keys(coverKey)) {
        const slot=document.querySelector(`[data-cover-slot="${which}"]`),path=material[coverKey[which]]||'';
        if(Object.prototype.hasOwnProperty.call(shownCover,which)&&shownCover[which]!==path)coverVersion[which]++;
        shownCover[which]=path;
        const img=slot.querySelector('img');img.hidden=!path;
        slot.classList.toggle('has-cover',!!path);
        if(path){const url='/asset?path='+encodeURIComponent(path);if(img.getAttribute('src')!==url)img.src=url;}else img.removeAttribute('src');
        const frame=material.cover_frames?.[which];
        const fromFrame=frame?.path===path&&Number.isFinite(frame.time)&&Number.isFinite(frame.width)&&Number.isFinite(frame.height);
        slot.querySelector('.cover-caption').textContent=path?(fromFrame?frame.time.toFixed(2)+' 秒的视频帧 · '+frame.width+' × '+frame.height:/^frame-(common|landscape|portrait)-[a-f0-9]{32}\.jpg$/.test(basename(path))?'视频截取的封面':basename(path)+(which==='common'&&material.cover_generated?' · 视频帧':'')):'拖入图片或截取视频帧';
        slot.querySelector('[data-cover-clear]').disabled=!path;
      }
      const next=material.video||'';if(next===source)return;
      source=next;const version=++sourceVersion;duration=selectedTime=0;seekPending=false;draggingSeek=false;seek.value=time.value='0';controls();
      player.pause();player.hidden=!source;$('videoEmpty').hidden=!!source;
      status.textContent='横竖比例可选；调整比例时保留完整画面，余白用模糊背景填充。';
      if(!source){player.removeAttribute('src');player.load();return;}
      player.src='/media?path='+encodeURIComponent(source);player.load();
      request('/api/media-info',{path:source}).then(info=>{if(version!==sourceVersion)return;duration=Number(info.duration)||0;controls();}).catch(error=>{if(version===sourceVersion){status.textContent='视频信息读取失败，请重新导入。';fail(error);}});
    }
    function reset() {
      sourceVersion++;captureRequest++;capturing=false;
      for(const which of Object.keys(coverKey))coverVersion[which]++;
      for(const field of Object.keys(fieldVersion)){fieldVersion[field]++;textRequest[field]++;}
      duration=selectedTime=0;seekPending=draggingSeek=false;seek.value=time.value='0';
      update({});controls();
    }
    controls();
    return {update,reset};
  }
  window.MaterialEditor={mount};
})();

"""Read Douyin's current visible video component without calling its APIs."""
import asyncio
import json


DY_VISIBLE_VIDEO_SCRIPT = r"""(() => {
 const wanted=WANTED;
 if(window!==top||location.origin!=='https://creator.douyin.com'||
    location.pathname!=='/creator-micro/content/post/video'||!Array.isArray(wanted)||
    !wanted.length||wanted.some(v=>typeof v!=='string'||!v))return null;
 const visible=e=>{const b=e.getBoundingClientRect(),s=getComputedStyle(e);
   return e.isConnected&&b.width>0&&b.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 const videos=[...document.querySelectorAll('video')].filter(visible);
 // The creator shell and the editor mount distinct React roots named root.
 // As in the SDK observer, inspect committed roots and require one host match.
 const roots=[...document.querySelectorAll('[id="root"]')];
 if(videos.length!==1||!roots.length||roots.length>8)return null;
 const video=videos[0],queue=[],rootSet=new Set(),seen=new Set(),hosts=[];
 for(const container of roots){
   const keys=Object.getOwnPropertyNames(container).filter(k=>k.startsWith('__reactContainer$'));
   if(keys.length!==1)continue;
   const anchor=container[keys[0]],current=anchor?.stateNode?.current;
   if(anchor?.tag!==3||current?.tag!==3||current.stateNode!==anchor.stateNode||
      current.stateNode.containerInfo!==container||rootSet.has(current))continue;
   rootSet.add(current);queue.push({node:current,parents:[]});
 }
 if(!queue.length)return null;
 while(queue.length){
   if(seen.size>=4096)return null;
   const {node,parents}=queue.shift();if(!node||seen.has(node))continue;seen.add(node);
   if(node.tag===5&&node.stateNode===video)hosts.push({node,parents});
   if(node.child)queue.push({node:node.child,parents:[node,...parents]});
   if(node.sibling)queue.push({node:node.sibling,parents});
 }
 if(hosts.length!==1)return null;
 const expectedKeys=['currentVideo','width','height','playUrl'].sort();
 const parents=hosts[0].parents.filter(f=>f.tag===0&&f.memoizedProps&&
   JSON.stringify(Object.keys(f.memoizedProps).sort())===JSON.stringify(expectedKeys));
 if(parents.length!==1)return null;
 const props=parents[0].memoizedProps,media=props.currentVideo;
 const src=video.getAttribute('src'),url=video.currentSrc;
 if(!media||typeof media!=='object'||typeof media.vid!=='string'||!wanted.includes(media.vid)||
    typeof props.playUrl!=='string'||!props.playUrl||props.playUrl!==src||url!==src||
    video.readyState<2||video.videoWidth<=0||video.videoHeight<=0||
    !Number.isFinite(video.duration)||video.duration<=0)return null;
 try{if(new URL(src).protocol!=='https:')return null;}catch(_){return null;}
 return {source:'normal_visible_video_component',video_count:1,video_ids:[media.vid]};
})()"""


async def read_dy_visible_video(page, video_ids):
    from uploader.draft_evidence import evaluate_main
    if not isinstance(video_ids, list) or not video_ids:
        return None
    script = DY_VISIBLE_VIDEO_SCRIPT.replace('WANTED', json.dumps(video_ids))
    try:
        result = await evaluate_main(page.main_frame, script)
        if result:
            return result
        # Saving a cover selects the cover/title preview. Return through the
        # normal unique video-preview tab, then keep the exact component/ID,
        # HTTPS URL and decoded-media checks unchanged.
        if not page.url.startswith('https://creator.douyin.com/creator-micro/content/post/video'):
            return None
        tabs = page.main_frame.get_by_text('预览视频', exact=True)
        visible_tabs = [tab for tab in await tabs.all() if await tab.is_visible()]
        if len(visible_tabs) != 1:
            return None
        await visible_tabs[0].click(timeout=5000)
        for _ in range(20):
            result = await evaluate_main(page.main_frame, script)
            if result:
                return result
            await asyncio.sleep(.25)
        return None
    except Exception:
        return None

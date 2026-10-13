"""Read the Douyin editor's visible cover cards from its committed React tree.

This reader does not resolve cover URIs, call platform endpoints, or infer an
identity from a CDN URL. The live React cover-card prop is the identity source;
the descendant Image prop and DOM image must agree on the same HTTPS src.

Frozen source contract (official-three, zero-based byte offsets):
  dy_async_7006.js SHA-256 f8608ff8b1689fe74caf54025ae5c7c50effe113a97923deae7ff9ec0b5d77df
    104500: cover card receives coverInfo, disabled, handleEditCover,
            durationStr, showOperation, className; reads coverInfo.uri.
    104893-105348: non-http uri uses sW(uri), then passes resolved value as
                  Image src.
  dy_content_main.js SHA-256 1d11fe80cc8ee374765b51d9969e56b9d3e8a5d7ca501ec939d229989823a9fc
    115460: sW returns http input as-is, otherwise maps uri via creator get/url.
  dy_async_8261.js SHA-256 2f5d2251e9ecb07520fd553148b1971faecd9f2f19900fccad260771425655d5
    381626: draft recovery hydrates coverInfoAtom from video.cover.cover_uri.

Current dual-cover contract (also preserved under dy-current-cover):
  2209.f96c1c3b.js SHA-256 a3e25f67ca0d7b528d82fb2d7dc7241bc6263fe44538a890a3298825dc7ce6f1
    21206: card renders coverUrl as Image src and direction/tip as its slot.
    31300: CoverEditorWithRecommend receives coverUri/coverHorizontalUri;
    34300: reads coverUrl/coverHorizontalUrl atoms, then renders h/v cards.
    44486,45595: horizontal/vertical card URL branches respectively.
  8261.7a5e196e.js: 386793,387291: normal cover effects write the resolved
    URL into BOTH the corresponding URL atom and {uri,url} info atom.

For this contract, a raw URI must match the live info atom's URI, and its
paired URL must match the live URL atom, card prop, Image props and loaded DOM
image. Only committed hooks are read; store getters/resolvers are never called.
"""
import asyncio
import json
import re


DY_VISIBLE_COVER_CARDS_SCRIPT = r"""(() => {
 const wanted=WANTED;
 if(window!==top||location.protocol!=='https:'||location.origin!=='https://creator.douyin.com'||
    location.pathname!=='/creator-micro/content/post/video'||!Array.isArray(wanted)||
    !wanted.length||wanted.length>64||wanted.some(v=>typeof v!=='string'||!v||v.length>8192||/[\u0000-\u0020\u007f?#]/.test(v)))return null;
 const safeUri=value=>typeof value==='string'&&value.length>0&&value.length<=8192&&
   !/[\u0000-\u0020\u007f?#]/.test(value)&&
   (value.startsWith('tos://')?value.length>6&&!/^[a-z][a-z0-9+.-]*:/i.test(value.slice(6)):
    !/^[a-z][a-z0-9+.-]*:/i.test(value)&&!value.startsWith('//'));
 const uriId=value=>value.startsWith('tos://')?value.slice(6):value;
 const aliases=value=>[...new Set([value,uriId(value),'tos://'+uriId(value)])];
 const wantedIds=new Set(wanted.filter(safeUri).map(uriId));
 if(!wantedIds.size||wanted.some(v=>!safeUri(v)))return null;
 const visible=e=>{if(!e||!e.isConnected)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);
   return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)>0;};
 const roots=[...document.querySelectorAll('[id="root"]')];
 if(!roots.length||roots.length>16)return null;
 const trees=[],globalSeen=new Set();let totalNodes=0;
 for(const root of roots){
   const keys=Object.getOwnPropertyNames(root).filter(k=>k.startsWith('__reactContainer$'));
   if(keys.length!==1)return null;
   const anchor=root[keys[0]],fiberRoot=anchor?.stateNode,current=fiberRoot?.current;
   if(anchor?.tag!==3||current?.tag!==3||current.stateNode!==fiberRoot)return null;
   const queue=[{node:current,parent:null}],seen=new Set(),parent=new Map(),nodes=[];
   while(queue.length){
     const item=queue.shift(),node=item.node;
     if(!node||typeof node!=='object'||seen.has(node))continue;
     if(totalNodes>=4096||globalSeen.has(node))return null;
     seen.add(node);globalSeen.add(node);totalNodes++;parent.set(node,item.parent);nodes.push(node);
     if(node.child)queue.push({node:node.child,parent:node});
     if(node.sibling)queue.push({node:node.sibling,parent:item.parent});
   }
   trees.push({current,nodes,parent});
 }
 const expected=['className','coverInfo','disabled','durationStr','handleEditCover','showOperation'].sort();
 const exactKeys=value=>value&&typeof value==='object'&&
   JSON.stringify(Object.keys(value).sort())===JSON.stringify(expected);
 const cards=[];
 for(const tree of trees)for(const node of tree.nodes){
   if(node.tag!==0||!exactKeys(node.memoizedProps))continue;
   const props=node.memoizedProps,info=props.coverInfo,uri=info&&info.uri;
   if(typeof props.handleEditCover!=='function'||typeof props.disabled!=='boolean'||
      typeof props.durationStr!=='string'||typeof props.showOperation!=='boolean'||
      typeof props.className!=='string'||!info||typeof info!=='object'||!safeUri(uri)||
      !wantedIds.has(uriId(uri)))continue;
   cards.push({fiber:node,uri,info,tree});
 }
 // The current CoverEditorWithRecommend no longer passes coverInfo to each
 // card. Read the platform's independently resolved {uri,url} atom value,
 // bound to the same current Recoil store as the visible card's URL atom.
 const widgetKeys=['algorithmStrategy','coverHorizontalUri','coverStatus','coverUri','coverVideoName',
   'defaultUri','from','isKeyAuthor','isMainReady','isModify','isReedit','isSelect','longFileInfo',
   'onCoverEditFinish','onDouyinCoverEdit','setLog','setRecommendCoverInfo','videoEventParams'].sort();
 const cardKeys=['canEdit','coverUrl','customStyle','direction','disabled','height','isLoading',
   'onClick','showSelect','tip','tooltipOptions'].sort();
 const supplierKeys=['abTestVal','algorithmStrategy','coverEdited','coverRequired','coverUrl','defaultUri',
   'editorsetLog','from','isKeyAuthor','isModify','isReedit','longFileInfo','onCoverEditFinish',
   'onDouyinCoverEdit','setRecommendCoverInfo'].sort();
 const routedSupplierKeys=[...supplierKeys,'history','location','match','staticContext'].sort();
 const keysEqual=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&
   JSON.stringify(Object.keys(value).sort())===JSON.stringify(keys);
 const https=value=>{try{const u=new URL(value);return typeof value==='string'&&u.protocol==='https:'&&
   !!u.hostname&&!u.username&&!u.password;}catch(_){return false;}};
 for(const tree of trees){
   const widgets=tree.nodes.filter(node=>node.tag===0&&node.memoizedState&&keysEqual(node.memoizedProps,widgetKeys));
   if(widgets.length>1)return null;
   if(!widgets.length)continue;
   const widget=widgets[0],props=widget.memoizedProps;
   const suppliers=[];
   for(let node=tree.parent.get(widget),depth=0;node;node=tree.parent.get(node),depth++){
     if(depth>=64)return null;
     if(node.tag===0&&node.memoizedState&&
        (keysEqual(node.memoizedProps,supplierKeys)||keysEqual(node.memoizedProps,routedSupplierKeys))){
       suppliers.push(node);break;
     }
   }
   if(suppliers.length!==1)return null;
   // The modify branch substitutes firstFrameCoverUri. The normal publish
   // selector also mounts this widget with isReedit=false when a real file
   // is present (2209:63520); requiring true rejects valid restored videos.
   if(props.isModify!==false||suppliers[0].memoizedProps.isModify!==false||
      typeof props.isReedit!=='boolean')return null;
   if(!props.isReedit&&(!props.longFileInfo||typeof props.longFileInfo.name!=='string'||
      !props.longFileInfo.name||props.longFileInfo.name==='没有文件'||
      typeof props.longFileInfo.size!=='number'||props.longFileInfo.size<=0))return null;
   const atoms=[];
   for(const node of [widget,suppliers[0]]){
     let hook=node.memoizedState,index=0;const seen=new Set();
     for(;hook;hook=hook.next,index++){
       if(index>=768||seen.has(hook))return null;
       seen.add(hook);
       const effect=hook.memoizedState,deps=effect?.deps;
       if(!Array.isArray(deps)||deps.length!==3||typeof effect.create!=='function'||
          !['coverInfo','coverHorizontalInfo','coverUrl','coverHorizontalUrl'].includes(deps[1]?.key))continue;
       const ref=hook.next?.memoizedState,loadable=ref?.current,store=deps[2]?.current;
       if(!keysEqual(ref,['current'])||!keysEqual(loadable,['contents','state'])||
          !keysEqual(store,['addTransactionMetadata','getGraph','getState','replaceState','subscribeToTransactions'])||
          Object.values(store).some(value=>typeof value!=='function'))return null;
       atoms.push({node,key:deps[1].key,store,loadable});
     }
   }
   if(typeof props.onCoverEditFinish!=='function'||typeof props.onDouyinCoverEdit!=='function'||
      typeof props.setLog!=='function'||typeof props.setRecommendCoverInfo!=='function')return null;
   const widgetAtoms=atoms.filter(atom=>atom.node===widget);
   const urls=widgetAtoms.filter(atom=>['coverUrl','coverHorizontalUrl'].includes(atom.key));
   if(urls.length!==2||new Set(urls.map(atom=>atom.key)).size!==2||urls[0].store!==urls[1].store)return null;
   const store=urls[0].store;
   const verticalUrl=urls.find(atom=>atom.key==='coverUrl').loadable.contents;
   const horizontalUrl=urls.find(atom=>atom.key==='coverHorizontalUrl').loadable.contents;
   // The source declares Y/em as two adjacent useState cells. Both must have
   // caught up to their independently resolved URL atoms before accepting the
   // rendered em||Y / Y||em card branches.
   const statePairs=[];
   for(let hook=widget.memoizedState;hook;hook=hook.next){
     if(hook.memoizedState===verticalUrl&&hook.next?.memoizedState===horizontalUrl&&
        hook.queue?.lastRenderedState===verticalUrl&&hook.next.queue?.lastRenderedState===horizontalUrl&&
        typeof hook.queue.dispatch==='function'&&typeof hook.next.queue.dispatch==='function')statePairs.push(hook);
   }
   if(!https(verticalUrl)||!https(horizontalUrl)||verticalUrl===horizontalUrl||statePairs.length!==1)return null;
   const isDescendant=(node,ancestor)=>{let cursor=node;while(cursor){if(cursor===ancestor)return true;cursor=tree.parent.get(cursor)||null;}return false;};
   for(const slot of [
     {direction:'v',label:'竖封面3:4',uriKey:'coverUri',infoKey:'coverInfo',urlKey:'coverUrl'},
     {direction:'h',label:'横封面4:3',uriKey:'coverHorizontalUri',infoKey:'coverHorizontalInfo',urlKey:'coverHorizontalUrl'}
   ]){
     const uri=props[slot.uriKey];
     if(!safeUri(uri)||!wantedIds.has(uriId(uri)))return null;
     const infos=atoms.filter(atom=>atom.store===store&&atom.key===slot.infoKey);
     const urlAtoms=atoms.filter(atom=>atom.store===store&&atom.key===slot.urlKey);
     if(!infos.length||!urlAtoms.length||infos.some(atom=>atom.loadable.state!=='hasValue')||
        urlAtoms.some(atom=>atom.loadable.state!=='hasValue'))return null;
     const info=infos[0].loadable.contents,url=urlAtoms[0].loadable.contents;
     if(!info||typeof info!=='object'||Array.isArray(info)||info.uri!==uri||info.url!==url||!https(url)||
        infos.some(atom=>atom.loadable.contents!==info)||urlAtoms.some(atom=>atom.loadable.contents!==url))return null;
     const children=tree.nodes.filter(node=>node.tag===0&&isDescendant(node,widget)&&
       keysEqual(node.memoizedProps,cardKeys)&&node.memoizedProps.direction===slot.direction);
     if(children.length!==1)return null;
     const child=children[0],p=child.memoizedProps;
     if(p.tip!==slot.label||p.coverUrl!==url||p.disabled!==false||p.isLoading!==false||
        typeof p.canEdit!=='boolean'||typeof p.showSelect!=='boolean'||typeof p.onClick!=='function'||
        typeof p.height!=='number'||p.height<=0||!p.customStyle||typeof p.customStyle!=='object'||
        !p.tooltipOptions||typeof p.tooltipOptions!=='object')return null;
     cards.push({fiber:child,uri,info,tree,expectedUrl:url,expectedSlot:slot.direction==='v'?'cover_portrait':'cover_landscape'});
   }
 }
 if(!cards.length)return null;
 const proofs=[],seenIds=new Set();
 for(const card of cards){
   const id=uriId(card.uri);
   if(seenIds.has(id))return null;
   seenIds.add(id);
   const isDescendant=(node,ancestor)=>{let cursor=node;while(cursor){if(cursor===ancestor)return true;cursor=card.tree.parent.get(cursor)||null;}return false;};
   const images=[];
   for(const node of card.tree.nodes){
     if(node.tag!==5||!isDescendant(node,card.fiber))continue;
     const image=node.stateNode;
     if(image?.localName==='img'&&visible(image))images.push({fiber:node,image});
   }
   if(images.length!==1)return null;
   const {fiber:imageFiber,image}=images[0],src=image.getAttribute('src');
   if(typeof src!=='string'||!src||image.currentSrc!==src||image.complete!==true||
      image.naturalWidth<=0||image.naturalHeight<=0)return null;
   if(card.expectedUrl&&src!==card.expectedUrl)return null;
   try{const parsed=new URL(src);if(parsed.protocol!=='https:'||!parsed.hostname||parsed.username||parsed.password)return null;}
   catch(_){return null;}
   const sourceProps=[];
   for(const node of card.tree.nodes){
     if(node===imageFiber||node.tag===5||!isDescendant(imageFiber,node)||!isDescendant(node,card.fiber))continue;
     const props=node.memoizedProps;
     if(props&&typeof props==='object'&&Object.prototype.hasOwnProperty.call(props,'src')&&typeof props.src==='string')
       sourceProps.push(props.src);
   }
   // Semi.Image uses nested forwardRef components. Each src along this
   // committed ancestry must agree; a second equal src is not a second image.
   if(!sourceProps.length||sourceProps.length>4||sourceProps.some(value=>value!==src))return null;
   const control=image.closest('[class^="coverControl-"]');
   if(!control)return null;
   const labels=[...control.querySelectorAll('*')].filter(e=>!e.children.length&&visible(e)&&
     ['横封面4:3','竖封面3:4'].includes(e.textContent));
   if(labels.length!==1)return null;
   const slot=labels[0].textContent==='竖封面3:4'?'cover_portrait':'cover_landscape';
   if(card.expectedSlot&&slot!==card.expectedSlot)return null;
   proofs.push({uri:card.uri,ids:aliases(card.uri),slot,image_match:true,image_count:1,
                image_width:image.naturalWidth,image_height:image.naturalHeight});
 }
 const proofIds=proofs.map(p=>uriId(p.uri));
 if(new Set(proofIds).size!==proofIds.length)return null;
 return {source:'normal_visible_cover_cards',cover_count:proofs.length,covers:proofs};
})()"""


def _cache_uri(value):
    """Accept only the raw cache URI spellings covered by the Douyin source."""
    return (isinstance(value, str) and 0 < len(value) <= 8192 and
            not re.search(r'[\x00-\x20\x7f?#]', value) and
            (value.startswith('tos://') and len(value) > 6 or
             not re.match(r'^[a-z][a-z0-9+.-]*:', value, re.I) and not value.startswith('//')))


async def read_dy_visible_cover_cards(page, cover_uris):
    """Return exact live cover-card URIs whose current image is visibly bound.

    ``cover_uris`` must come from the saved editor cache (including only its
    URI spellings or the corresponding bare ``tos://`` aliases). Returned IDs
    are derived from the exact current React ``coverInfo.uri``; candidate URLs
    are never copied or correlated by inspecting a CDN URL.
    """
    from uploader.draft_evidence import evaluate_main

    if (not isinstance(cover_uris, (list, tuple)) or not cover_uris or len(cover_uris) > 64 or
            any(not _cache_uri(value) for value in cover_uris)):
        return None
    try:
        script = DY_VISIBLE_COVER_CARDS_SCRIPT.replace('WANTED', json.dumps(list(cover_uris), ensure_ascii=False))
        first = await evaluate_main(page.main_frame, script)
        if not first:
            return None
        # Loadable refs are synchronized by passive React effects. Require a
        # second committed read instead of accepting one transitional sample.
        await asyncio.sleep(.15)
        second = await evaluate_main(page.main_frame, script)
        return first if first == second else None
    except Exception:
        return None

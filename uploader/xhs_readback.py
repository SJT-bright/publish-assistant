"""Atomic, read-only XHS editor projection in the canonical root CDP context.

No selection, keyboard, SDK, navigation, or save operation is performed. The
projection returns fixed booleans/counts and a fixed reason enum, never
descriptions, source URLs, or resource IDs.
"""
import copy
import json
from urllib.parse import urlsplit

from uploader.draft_evidence import field_match
from uploader.native_read import canonical_root_evaluate, NativeReadError
from uploader.xiaohongshu_uploader.main import (XHS_DRAFT_EDITOR_SELECTION,
                                               draft_xhs_description_plan)


_read_prefix, _marker, _ = XHS_DRAFT_EDITOR_SELECTION.partition(
    "    if(!request || request.mode==='read')")
if not _marker:
    raise RuntimeError('XHS canonical editor read boundary missing')
_CANONICAL_READ = _read_prefix + '    return {plain,serialized,topics};\n}'

# Diagnostic projection only. A Blob is not accepted by the production asset
# predicate until the actual normal editor's ownership contract is established.
XHS_VIDEO_OWNER_ABORT_REASON_CODES = frozenset({
    'none', 'document_scope', 'container_scope', 'visible_video_scope',
    'duplicate_vnode', 'visit_limit', 'depth_limit', 'component_link_invalid',
    'children_limit', 'source_consumer_missing', 'source_consumer_ambiguous',
    'video_vnode_present', 'ancestor_state_unbound', 'projection_exception',
})

XHS_VIDEO_OWNER_PROJECTION = r"""input => {
 const out={valid:false,abort_reason_code:'none',publisher_root_unique:false,publisher_element_owned:false,
  publisher_app_present:false,publisher_container_owned:false,container_scope_valid:false,root_vnode_present:false,
  container_own_property:false,container_data_property:false,container_getter_property:false,
  container_node_type:0,container_connected:false,container_native_owner_root_document:false,
  container_body_contains:false,container_contains_publisher:false,publisher_contains_container:false,
  container_parent_is_publisher:false,container_vue_app_equal:false,container_vnode_data_property:false,
  vnode_component_present:false,vnode_subtree_present:false,vnode_el_inside_container:false,
  subtree_el_inside_container:false,app_instance_present:false,app_instance_data_property:false,
  app_instance_getter_property:false,instance_el_inside_container:false,
  visible_video_count:0,blob_video_count:0,visited_vnode_count:0,exact_video_vnode_count:0,
  duplicate_vnode_count:0,duplicate_vnode_cycle_count:0,duplicate_vnode_shared_reference_count:0,
  duplicate_vnode_depth:0,duplicate_component_vnode_count:0,duplicate_player_scope_vnode_count:0,
  duplicate_video_vnode_count:0,duplicate_static_vnode_count:0,duplicate_teleport_vnode_count:0,
  duplicate_suspense_vnode_count:0,component_link_invalid_count:0,component_unmounted_count:0,
  component_vnode_link_mismatch_count:0,component_subtree_missing_count:0,
  ancestor_component_count:0,dom_owned_component_count:0,app_owned_component_count:0,
  source_consumer_count:0,component_root_contains_video_count:0,player_scope_component_count:0,
  player_src_equal_count:0,player_src_blob_equal_count:0,player_src_candidate_id_equal_count:0,
  player_exposed_video_equal_count:0,player_sdk_instance_count:0,
  player_sdk_config_url_equal_count:0,player_sdk_options_url_equal_count:0,
  player_sdk_current_config_url_equal_count:0,player_sdk_current_options_url_equal_count:0,
  player_sdk_options_file_url_equal_count:0,player_sdk_current_options_file_url_equal_count:0,
  player_sdk_url_equal_count:0,static_vnode_count:0,
  teleport_vnode_count:0,suspense_vnode_count:0,suspense_active_branch_count:0,
  state_component_count:0,state_video_ids_equal_count:0,
  state_blob_src_equal_count:0,state_server_src_equal_count:0,
  state_server_prop_src_equal_count:0,state_server_resource_id_equal_count:0,state_title_equal_count:0,
  state_description_equal_count:0,fully_bound_blob_video_count:0};
 try {
  if(self!==top||location.origin!=='https://creator.xiaohongshu.com'||
      location.pathname!=='/publish/publish'){out.abort_reason_code='document_scope';return out;}
  const roots=document.querySelectorAll('#creator-publish-dom'),publisher=window.publish;
  out.publisher_root_unique=roots.length===1;
  out.publisher_element_owned=roots.length===1&&publisher?.el===roots[0]&&roots[0].isConnected;
  out.publisher_app_present=!!publisher?.app;
  const descriptor=(o,key)=>o&&(typeof o==='object'||typeof o==='function')?Object.getOwnPropertyDescriptor(o,key):undefined;
  const data=(o,key)=>{const d=descriptor(o,key);return d&&'value' in d?d.value:undefined;};
  const containerDescriptor=descriptor(publisher?.app,'_container'),
        container=data(publisher?.app,'_container'),owner=Object.getOwnPropertyDescriptor(Node.prototype,'ownerDocument').get,
        connected=Object.getOwnPropertyDescriptor(Node.prototype,'isConnected').get,
        parent=Object.getOwnPropertyDescriptor(Node.prototype,'parentNode').get,contains=Node.prototype.contains;
  const call=(fn,value,...args)=>Reflect.apply(fn,value,args),node=value=>value instanceof Node;
  out.container_own_property=!!containerDescriptor;
  out.container_data_property=!!containerDescriptor&&'value' in containerDescriptor;
  out.container_getter_property=typeof containerDescriptor?.get==='function';
  if(node(container)){
   out.container_node_type=container.nodeType;
   out.container_connected=call(connected,container);
   out.container_native_owner_root_document=call(owner,container)===document;
   out.container_body_contains=!!document.body&&call(contains,document.body,container);
   out.container_contains_publisher=out.publisher_element_owned&&call(contains,container,roots[0]);
   out.publisher_contains_container=out.publisher_element_owned&&call(contains,roots[0],container);
   out.container_parent_is_publisher=out.publisher_element_owned&&call(parent,container)===roots[0];
   out.container_vue_app_equal=data(container,'__vue_app__')===publisher?.app;
  }
  out.publisher_container_owned=out.publisher_element_owned&&container===roots[0];
  const vnodeDescriptor=descriptor(container,'_vnode'),root=data(container,'_vnode'),component=data(root,'component'),subTree=data(component,'subTree');
  out.container_vnode_data_property=!!vnodeDescriptor&&'value' in vnodeDescriptor;
  out.vnode_component_present=!!component&&typeof component==='object';
  out.vnode_subtree_present=!!subTree&&typeof subTree==='object';
  out.root_vnode_present=out.vnode_component_present&&out.vnode_subtree_present;
  const inContainer=el=>node(el)&&node(container)&&call(contains,container,el);
  out.vnode_el_inside_container=inContainer(data(root,'el'));
  out.subtree_el_inside_container=inContainer(data(subTree,'el'));
  const instanceDescriptor=descriptor(publisher?.app,'_instance'),instance=data(publisher?.app,'_instance');
  out.app_instance_present=!!instance&&typeof instance==='object';
  out.app_instance_data_property=!!instanceDescriptor&&'value' in instanceDescriptor;
  out.app_instance_getter_property=typeof instanceDescriptor?.get==='function';
  out.instance_el_inside_container=inContainer(data(data(instance,'subTree'),'el'));
  // Official mountPublisher mounts to one newly created div, then appends that
  // exact Vue container directly to publisher.el (bundle Unicode1277205).
  out.container_scope_valid=out.publisher_element_owned&&out.container_data_property&&
    out.container_node_type===1&&out.container_connected&&out.container_native_owner_root_document&&
    out.container_body_contains&&out.publisher_contains_container&&out.container_parent_is_publisher&&
    !out.container_contains_publisher&&out.container_vue_app_equal&&out.container_vnode_data_property&&
    out.root_vnode_present&&out.vnode_el_inside_container&&out.subtree_el_inside_container;
  if(!out.container_scope_valid){out.abort_reason_code='container_scope';out.valid=true;return out;}
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
    return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const exactResource=(value,ids)=>{
   if(typeof value!=='string'||!value||value.startsWith('blob:'))return false;
   if(ids.includes(value))return true;
   let url;try{url=new URL(value);}catch(_){return false;}
   if(url.protocol!=='https:')return false;
   let path;try{path=decodeURIComponent(url.pathname);}catch(_){path=url.pathname;}
   path=path.replace(/^\/+|\/+$/g,'');
   const segments=path?path.split('/'):[],queryValues=[...new URLSearchParams(url.search).values()];
   return ids.some(id=>{
    const normalized=id.replace(/^\/+|\/+$/g,'');if(!normalized)return false;
    return normalized.includes('/')?path===normalized:
      segments.includes(normalized)||queryValues.includes(normalized);
   });
  };
  const dataPath=(value,path)=>{
   for(const key of path){const d=descriptor(value,key);if(!d||!('value' in d))return undefined;value=d.value;}
   return value;
  };
  const videos=[...document.querySelectorAll('video')].filter(visible);
  out.visible_video_count=videos.length;if(videos.length!==1||!call(contains,container,videos[0])||call(owner,videos[0])!==document){out.abort_reason_code='visible_video_scope';return out;}
  const video=videos[0],sources=[video.currentSrc,video.getAttribute('src')].filter(v=>typeof v==='string'&&v),
        blobs=sources.filter(v=>v.startsWith('blob:'));
  out.blob_video_count=blobs.length?1:0;
  const queue=[{node:root,branch:[],depth:0,vnodePath:null}],seen=new Map(),paths=[];
  const unref=v=>v&&v.__v_isRef===true?v.value:v;
  const hasVNodeAncestor=(path,target)=>{while(path){if(path.node===target)return true;path=path.parent;}return false;};
  while(queue.length){
   const {node,branch,depth,vnodePath}=queue.pop();
   if(!node||typeof node!=='object')continue;
   if(seen.has(node)){
    out.duplicate_vnode_count++;
    out.duplicate_vnode_depth=depth;
    if(hasVNodeAncestor(vnodePath,node))out.duplicate_vnode_cycle_count++;
    else out.duplicate_vnode_shared_reference_count++;
    if(node.component){
     out.duplicate_component_vnode_count++;
     if(node.type?.__scopeId==='data-v-286252ff')out.duplicate_player_scope_vnode_count++;
    }
    if(node.type==='video'&&node.el===video)out.duplicate_video_vnode_count++;
    if(node.type===Symbol.for('v-stc'))out.duplicate_static_vnode_count++;
    if(node.type?.__isTeleport===true)out.duplicate_teleport_vnode_count++;
    if(node.type?.__isSuspense===true)out.duplicate_suspense_vnode_count++;
    out.abort_reason_code='duplicate_vnode';return out;
   }
   seen.set(node,true);out.visited_vnode_count++;
   if(seen.size>20000){out.abort_reason_code='visit_limit';return out;}
   if(depth>64){out.abort_reason_code='depth_limit';return out;}
   const nextVNodePath={node,parent:vnodePath};
   if(node.type==='video'&&node.el===video)out.exact_video_vnode_count++;
   if(node.type===Symbol.for('v-stc'))out.static_vnode_count++;
   if(node.type?.__isTeleport===true)out.teleport_vnode_count++;
   if(node.type?.__isSuspense===true)out.suspense_vnode_count++;
   if(node.suspense?.activeBranch)out.suspense_active_branch_count++;
   if(node.component){
    const component=node.component;
    if(component.isUnmounted||component.vnode!==node||!component.subTree){
     out.component_link_invalid_count++;
     if(component.isUnmounted)out.component_unmounted_count++;
     if(component.vnode!==node)out.component_vnode_link_mismatch_count++;
     if(!component.subTree)out.component_subtree_missing_count++;
     out.abort_reason_code='component_link_invalid';return out;
    }
    const el=component.subTree.el,
          containsVideo=node?.el===el&&el?.nodeType===1&&call(owner,el)===document&&
            call(connected,el)&&call(contains,container,el)&&call(contains,el,video);
    if(containsVideo)out.component_root_contains_video_count++;
    // Public pinned module992 PublishPlayer renders only this div. On mount
    // its existing player receives props.src and exposes that actual video
    // via the Vue ref. Reading the ref calls no SDK method or business getter.
    const playerScope=containsVideo&&node.type?.__scopeId==='data-v-286252ff'&&
          component.subTree.type==='div'&&el.classList.contains('player');
    const propSrc=unref(component.props?.src),vnodeSrc=unref(node.props?.src),
          propVnodeEqual=playerScope&&typeof propSrc==='string'&&vnodeSrc===propSrc,
          srcBlobEqual=propVnodeEqual&&blobs.includes(propSrc),
          srcResourceEqual=propVnodeEqual&&exactResource(propSrc,input.video_ids),
          exposed=data(component,'exposed'),
          exposedEqual=playerScope&&unref(data(exposed,'video'))===video,
          sdk=unref(data(exposed,'player')),
          sdkConfigEqual=propVnodeEqual&&dataPath(sdk,['config','url'])===propSrc,
          sdkOptionsEqual=propVnodeEqual&&(
            dataPath(sdk,['options','url'])===propSrc||dataPath(sdk,['_options','url'])===propSrc),
          sdkCurrentConfigEqual=propVnodeEqual&&dataPath(sdk,['current','config','url'])===propSrc,
          sdkCurrentOptionsEqual=propVnodeEqual&&(
            dataPath(sdk,['current','options','url'])===propSrc||
            dataPath(sdk,['current','_options','url'])===propSrc),
          sdkOptionsFileEqual=propVnodeEqual&&
            dataPath(sdk,['playerOptionsConfig','fileUrl'])===propSrc,
          sdkCurrentOptionsFileEqual=propVnodeEqual&&
            dataPath(sdk,['current','playerOptionsConfig','fileUrl'])===propSrc,
          sdkUrlValues=[dataPath(sdk,['config','url']),dataPath(sdk,['options','url']),
            dataPath(sdk,['_options','url']),dataPath(sdk,['current','config','url']),
            dataPath(sdk,['current','options','url']),dataPath(sdk,['current','_options','url']),
            dataPath(sdk,['playerOptionsConfig','fileUrl']),
            dataPath(sdk,['current','playerOptionsConfig','fileUrl'])].filter(v=>typeof v==='string'),
          sdkUrlEqual=propVnodeEqual&&sdkUrlValues.length>0&&sdkUrlValues.every(value=>value===propSrc);
    if(playerScope)out.player_scope_component_count++;
    if(propVnodeEqual)out.player_src_equal_count++;
    if(srcBlobEqual)out.player_src_blob_equal_count++;
    if(srcResourceEqual)out.player_src_candidate_id_equal_count++;
    if(exposedEqual)out.player_exposed_video_equal_count++;
    if(sdk)out.player_sdk_instance_count++;
    if(sdkConfigEqual)out.player_sdk_config_url_equal_count++;
    if(sdkOptionsEqual)out.player_sdk_options_url_equal_count++;
    if(sdkCurrentConfigEqual)out.player_sdk_current_config_url_equal_count++;
    if(sdkCurrentOptionsEqual)out.player_sdk_current_options_url_equal_count++;
    if(sdkOptionsFileEqual)out.player_sdk_options_file_url_equal_count++;
    if(sdkCurrentOptionsFileEqual)out.player_sdk_current_options_file_url_equal_count++;
    if(sdkUrlEqual)out.player_sdk_url_equal_count++;
    if(playerScope&&propVnodeEqual&&exposedEqual&&component.appContext?.app===publisher.app){
     out.source_consumer_count++;
     paths.push({branch:[...branch,component],propSrc,propVnodeEqual,srcBlobEqual,
       srcResourceEqual,sdkUrlEqual});
    }
    queue.push({node:component.subTree,branch:[...branch,component],depth:depth+1,vnodePath:nextVNodePath});
   }else if(Array.isArray(node.children)){
    if(node.children.length>2048){out.abort_reason_code='children_limit';return out;}
    for(const child of node.children)if(child&&typeof child==='object')queue.push({node:child,branch,depth:depth+1,vnodePath:nextVNodePath});
   }else if(node.suspense?.activeBranch){
    queue.push({node:node.suspense.activeBranch,branch,depth:depth+1,vnodePath:nextVNodePath});
   }
  }
  if(paths.length!==1||out.exact_video_vnode_count!==0){
   out.abort_reason_code=paths.length===0?'source_consumer_missing':
    paths.length>1?'source_consumer_ambiguous':'video_vnode_present';
   out.valid=true;return out;
  }
  const {branch,propSrc,propVnodeEqual,srcBlobEqual,srcResourceEqual,sdkUrlEqual}=paths[0];
  const consumer=branch[branch.length-1];let bound=false;
  for(let index=branch.length-1;index>=0;index--){
   const component=branch[index];out.ancestor_component_count++;
   const appOwned=component.appContext?.app===publisher.app;
   // This component's current rendered subtree led to the exact video vnode.
   // Fragment roots do not need a synthetic DOM containment projection.
   const domOwned=appOwned&&call(contains,container,video);
   if(domOwned)out.dom_owned_component_count++;if(appOwned)out.app_owned_component_count++;
   const state=component.props?.state;
   if(!state||typeof state!=='object')continue;
   out.state_component_count++;
   const record=unref(state.video),ids=record&&[unref(record.fileid),unref(record.transcodeVideoFileId)].filter(v=>typeof v==='string'&&v);
   const idsEqual=Array.isArray(ids)&&JSON.stringify([...new Set(ids)].sort())===JSON.stringify([...input.video_ids].sort());
   const blob=unref(state.videoBlobUrl),server=unref(state.videoUrl),title=unref(state.title),description=unref(state.desc);
   const blobEqual=typeof blob==='string'&&blob.startsWith('blob:')&&blobs.includes(blob);
   const serverEqual=typeof server==='string'&&server.startsWith('https:')&&sources.includes(server);
   const serverResourceEqual=exactResource(server,input.video_ids),serverPropEqual=serverResourceEqual&&server===propSrc;
   const titleEqual=title===input.title,descriptionEqual=description===input.description;
   if(idsEqual)out.state_video_ids_equal_count++;if(blobEqual)out.state_blob_src_equal_count++;
   if(serverEqual)out.state_server_src_equal_count++;
   if(serverPropEqual)out.state_server_prop_src_equal_count++;
   if(serverResourceEqual)out.state_server_resource_id_equal_count++;
   if(titleEqual)out.state_title_equal_count++;
   if(descriptionEqual)out.state_description_equal_count++;
   const blobSourceBound=propVnodeEqual&&srcBlobEqual;
   const serverSourceBound=propVnodeEqual&&srcResourceEqual&&serverPropEqual&&sdkUrlEqual;
   if(domOwned&&appOwned&&consumer&&idsEqual&&blobEqual&&titleEqual&&descriptionEqual&&
      (blobSourceBound||serverSourceBound))bound=true;
  }
  out.fully_bound_blob_video_count=bound?1:0;out.valid=true;
  if(!bound)out.abort_reason_code='ancestor_state_unbound';return out;
 }catch(_){out.abort_reason_code='projection_exception';return out;}
}"""

XHS_READBACK_PROJECTION = r"""input => {
 const out={valid:false,root_scope_valid:false,title_count:0,title_equal:false,
   current_editor_count:0,current_editor_owned:false,canonical_read_valid:false,
   canonical_plain_equal:false,canonical_serialized_equal:false,canonical_topics_equal:false,
   canonical_topic_count:0,visible_video_count:0,video_exact_id_match_count:0,
   video_blob_count:0,visible_cover_count:0,cover_exact_id_match_count:0,
   cover_background_match_count:0,video_poster_cover_match_count:0,
   fields_ok:false,assets_ok:false};
 if(self!==top || location.origin!=='https://creator.xiaohongshu.com' ||
     location.pathname!=='/publish/publish')return out;
 out.root_scope_valid=true;
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
   return e.isConnected && r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';};
 const matches=(value,resource)=>{
   if(typeof value!=='string'||typeof resource!=='string')return false;
   if(value===resource)return true;
   if(value.startsWith('blob:'))return false;
   let pathname,search;
   try{const url=new URL(value);pathname=url.pathname;search=url.search;}
   catch(_){const noHash=value.split('#',1)[0],q=noHash.indexOf('?');
     pathname=q<0?noHash:noHash.slice(0,q);search=q<0?'':noHash.slice(q);}
   const id=resource.replace(/^\/+|\/+$/g,'');
   if(!id)return false;
   if(id.includes('/'))return pathname.replace(/^\/+|\/+$/g,'')===id;
   return pathname.replace(/^\/+|\/+$/g,'').split('/').includes(id) ||
     [...new URLSearchParams(search).values()].includes(id);
 };
 const exact=(values,ids)=>values.some(value=>ids.some(id=>matches(value,id)));
 const titles=[...document.querySelectorAll('input[placeholder*="标题"],textarea[placeholder*="标题"]')].filter(visible);
 out.title_count=titles.length;out.title_equal=titles.length===1&&titles[0].value===input.title;
 const view=window._tiptap_editor?.view,editor=view?.dom;
 out.current_editor_count=editor&&editor.ownerDocument===document&&editor.isConnected?1:0;
 out.current_editor_owned=out.current_editor_count===1&&editor.isContentEditable&&visible(editor)&&!!view.docView;
 if(out.current_editor_owned){
   try{
     const readCanonical=CANONICAL_READ;
     const snapshot=readCanonical(editor,{mode:'read'});
     out.canonical_read_valid=typeof snapshot.plain==='string'&&typeof snapshot.serialized==='string'&&Array.isArray(snapshot.topics);
     if(out.canonical_read_valid){
       out.canonical_plain_equal=snapshot.plain===input.plain;
       out.canonical_serialized_equal=snapshot.serialized===input.serialized;
       out.canonical_topics_equal=JSON.stringify(snapshot.topics)===JSON.stringify(input.topics);
       out.canonical_topic_count=snapshot.topics.length;
     }
   }catch(_){/* Unsupported or unowned editor nodes fail closed. */}
 }
 const videos=[...document.querySelectorAll('video,[data-video-id],[data-file-id],[data-cid]')].filter(visible);
 const covers=[...document.querySelectorAll('img[src],[data-cover-id],[style*="background"],[class*="cover"]')].filter(visible);
 out.visible_video_count=videos.length;out.visible_cover_count=covers.length;
 if(videos.length>128||covers.length>1024)return out;
 for(const e of videos){
   const values=[e.currentSrc,e.getAttribute('src'),e.getAttribute('data-video-id'),e.getAttribute('data-file-id'),e.getAttribute('data-cid')].filter(v=>typeof v==='string');
   if(exact(values,input.video_ids))out.video_exact_id_match_count++;
   if(values.some(v=>v.startsWith('blob:')))out.video_blob_count++;
   if(exact([e.getAttribute('poster')],input.cover_ids))out.video_poster_cover_match_count++;
 }
 for(const e of covers){
   const backgrounds=[...getComputedStyle(e).backgroundImage.matchAll(/url\(["']?(.*?)["']?\)/g)].map(match=>match[1]);
   if(exact(backgrounds,input.cover_ids))out.cover_background_match_count++;
   if(exact([e.getAttribute('src'),e.getAttribute('data-cover-id'),...backgrounds],input.cover_ids))out.cover_exact_id_match_count++;
 }
 out.valid=true;
 out.fields_ok=out.current_editor_owned&&out.title_equal&&out.canonical_read_valid&&
   out.canonical_plain_equal&&out.canonical_serialized_equal&&out.canonical_topics_equal;
 // Preserve the existing visible video + selected cover identity predicate.
 // Poster matches are diagnostic only until actual platform evidence supports it.
 out.assets_ok=out.video_exact_id_match_count>0&&out.cover_exact_id_match_count>0;
 return out;
} """.replace('CANONICAL_READ', '('+_CANONICAL_READ+')')


def xhs_readback_expression(fields, candidate):
    """Build a fixed projection; resource values are never returned or printed."""
    if not isinstance(fields, dict) or not isinstance(candidate, dict):
        raise ValueError('xhs_readback_arguments_invalid')
    if (not isinstance(fields.get('title'), str) or not isinstance(fields.get('description'), str) or
            not isinstance(fields.get('tags'), list) or
            not all(isinstance(tag, str) and tag for tag in fields['tags'])):
        raise ValueError('xhs_readback_title_invalid')
    for key in ('video_ids', 'cover_ids'):
        values=candidate.get(key)
        if (not isinstance(values, list) or not 1<=len(values)<=32 or
                not all(isinstance(value,str) and 0<len(value)<=2048 and
                        not any(ord(c)<32 or ord(c)==127 for c in value) for value in values) or
                len(set(values)) != len(values)):
            raise ValueError('xhs_readback_resource_shape_invalid')
    plan=draft_xhs_description_plan(fields.get('description') or '', fields.get('tags', []))
    args={key:plan[key] for key in ('plain','serialized','topics')}
    args.update(title=fields['title'],video_ids=candidate['video_ids'],cover_ids=candidate['cover_ids'])
    return '('+XHS_READBACK_PROJECTION+')('+json.dumps(args,ensure_ascii=False)+')'


_BOOL_KEYS = frozenset({'valid','root_scope_valid','title_equal','current_editor_owned',
    'canonical_read_valid','canonical_plain_equal','canonical_serialized_equal',
    'canonical_topics_equal','fields_ok','assets_ok'})
_COUNT_KEYS = frozenset({'title_count','current_editor_count','canonical_topic_count',
    'visible_video_count','video_exact_id_match_count','video_blob_count','visible_cover_count',
    'cover_exact_id_match_count','cover_background_match_count','video_poster_cover_match_count'})


def _failed(reason):
    return {'valid':False,'fields_ok':False,'assets_ok':False,'reason':reason}


def _official_document(value):
    try:
        url=urlsplit(value)
        return (url.scheme=='https' and url.netloc=='creator.xiaohongshu.com' and
                url.path=='/publish/publish' and not url.fragment)
    except (ValueError,TypeError):
        return False


async def read_xhs_reopened_ui(page, candidate, *, timeout=5.0):
    """Read the exact normally edited local draft without creating save proof.

    XHS saves locally, so its normal edit helper retains the fresh candidate
    separately from network session.saves. The common verifier independently
    rereads the local record and verifies saved resources/account afterwards.
    """
    try:
        session=getattr(page,'_sau_draft_evidence',None)
        if (session is None or session.closed or session.page is not page or session.platform!='xiaohongshu' or
                not isinstance(candidate,dict) or candidate.get('fresh') is not True or
                not isinstance(candidate.get('id'),str) or not candidate['id']):
            return _failed('session_binding_invalid')
        account=session.account
        wanted=session.expected.get('account')
        binding=getattr(session,'normal_ui_binding',None)
        attested=getattr(session,'normal_ui_candidate',None)
        owner=candidate.get('owner')
        if (not isinstance(account,dict) or account.get('verified') is not True or
                account.get('platform')!='xiaohongshu' or account.get('uid') is None or
                not account.get('identity') or not isinstance(wanted,dict) or
                wanted.get('identity')!=account['identity'] or str(wanted.get('uid'))!=str(account['uid']) or
                not isinstance(owner,str) or not owner or owner!=str(account['uid']) or
                binding!={'id':candidate['id'],'owner':owner,'control':'draft_item_edit'} or
                attested!=candidate or not field_match('xiaohongshu',candidate,session.expected)):
            return _failed('normal_edit_binding_invalid')
        if not _official_document(page.url) or page.main_frame.url!=page.url:
            return _failed('document_scope_invalid')
        before_url=page.url
        before_frame=page.main_frame
        before_record,before_expected,before_account=copy.deepcopy(candidate),copy.deepcopy(session.expected),copy.deepcopy(account)
        before_binding,before_attested=copy.deepcopy(binding),copy.deepcopy(attested)
        value=await canonical_root_evaluate(page,xhs_readback_expression(before_expected['platform_fields'],before_record),timeout=timeout)
        if (getattr(page,'_sau_draft_evidence',None) is not session or session.closed or
                session.page is not page or session.platform!='xiaohongshu' or
                page.url!=before_url or page.main_frame is not before_frame or before_frame.url!=before_url or
                session.expected!=before_expected or session.account!=before_account or candidate!=before_record or
                getattr(session,'normal_ui_binding',None)!=before_binding or
                getattr(session,'normal_ui_candidate',None)!=before_attested):
            return _failed('session_changed_during_read')
        if (not isinstance(value,dict) or set(value)!=_BOOL_KEYS|_COUNT_KEYS or
                any(type(value[key]) is not bool for key in _BOOL_KEYS) or
                any(type(value[key]) is not int or not 0<=value[key]<=1024 for key in _COUNT_KEYS) or
                value['valid'] is not True or value['root_scope_valid'] is not True):
            return _failed('projection_invalid')
        return value
    except NativeReadError as error:
        return _failed(error.reason)
    except (ValueError,KeyError,TypeError,AttributeError):
        return _failed('read_arguments_invalid')

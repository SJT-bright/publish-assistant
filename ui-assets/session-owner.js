/* Read only the current website's owner-shaped public state in its main JS world.
   This expression is shared by Electron and the draft verifier. It does not
   navigate, fetch, inspect credentials/storage, or change the website state.
   Hosts are the existing creator origins, matched exactly. An explicit false
   login flag is logout. A missing flag on a trusted store branch stays the
   original owner-shaped read. Tencent uses its proven boolean isExpired;
   Douyin's upload/editor pages require the committed master account provider and explicit
   authenticated login type, independent of cached globals. */
(platform => {
  const hosts = {douyin:'creator.douyin.com',xiaohongshu:'creator.xiaohongshu.com',
    tencent:'channels.weixin.qq.com',bilibili:'member.bilibili.com',kuaishou:'cp.kuaishou.com'};
  if(!hosts[platform] || ![hosts[platform],'127.0.0.1'].includes(location.hostname))
    return {owner:null,loggedOut:false,source:null};
  const app = document.querySelector('#app');
  const state = app?.__vue__?.$store?.state;
  const positiveInteger = value => Number.isSafeInteger(value) && value > 0;
  function boundedVisit(roots, children, inspect) {
    const queue = roots.filter(Boolean).map(node => ({ node, depth: 0 }));
    const seen = new Set();
    let examined = 0;
    while (queue.length && examined < 500) {
      const { node, depth } = queue.shift();
      if (!node || typeof node !== 'object' || seen.has(node)) continue;
      seen.add(node); examined++;
      inspect(node);
      if (depth >= 8) continue;
      const next = children(node);
      for (let index = 0; index < next.length && queue.length + examined < 500; index++) {
        const child = next[index];
        if (child && typeof child === 'object' && !seen.has(child)) queue.push({ node: child, depth: depth + 1 });
      }
    }
  }
  function capabilities(owner, ownerSource, ownerRecord) {
    const result = {}, tagCounts = new Set();
    if (platform === 'douyin' && ['douyin-owner-state', 'douyin-current-user-provider'].includes(ownerSource)) {
      // Official module 6016.Q only applies the confirmed owner's published
      // health restriction. It defaults to 1000 for empty/invalid messages and
      // compares numeric strings using JS numeric coercion. Retain only a
      // safely normalized positive integer below the normal editor limit.
      // The record is the same object that supplied the confirmed owner id.
      const restriction = ownerRecord?.health_score?.post_text_limit;
      if (restriction?.is_published) {
        let message = null;
        try {
          message = JSON.parse(restriction.publish_msg);
        } catch {
          // An unknown message is not evidence of a lower account limit.
        }
        const raw = message?.ItemDescTextLimit || 1000;
        if (typeof raw === 'number' || typeof raw === 'string') {
          const limit = Number(raw);
          if (positiveInteger(limit) && limit < 1000) result.caption_max = limit;
        }
      }
    } else if (platform === 'kuaishou') {
      boundedVisit([app?.__vue__], component => Array.isArray(component.$children) ? component.$children : [], component => {
        for (const value of [component.uploadConfig?.maxTagCount,
          component._data?.uploadConfig?.maxTagCount, component.config?.maxTagCount]) {
          if (positiveInteger(value)) tagCounts.add(value);
        }
      });
    } else if (platform === 'xiaohongshu') {
      boundedVisit([app?.__vue_app__?._instance, app?._vnode?.component,
        app?.__vueParentComponent, app?.firstElementChild?.__vueParentComponent], node => {
        const children = [];
        if (node.component) children.push(node.component);
        if (node.subTree) children.push(node.subTree);
        if (Array.isArray(node.children)) {
          // Limit the frontier before copying children from a large VNode array.
          for (let index = 0; index < node.children.length && children.length < 500; index++) children.push(node.children[index]);
        }
        return children;
      }, component => {
        const flag = component.setupState?.features?.isTopicLimit;
        if (flag === true) tagCounts.add(3);
        else if (flag === false) tagCounts.add(10);
      });
    }
    // A missing or contradictory topic configuration stays unknown.
    if (tagCounts.size === 1) result.tags_max = [...tagCounts][0];
    return result;
  }
  function ownerResult(owner, loggedOut, source, ownerRecord) {
    const result = { owner, loggedOut, source };
    if (owner && !loggedOut) {
      try {
        const limits = capabilities(owner, source, ownerRecord);
        if (Object.keys(limits).length) result.capabilities = limits;
      } catch {
        // Optional publishing configuration must not hide a proven owner if a
        // component is being replaced or exposes an unavailable reactive field.
      }
    }
    return result;
  }
  const guestId = value => ['0', '-1', 'null', 'undefined'].includes(value);
  function douyinEditorOwner() {
    // The host's committed master account provider, grounded in the retained
    // official user-info-model. Editor/preview props and fastUserInfo are not
    // authentication sources. Read neither an alternate nor a pending value.
    const keys = ['userInfo','fastUserInfo','loginType','userLoading','abLoading',
      'fg','abTest','newPcLayoutVersion','setUserInfo','updateUserInfo','loadUserInfo',
      'permissions','lng','isOversea','setLng','playerModal','fansIndexConfig',
      'accountSwitch','aiCreationEntries','aiCreationEntriesLoaded',
      'selectedAICreationTabId','setSelectedAICreationTabId'].sort();
    const loginTypes = new Set(['personal','oversea_mobile_personal','org','mcn','confer','open_platform']);
    const unknown = {owner:null, loggedOut:false, source:null};
    try {
      // The creator host HTML mounts directly under body. Garfish mounts a
      // second, nested #root for the content microfrontend; it is not the host.
      const roots = document.querySelectorAll('body > #root');
      if (roots.length !== 1 || window !== top) return unknown;
      const host = roots[0];
      const properties = Object.getOwnPropertyNames(host).filter(key => key.startsWith('__reactContainer$'));
      if (properties.length !== 1) return unknown;
      const anchor = host[properties[0]], stateNode = anchor?.stateNode;
      const committed = stateNode?.current;
      if (anchor?.tag !== 3 || committed?.tag !== 3 || committed.stateNode !== stateNode) return unknown;
      const queue = [committed], seen = new Set(), providers = [];
      while (queue.length) {
        const node = queue.pop();
        if (!node || typeof node !== 'object' || seen.has(node) || seen.size >= 4096) return unknown;
        seen.add(node);
        if (node.tag === 10 && node.type?._context) {
          const value = node.memoizedProps?.value;
          if (value && typeof value === 'object') {
            const ownKeys = Object.keys(value).sort();
            if (ownKeys.length === keys.length && ownKeys.every((key,index) => key === keys[index])) providers.push(value);
          }
        }
        if (node.sibling) queue.push(node.sibling);
        if (node.child) queue.push(node.child);
      }
      if (stateNode.current !== committed || host[properties[0]] !== anchor || providers.length !== 1) return unknown;
      const value = providers[0];
      // The host initializes loginType as not_login while its normal account
      // request is still loading. That state is pending, not a logout signal.
      if (value.userLoading !== false) return unknown;
      if (value.loginType === 'not_login') return {owner:null, loggedOut:true, source:'douyin-current-user-provider'};
      if (!loginTypes.has(value.loginType) ||
          typeof value.setUserInfo !== 'function' || typeof value.updateUserInfo !== 'function') return unknown;
      const user = value.userInfo, raw = user?.uid;
      const id = typeof raw === 'string' && /^[1-9][0-9]{0,39}$/.test(raw) ? raw :
        typeof raw === 'number' && Number.isSafeInteger(raw) && raw > 0 ? String(raw) : null;
      // A display name is not an authentication field; some account modes
      // expose only user_profile.nick_name. The full model's UID is canonical.
      if (!id || user.isLogin === false) return unknown;
      return {owner:id, loggedOut:false, source:'douyin-current-user-provider', record:user};
    } catch {
      return unknown;
    }
  }
  let user, logged, uid, source;
  if (platform === 'kuaishou') {
    user = state?.user;
    logged = user?.logined;
    uid = user?.userId;
    source = 'kuaishou-owner-store';
    // Official 6551.e689a430 module initializes this empty store before its
    // normal owner request. It is pending authentication, never an owner or
    // a logout receipt. A false flag on a populated owner still stops below.
    if (logged === false && uid === 0 && user.userName === '' &&
        user.userAvatar === '' && user.userKwaiId === '')
      return {owner:null,loggedOut:false,source:'kuaishou-owner-loading'};
  } else if (platform === 'bilibili') {
    user = state?.upInfo;
    logged = user?.isLogin;
    uid = user?.mid;
    source = 'bilibili-upinfo-store';
  } else if (platform === 'xiaohongshu') {
    const getters = app?.__vue_app__?._context?.provides?.store?.getters;
    logged = getters?.authPermissionState?.isLogin;
    user = getters?.authPermissionRes?.isLogin;
    uid = user?.userId;
    source = 'xiaohongshu-authpermission-store';
  } else if (platform === 'tencent') {
    const account = window._store?.userStore;
    user = account?.finder;
    uid = user?.finderUsername;
    // The WeChat operator lives on account.user and is never the Finder owner.
    // Public userStore initializes isExpired to false. Only that boolean
    // confirms this platform; true is expiry. This rule is not used elsewhere.
    if (account && account.isExpired === true) logged = false;
    else if (account && account.isExpired === false) logged = true;
    else uid = undefined;
    source = 'tencent-finder-store';
  } else if (platform === 'douyin') {
    const matches = [];
    let explicitLogout = false;
    for (const key of ['USER_INFO', '__USER_INFO__', 'userInfo', '__INITIAL_STATE__']) {
      const value = window[key];
      if (!value || typeof value !== 'object') continue;
      for (const candidate of [value.userInfo, value.user?.userInfo, value.user, value.accountInfo, value]) {
        if (!candidate || typeof candidate !== 'object') continue;
        const raw = candidate.user_id || candidate.userId || candidate.uid;
        const text = raw == null ? '' : String(raw).trim();
        if (!text || guestId(text)) continue;
        if (candidate.isLogin === false) { explicitLogout = true; continue; }
        // Original owner-shaped branch. Post arrays are not candidates.
        if (candidate.nickname || candidate.nickName || candidate.isLogin === true)
          matches.push({ id: text, record: candidate });
      }
    }
    const distinct = [...new Set(matches.map(item => item.id))];
    if (location.origin === 'https://creator.douyin.com' &&
        ['/creator-micro/content/upload','/creator-micro/content/post/video'].includes(location.pathname)) {
      const current = douyinEditorOwner();
      if (explicitLogout || current.loggedOut) return ownerResult(null, true, current.source);
      // Old globals can corroborate, never replace, the current editor owner.
      // Conflicting candidates or a missing current provider stop verification.
      if (!current.owner || distinct.some(id => id !== current.owner)) return {owner:null, loggedOut:false, source:null};
      return ownerResult(current.owner, false, current.source, current.record);
    }
    if (distinct.length === 1 && !explicitLogout) {
      user = matches.find(item => item.id === distinct[0]).record;
      uid = distinct[0];
      logged = user.isLogin;
    } else if (explicitLogout) logged = false;
    source = 'douyin-owner-state';
  }
  const id = uid == null ? '' : String(uid).trim();
  const valid = id && !guestId(id);
  if (logged === false) return ownerResult(null, true, source);
  // Tencent must see boolean isExpired false. Other trusted branches keep a
  // valid owner id when their login flag is simply absent.
  if (platform === 'tencent' ? logged === true && valid : valid) return ownerResult(id, false, source, user);

  // The localhost fixtures have no Vue store; this fallback is never used on
  // a creator platform, where data-user-id may identify someone else's content.
  if (location.hostname === '127.0.0.1') {
    const ids = [...document.querySelectorAll('[data-account-id]')]
      .filter(element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden')
      .map(element => element.getAttribute('data-account-id')?.trim()).filter(Boolean);
    if (new Set(ids).size === 1) return ownerResult(ids[0], false, 'localhost-owner-fixture');
  }
  const login = [...document.querySelectorAll('[data-auth-state="logged-out"], .login-box, main#login-form')]
    .some(element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
  return { owner: null, loggedOut: login, source: null };
})

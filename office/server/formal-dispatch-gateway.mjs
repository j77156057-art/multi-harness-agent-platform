import fs from 'node:fs';

const PREFIX='/api/formal';
const fail=(status,message)=>Object.assign(new Error(message),{status});
const ident=s=>typeof s==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(s);
const BASE_HARNESSES=new Set(['codex','claude-code','opencode','qoder','codebuddy']);
// Administrator identity is *not* a gateway constant any more (R10-fix-15): the
// platform delegates it through ``AP_ADMIN_SUBJECTS``, and the opening guide lets
// the owner pick which registered window that is. This variable is an optional
// assertion — set ``OFFICE_FORMAL_ADMIN_SUBJECT`` to fail loudly if the credential
// file holds somebody else's token. Unset means "trust whoever the platform
// authenticates, then let ``AP_ADMIN_SUBJECTS`` decide what they may do".
const ADMIN_ID=process.env.OFFICE_FORMAL_ADMIN_SUBJECT||null;
// Base-harness registration templates (R10 §6). These mirror the platform's own
// pinned registrations (backend/app/adapter/harnesses/*.py). They live on the
// server so the browser only ever names a harness_id: provider/adapter/version
// and the administrator identity stay out of client reach.
const BASE_ADAPTERS={
  'codex':{name:'Codex CLI',provider:'openai',adapter:'codex-exec',adapter_version:'1.0.0',capabilities:['coding'],registration_version:'reg-001'},
  'claude-code':{name:'Claude Code',provider:'anthropic',adapter:'claude-print',adapter_version:'1.0.0',capabilities:['coding'],registration_version:'reg-001'},
  'opencode':{name:'OpenCode',provider:'sst',adapter:'opencode-run',adapter_version:'1.0.0',capabilities:['coding'],registration_version:'reg-001'},
  'qoder':{name:'Qoder CLI CN',provider:'qoder',adapter:'qoderclicn-print',adapter_version:'1.0.0',capabilities:['coding'],registration_version:'reg-001'},
  'codebuddy':{name:'CodeBuddy',provider:'tencent',adapter:'codebuddy-print',adapter_version:'1.0.0',capabilities:['coding'],registration_version:'reg-001'},
  // The administrator is an external Harness window chosen in the opening guide
  // (R10-fix-15). `office-admin` used to be registered here as an extra harness;
  // it is the platform assistant's model identity, not a window, and no Harness
  // CLI answers to that id -- the backend's refine call resolves the model channel
  // from the administrator's own identity, so a non-window id fails closed.
  // This table only holds *base* registrations; windows are added through
  // POST /harnesses, and the administrator credential lives in
  // OFFICE_FORMAL_ADMIN_TOKEN_FILE (currently qoder-window-admin.key).
};
const windowIdFor=(base, value)=>typeof base==='string' && typeof value==='string' &&
  new RegExp(`^${base.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}-(member|window)-[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`).test(value);

export function createFormalGateway(config={}){
  const enabled=Boolean(config.upstream&&config.tokenFile&&config.project&&config.group);
  let upstream;
  if(enabled){
    upstream=new URL(config.upstream);
    if(upstream.protocol!=='http:'||upstream.hostname!=='127.0.0.1'||upstream.username||upstream.password||upstream.pathname!=='/'||upstream.search||upstream.hash)
      throw new Error('formal upstream must be loopback HTTP');
    if(!ident(config.project)||!ident(config.group))throw new Error('invalid formal scope');
  }
  // A second credential, for the administrator harness subject. It is optional:
  // without it the admin-only routes answer 503 and every owner route keeps
  // working exactly as before.
  const adminEnabled=Boolean(config.adminTokenFile);
  const json=(res,status,data)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(data));};
  // One credential rescue helper: a file path is any string that is not already
  // a token, so both shapes work and a mis-typed `[object Object]` fails loudly
  // instead of silently becoming a path.
  function readCredential(value){
    if(typeof value!=='string')throw fail(503,'administrator_credential_unavailable');
    const trimmed=value.trim();
    if(/^apt_[a-f0-9]{64}$/.test(trimmed))return trimmed;
    if(!trimmed||trimmed.startsWith('['))throw fail(503,'administrator_credential_unavailable');
    return fs.readFileSync(trimmed,'utf8');
  }
  async function call(path,body,credential){
    // `undefined` = the operator; `null`/`''` used to mean "open" by accident,
    // so an explicit call site must pass a credential and defaulting is done here,
    // once.
    const source=credential===undefined?config.tokenFile:credential;
    const token=readCredential(source).trim();
    if(process.env.ADMIN_DEBUG_ROUTES){console.error('ADMIN-DEBUG call',path,'cred type',typeof credential,'first',String(credential).slice(0,6));}
    if(!/^apt_[a-f0-9]{64}$/.test(token)){throw fail(503,'administrator_credential_unavailable');}
    let response;
    try{response=await fetch(new URL('/api/v1'+path,upstream),{method:body?'POST':'GET',
      headers:{Authorization:`Bearer ${token}`,...(body?{'Content-Type':'application/json'}:{})},
      ...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(10000),redirect:'error'});}
    catch{throw fail(503,'formal_backend_unavailable');}
    // 1 MB was too tight for a read that pages 200 events: one attempt's output
    // once went into the event log and the task history grew past it, so the
    // office's task read answered 502. The cap stays (a runaway upstream must
    // not be forwarded), but it has to fit a real event page.
    const text=await response.text();if(text.length>8388608)throw fail(502,'response_too_large');
    let data;try{data=JSON.parse(text);}catch{throw fail(502,'invalid_backend_response');}
    if(!response.ok)throw fail(response.status,data.error?.code||'backend_rejected');
    return data;
  }
  // The scope (who the operator is, and which group this gateway serves) does
  // not change between requests, but it was re-fetched on **every** call: the
  // office's harness screens ask for one task and one output per window, so a
  // dozen windows turned into ~50 upstream calls per cycle -- and the browser's
  // per-origin connection limit then starved the platform assistant's panel,
  // which never got to render.  A few seconds of caching is the difference
  // between a live observation surface and a frozen one.
  let scopeCache=null,scopeCacheAt=0;
  const SCOPE_TTL_MS=5000;
  const scope=async()=>{
    const now=Date.now();
    if(scopeCache&&now-scopeCacheAt<SCOPE_TTL_MS)return scopeCache;
    const [auth,groups]=await Promise.all([call('/auth/whoami'),call('/groups?project_id='+encodeURIComponent(config.project))]);
    if(auth.mode!=='enforce'||auth.subject?.type!=='human')throw fail(503,'formal_authentication_required');
    if(!groups.groups?.some(g=>g.group_id===config.group&&g.project_id===config.project))throw fail(503,'formal_group_not_configured');
    scopeCache=auth.subject.id;scopeCacheAt=now;
    return scopeCache;
  };
  // The administrator's own credential, verified by the platform rather than by
  // us: whoami must come back as a *harness* subject. A human token pasted into
  // the admin slot therefore cannot borrow the harness's authority, and an admin
  // token that the platform does not know is refused before any write. The id
  // itself is whatever `AP_ADMIN_SUBJECTS` delegated; `OFFICE_FORMAL_ADMIN_SUBJECT`
  // is an optional extra assertion for deployments that want one pinned.
  let adminToken=null;
  let adminId=null;
  const adminScope=async()=>{
    if(!adminEnabled)throw fail(503,'formal_admin_not_configured');
    // The identity and the credential are cached separately on purpose. Returning
    // the cached *token* from here used to make the second administrator action in
    // one process send `reviewer_harness_id: "apt_..."`, which the platform
    // correctly refused as `identity_mismatch` (audit seq 386).
    if(adminId)return adminId;
    const token=readCredential(config.adminTokenFile).trim();
    if(!/^apt_[a-f0-9]{64}$/.test(token))throw fail(503,'administrator_credential_unavailable');
    const auth=await call('/auth/whoami',undefined,token);
    if(auth.mode!=='enforce'||auth.subject?.type!=='harness')throw fail(503,'formal_admin_authentication_required');
    if(ADMIN_ID&&auth.subject.id!==ADMIN_ID)throw fail(503,'formal_admin_identity_mismatch');
    adminToken=token;
    adminId=auth.subject.id;
    return adminId;
  };
  const safeTask=t=>({task_id:t.task_id,title:t.title,group_id:t.group_id,status:t.status,owner:t.owner,
    assigned_harness_ids:JSON.parse(t.assigned_harness_ids_json||'[]'),attempt_id:t.current_attempt_id,
    attempt_no:t.attempt_no,max_attempts:t.max_attempts,budget_override_by:t.budget_override_by,
    escalated_at:t.escalated_at,state_changed_seq:t.state_changed_seq,
    registration_version:t.registration_version,receipt_id:t.last_receipt_id,epoch:t.current_epoch,
    // Why the task fell over: the office's「失败与接手」panel says why, not
    // just "abandoned".  It is the platform's own last abandon/release reason.
    last_failure:t.last_failure||null,
   // The lineage: which task this one takes over (a platform fact).
   supersedes:t.supersedes||null,
   // 时间戳：办公室要按"最近更新"排序、要让人看见一条活多久没动了。
   created_at:t.created_at||null,updated_at:t.updated_at||null});
  async function handle(req,res){
    if(!(req.url===PREFIX||req.url.startsWith(PREFIX+'/')))return false;
    try{
      const host=req.headers.host;
      if(![`127.0.0.1:${req.socket.localPort}`,`localhost:${req.socket.localPort}`].includes(host))throw fail(421,'invalid_host');
      if(!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress))throw fail(403,'loopback_only');
      if(req.headers.origin&&req.headers.origin!==`http://${host}`)throw fail(403,'invalid_origin');
      if(req.headers['sec-fetch-site']&&!['same-origin','none'].includes(req.headers['sec-fetch-site']))throw fail(403,'cross_site_request');
      const url=new URL(req.url,`http://${host}`),route=url.pathname.slice(PREFIX.length);
      // 只有这两条路由带查询串：消息的游标，和任务列表的 basic 开关
      // （basic=1 跳过逐任务的校验回执，手机那种只读列表不该付这个代价）。
      // Which routes may carry a query string. `/tasks/{id}/output` also accepts
   // `attempt_id`: "the real log of *this* failure" is the failed attempt, not
   // whatever attempt happens to be running now.
   const outputRoute=/^\/tasks\/FD-[a-f0-9]{16}\/output$/.test(route);
   if(url.search&&!['/messages','/tasks','/events'].includes(route)&&!outputRoute)throw fail(400,'unexpected_query');
   if(outputRoute&&url.search){
     for(const key of url.searchParams.keys())if(key!=='attempt_id')throw fail(400,'unexpected_query');
     const attemptParam=url.searchParams.get('attempt_id')||'';
     if(attemptParam&&!/^[A-Za-z0-9._-]{1,120}$/.test(attemptParam))throw fail(400,'invalid_attempt_id');
   }
      if(process.env.ADMIN_DEBUG_ROUTES){console.error('ADMIN-DEBUG ROUTE',req.method,route,req.url);}
      if(!enabled){if(req.method==='GET'&&route==='/status'){json(res,200,{configured:false,connected:false});return true;}throw fail(503,'formal_gateway_not_configured');}
      if(req.method==='POST'&&(req.headers['x-office-formal']!=='1'||req.headers.origin!==`http://${host}`))throw fail(403,'same_origin_write_required');
      const operator=await scope();
      const admin={accounted:false};
      // Lazily verified, so the administrator credential is only read on the
      // routes that actually act as the administrator harness. Returns the
      // verified harness id *and* its token, which is what `call()` needs.
      const asAdmin=async()=>{
        if(!admin.accounted){admin.id=await adminScope();admin.token=adminToken;admin.accounted=true;}
        return admin.id;
      };
      if(req.method==='GET'&&route==='/status'){
        const members=await call('/harnesses');
        const listed=members.harnesses||[];
        // The administrator identity is read from the *credential the platform
        // authenticated*, never from a constant: the owner picks the window in the
        // opening guide, and the refine call resolves its CLI from that same id.
        // A broken admin credential must not take `/status` down with it — every
        // owner route still works — so the failure is reported next to the null.
        let adminIdentity=null,adminError=null;
        if(adminEnabled){
          try{adminIdentity=await adminScope();}
          catch(error){adminError=String(error?.message||error);}
        }
        // 后台的 harness 注册表是全局的：所有项目共用同一批登记窗口。真正的成员关系
        // 只能从本项目已派发的任务里推导（指定执行者 / 持有者 / 提交者 / 审查者），
        // 否则新开一个项目会把别的项目的窗口也算进巡检摘要。
        const scoped=await call('/groups/'+encodeURIComponent(config.group)+'/tasks');
        if((scoped.tasks||[]).some(t=>t.group_id&&t.group_id!==config.group))throw fail(502,'task_scope_mismatch');
        // The raw `/tasks` payload carries `assigned_harness_ids_json`; only the
        // gateway's own `safeTask()` parses it. Reading the raw row directly is
        // how the roster briefly came back empty.
        const targets=t=>Array.isArray(t.assigned_harness_ids)?t.assigned_harness_ids
          :JSON.parse(t.assigned_harness_ids_json||'[]');
        const seen=new Set();
        for(const t of scoped.tasks||[]){
          for(const id of targets(t)) if(id) seen.add(id);
          if(t.owner) seen.add(t.owner);
          if(t.reviewer_harness_id) seen.add(t.reviewer_harness_id);
        }
        json(res,200,{configured:true,connected:true,project_id:config.project,group_id:config.group,operator,
          // `identity` 是网关当前**以谁的管理员身份**动作——由平台配置决定，不是常量。
          // 拿不到（凭据坏了/未配置）就是 null 加一条 `error`：前端据此不显示管理员
          // 结论，而不是拿一个过期的 id 去猜。
          admin:{configured:adminEnabled,identity:adminIdentity,error:adminError},
          project_members:[...seen].sort(),
          harnesses:listed.filter(h=>h.registration_version).map(h=>({harness_id:h.harness_id,
          name:h.name||h.harness_id,status:h.status,registration_version:h.registration_version,
          provider:h.provider,
          // `credential_count` 是平台那边"这个窗口能不能真的领活"的依据：
          // 登记在册但没有凭据的窗口领不了任务，派给它的活会永远停在 ready。
          credential_count:Number(h.credential_count||0),
          capabilities:JSON.parse(h.capabilities_json||'[]'),running_tasks:h.running_tasks})),
          base_adapters:Object.entries(BASE_ADAPTERS)
            .filter(([id])=>!listed.some(h=>h.harness_id===id&&h.registration_version))
            .map(([id,t])=>({harness_id:id,name:t.name,provider:t.provider,adapter:t.adapter}))});return true;
      }
      if(req.method==='POST'&&route==='/harnesses'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>12000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        const fields=['source_harness_id','instance_id','name'];
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>!fields.includes(k))||
          fields.some(k=>typeof body[k]!=='string'||!body[k].trim()))throw fail(400,'invalid_harness_registration');
        if(!ident(body.source_harness_id)||!BASE_HARNESSES.has(body.source_harness_id)||
          !ident(body.instance_id)||body.name.length>120)
          throw fail(400,'invalid_harness_registration');
        if(!windowIdFor(body.source_harness_id,body.instance_id))
          throw fail(400,'instance_id_must_be_member_or_window');
        const listed=await call('/harnesses');
        const source=listed.harnesses?.find(item=>item.harness_id===body.source_harness_id);
        if(!source||!source.registration_version)throw fail(404,'source_harness_not_registered');
        const capabilities=JSON.parse(source.capabilities_json||'[]');
        const registration={
          registered_by:operator,
          harness_id:body.instance_id,
          name:body.name.trim(),
          provider:source.provider,
          adapter:source.adapter,
          adapter_version:source.adapter_version,
          capabilities:Array.isArray(capabilities)?capabilities:[],
          max_parallel_tasks:Number(source.max_parallel_tasks)||1,
          status:'online',
          registration_version:source.registration_version,
        };
        const result=await call('/harnesses',registration);
        json(res,200,{...result,source_harness_id:body.source_harness_id,instance_id:body.instance_id});return true;
      }
      if(req.method==='POST'&&route==='/base-harnesses'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>4000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>k!=='harness_id')||typeof body.harness_id!=='string'||!body.harness_id.trim())
          throw fail(400,'invalid_base_registration');
        const template=BASE_ADAPTERS[body.harness_id];
        if(!template)throw fail(400,'unknown_base_harness');
        const registration={registered_by:operator,harness_id:body.harness_id,name:template.name,
          provider:template.provider,adapter:template.adapter,adapter_version:template.adapter_version,
          capabilities:template.capabilities,max_parallel_tasks:1,status:'online',
          registration_version:template.registration_version};
        const result=await call('/harnesses',registration);
        json(res,200,{...result,harness_id:body.harness_id,name:template.name,provider:template.provider});return true;
      }
      if(req.method==='GET'&&route==='/tasks'){
        // `?basic=1` 跳过逐任务的校验回执：44 条任务就是 44 个额外请求，平台在
        // 写库时读会被阻塞，手机那种每几秒一轮的只读列表不该付这个代价。
        // 正式任务观察面板要看校验回执，走默认路径。
        const basic=url.searchParams.get('basic')==='1';
        if([...url.searchParams.keys()].some(k=>k!=='basic'))throw fail(400,'unexpected_query');
        const result=await call('/groups/'+encodeURIComponent(config.group)+'/tasks');
        const tasks=await Promise.all(result.tasks.map(async t=>{
          const base=safeTask(t);
          if(basic)return {...base,verification:null};
          return {...base,...(await call('/tasks/'+encodeURIComponent(t.task_id)+'/verification'))};
        }));
        json(res,200,{tasks});return true;
      }
      if(req.method==='GET'&&route==='/events'){
        // 群范围内的事件流：成员的动态表、成员的求助都从这里读。
        // 平台的 /events 返回本主体能看到的全部事件，所以这里必须自己裁到
        // 本群的任务上（否则别的项目的事件会混进来）。
        for(const key of url.searchParams.keys())if(key!=='limit')throw fail(400,'unexpected_query');
        const limit=Math.min(1000,Math.max(1,Number(url.searchParams.get('limit'))||300));
        const groupTasks=await call('/groups/'+encodeURIComponent(config.group)+'/tasks');
        const taskIds=new Set((groupTasks.tasks||[]).map(t=>t.task_id));
        // 平台的事件页是按 seq 升序的（最旧的在前面），而这个群可能已经超过
        // 一页。用本群任务的 state_changed_seq 定位最新的一段，再裁到本群的任务上。
        const maxSeq=Math.max(0,...(groupTasks.tasks||[]).map(t=>Number(t.state_changed_seq||0)));
        const since=Math.max(0,maxSeq-limit);
        const page=await call('/events?since_seq='+since+'&limit='+limit);
        const events=(page.events||[]).filter(e=>taskIds.has(e.aggregate_id));
        json(res,200,{events,next_seq:page.next_seq,has_more:page.has_more});return true;
      }
      if(req.method==='GET'&&route==='/availability'){
        // 谁现在能用、用的哪个模型、最近有没有失败（平台侧的事实）。
        const availability=await call('/adapter/availability');
        json(res,200,availability);return true;
      }
      if(req.method==='GET'&&route==='/plan'){
        // 方案定稿：给人看的那一份（草稿 + 传阅记录），以及由人点「批准并派单」。
        const plan=await call('/groups/'+encodeURIComponent(config.group)+'/plans');
        json(res,200,plan);return true;
      }
      if(req.method==='POST'&&route==='/task/budget_override'){
        // **人**给一次机会：这条活的尝试预算用完了，只有人能说"再给一次"。
        // 平台不给自己豁免，管理员 harness 也不代劳。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>20000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body.task_id!=='string'||!ident(body.task_id))throw fail(400,'invalid_task_fields');
        const result=await call('/human/attempt_budget_override',{task_id:body.task_id,decided_by:operator,reason:body.reason||''});
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/plan/approve'){
        // **人**的最终审核：用 operator 身份（人类）批准计划 → 任务才可被领取。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>20000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body.plan_id!=='string'||!ident(body.plan_id))throw fail(400,'invalid_plan_fields');
        const result=await call('/plans/'+encodeURIComponent(body.plan_id)+'/approve',{decided_by:operator});
        json(res,200,result);return true;
      }
      if(req.method==='GET'&&route==='/pool'){
        // 平台自己的额度池：池子里有哪些额度、谁的额度被谁花了。
        // 池子的接口只认 harness 主体，所以这里用管理员 harness 的凭据读。
        const pool=await call('/pool/status',undefined,config.adminTokenFile);
        json(res,200,pool);return true;
      }
      if(req.method==='GET'&&route==='/messages'){
        const sinceRaw=url.searchParams.get('since_seq')||'0';
        const limitRaw=url.searchParams.get('limit')||'100';
        if(!/^\d+$/.test(sinceRaw)||!/^\d+$/.test(limitRaw))throw fail(400,'invalid_message_cursor');
        const since=Number(sinceRaw),limit=Number(limitRaw);
        if(!Number.isSafeInteger(since)||!Number.isSafeInteger(limit)||limit<1||limit>500)throw fail(400,'invalid_message_cursor');
        const result=await call('/groups/'+encodeURIComponent(config.group)+'/messages?since_seq='+since+'&limit='+limit);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&['/human/resume','/human/reopen','/human/release','/human/cancel','/human/adjudicate'].includes(route)){
        const action=route.slice('/human'.length);
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>8000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        const allowed=action==='/cancel'?['task_id','reason']
          :action==='/release'?['task_id','reason','budget_override']
          :action==='/adjudicate'?['task_id','question','options']
          :['task_id','answer'];
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>!allowed.includes(k))||
          typeof body.task_id!=='string'||!ident(body.task_id))throw fail(400,'invalid_task_action');
        if(body.answer!==undefined&&typeof body.answer!=='string')throw fail(400,'invalid_task_action');
        if(body.reason!==undefined&&typeof body.reason!=='string')throw fail(400,'invalid_task_action');
        if(body.budget_override!==undefined&&typeof body.budget_override!=='boolean')throw fail(400,'invalid_task_action');
        if(body.question!==undefined&&typeof body.question!=='string')throw fail(400,'invalid_task_action');
        if(body.options!==undefined&&(!Array.isArray(body.options)||body.options.length>20||body.options.some(option=>typeof option!=='string')))throw fail(400,'invalid_task_action');
        if(action==='/adjudicate'&&(typeof body.question!=='string'||!body.question.trim()))throw fail(400,'invalid_task_action');
        const payload={task_id:body.task_id,decided_by:operator,
          ...(body.answer!==undefined?{answer:body.answer}:{}),...(body.reason!==undefined?{reason:body.reason}:{}),
          ...(body.budget_override!==undefined?{budget_override:body.budget_override}:{}),
          ...(body.question!==undefined?{question:body.question}:{}),...(body.options!==undefined?{options:body.options}:{})};
        const result=await call(route,payload);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/messages'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>20000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        const fields=['body','mentions','task_id','refs','direct'];
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>!fields.includes(k))||typeof body.body!=='string'||!body.body.trim())throw fail(400,'invalid_message_fields');
        if(body.mentions!==undefined&&!Array.isArray(body.mentions))throw fail(400,'invalid_message_fields');
        if(body.refs!==undefined&&!Array.isArray(body.refs))throw fail(400,'invalid_message_fields');
        const result=await call('/groups/'+encodeURIComponent(config.group)+'/messages',{author_kind:'human',author_id:operator,
          body:body.body,mentions:body.mentions||[],...(body.task_id?{task_id:body.task_id}:{}),refs:body.refs||[],
          ...(body.direct?{direct:true}:{})});
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&(route==='/review/claim'||route==='/review/submit')){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>200000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body.task_id!=='string'||typeof body.attempt_id!=='string')throw fail(400,'invalid_review_fields');
        const task=await call('/tasks/'+encodeURIComponent(body.task_id));
        if(task.group_id!==config.group)throw fail(404,'task_not_in_group');
        // Reviewer identity: 0 means the administrator harness (the platform
        // then enforces review_policy=cross_provider against the task owner),
        // 1 means the human operator (kind=human, so the policy check is the
        // owner's deliberate override).
        const actor=body.as===1?'human':'admin';
        if(body.as!==undefined&&body.as!==0&&body.as!==1)throw fail(400,'invalid_review_fields');
        if(process.env.ADMIN_DEBUG_ROUTES){console.error('DEBUG review actor',route,actor,JSON.stringify(body));}
        if(route==='/review/claim'){
          const fields=['task_id','attempt_id','review_id','as'];
          if(Object.keys(body).some(k=>!fields.includes(k)))throw fail(400,'invalid_review_fields');
          if(actor==='human'){
            const result=await call('/human/review/claim',{task_id:body.task_id,attempt_id:body.attempt_id,...(body.review_id?{review_id:body.review_id}:{}),decided_by:operator});
            json(res,200,result);return true;
          }
          const who=await asAdmin();
          const result=await call('/review/claim',{task_id:body.task_id,attempt_id:body.attempt_id,...(body.review_id?{review_id:body.review_id}:{}),reviewer_harness_id:who},admin.token);
          json(res,200,result);return true;
        }
        const fields=['task_id','attempt_id','review_id','verdict','reviewed_commit','review_revision','findings','as'];
        if(Object.keys(body).some(k=>!fields.includes(k))||typeof body.review_id!=='string'||typeof body.verdict!=='string'||typeof body.reviewed_commit!=='string')throw fail(400,'invalid_review_fields');
        if(!Array.isArray(body.findings||[]))throw fail(400,'invalid_review_fields');
        const payload={task_id:body.task_id,attempt_id:body.attempt_id,review_id:body.review_id,
          verdict:body.verdict,reviewed_commit:body.reviewed_commit,
          review_revision:body.review_revision??1,findings:body.findings||[]};
        const configuredAdmin=await asAdmin();
        const result=actor==='human'
          ?await call('/human/review/submit',{...payload,decided_by:operator})
          :await call('/review/submit',{...payload,reviewer_harness_id:configuredAdmin},admin.token);        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/admin/refine'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>40000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(process.env.ADMIN_DEBUG_ROUTES){console.error('DEBUG refine body',raw.slice(0,200));}
        const fields=['requirement','max_tasks','answers'];
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>!fields.includes(k))||
          typeof body.requirement!=='string'||!body.requirement.trim()||body.requirement.length>4000)throw fail(400,'invalid_refine_fields');
        if(body.max_tasks!==undefined&&(!Number.isInteger(body.max_tasks)||body.max_tasks<1||body.max_tasks>8))throw fail(400,'invalid_refine_fields');
        if(body.answers!==undefined&&(!Array.isArray(body.answers)||body.answers.length>8||body.answers.some(a=>typeof a!=='string')))throw fail(400,'invalid_refine_fields');
        const result=await call('/adapter/admin/refine',{group_id:config.group,requirement:body.requirement,
          max_tasks:body.max_tasks??3,answers:body.answers||[]},config.adminTokenFile);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/admin/dispatch'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>20000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        const fields=['request_id','title','goal','harness_id','exclusive_paths','acceptance'];
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>!fields.includes(k))||fields.some(k=>!(k in body)))throw fail(400,'invalid_fields');
        const result=await call('/groups/'+encodeURIComponent(config.group)+'/dispatch',{...body,decided_by:await asAdmin()});
        const task=await call('/tasks/'+encodeURIComponent(result.task_id));
        if(task.group_id!==config.group)throw fail(502,'task_scope_mismatch');
        json(res,200,{...result,task:safeTask(task)});return true;
      }
      if(req.method==='GET'&&/^\/tasks\/FD-[a-f0-9]{16}$/.test(route)){
        const id=route.split('/').at(-1),task=await call('/tasks/'+id);
        if(task.group_id!==config.group)throw fail(404,'task_not_in_group');
        const diff=await call('/tasks/'+id+'/diff?include_patch=true');
        const events=await call('/tasks/'+id+'/events?limit=200');
        // 规格（工单/验收/路径）一起给：办公室要让人**看着工单**做决定。
        json(res,200,{task:{...safeTask(task),...(task.spec?{spec:task.spec}:{})},diff,events});return true;
      }
      if(req.method==='GET'&&/^\/tasks\/FD-[a-f0-9]{16}\/output$/.test(route)){
        // The harness CLI's own output, as it streams. Read-only, and scoped to
        // this group exactly like the task route above.
        const id=route.split('/')[2],task=await call('/tasks/'+id);
        if(task.group_id!==config.group)throw fail(404,'task_not_in_group');
        const attemptParam=url.searchParams.get('attempt_id')||'';
        const output=await call('/tasks/'+id+'/output'+(attemptParam?'?attempt_id='+encodeURIComponent(attemptParam):''));
        json(res,200,{output:output.output||null});return true;
      }
      // 注意：这里**没有**人类的派单路由。
      //
      // 办公室是观察面：派单是管理员 Harness 的动作（`/admin/dispatch`，用管理员
      // 凭据）。曾经存在过一条以人类身份（operator）调用平台派单接口的路由，它没
      // 有任何前端调用者，却是"一个目标 × 9 个窗口"那批历史任务的来源——每个
      // harness 都收到一模一样的"做一个游戏"。按"不得恢复人工派单"，这条写入
      // 面已经撤掉：现在只有管理员能派单，而且派的是**细化之后**的子任务。
      throw fail(404,'formal_route_not_allowed');
    }catch(error){json(res,error.status||503,{error:error.status?error.message:error.code||'formal_gateway_unavailable',...(error.status?{}:{detail:String(error.message||error)})});return true;}
  }
  return {handle};
}

export function formalGatewayPlugin(config){
  const configure=server=>{const gateway=createFormalGateway(config);
    server.middlewares.use((req,res,next)=>void gateway.handle(req,res).then(handled=>{if(!handled)next();}));};
  return {name:'local-formal-dispatch',configureServer:configure,configurePreviewServer:configure};
}

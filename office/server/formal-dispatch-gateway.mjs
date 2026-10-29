import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createWindowProvisioner } from './window-provisioner.mjs';

import { parseTranscript } from '../src/cli-transcript.js';

const PREFIX='/api/formal';
const fail=(status,message)=>Object.assign(new Error(message),{status});
const ident=s=>typeof s==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(s);
const BASE_HARNESSES=new Set(['codex','claude-code','opencode','qoder','codebuddy','platform']);
// 声明出来的基座（`adapters/cli.json`）：平台知道它们（`known_bases()` 读声明表），
// 办公室本地照着它登记 —— 「加一个新 harness 不用改代码」在本地这一侧也成立。
// 模板由平台给（与 `GenericCliAdapter` 造注册表的是同一份），这里不自己拼。
const declaredBaseTemplates=(members)=>Array.isArray(members?.base_adapters)?members.base_adapters:[];
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
  // 平台自己的工具：不是某个 CLI，而是平台替模型执行工具的那条路（见
  // `backend/app/agenttools/`）。放进这张表的用处只有一个 —— 窗口创建时能选到它，
  // 不需要人先手工登记一次基座。
  'platform':{name:'平台工具',provider:'pool',adapter:'platform-tools',adapter_version:'1.0.0',capabilities:['coding'],registration_version:'reg-001'},
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
  async function call(path,body,credential,method,timeoutMs){
    // `undefined` = the operator; `null`/`''` used to mean "open" by accident,
    // so an explicit call site must pass a credential and defaulting is done here,
    // once.
    const source=credential===undefined?config.tokenFile:credential;
    const token=readCredential(source).trim();
    if(process.env.ADMIN_DEBUG_ROUTES){console.error('ADMIN-DEBUG call',path,'cred type',typeof credential,'first',String(credential).slice(0,6));}
    if(!/^apt_[a-f0-9]{64}$/.test(token)){throw fail(503,'administrator_credential_unavailable');}
    let response;
    try{response=await fetch(new URL('/api/v1'+path,upstream),{method:method||(body?'POST':'GET'),
      headers:{Authorization:`Bearer ${token}`,...(body?{'Content-Type':'application/json'}:{})},
      ...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(timeoutMs||10000),redirect:'error'});}
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
  /**
   * 让**平台**把机器写的文本洗成人话（一次问一批）。
   *
   * 结论第一句常常是"FD-0b0bcc415aa9fe2f（attempt .a1）· 提交 32f84d6" ——
   * 人读到的是编号，不是"这件事"。失败原因常常是 `HarnessLeaseLost` 这种
   * 给日志看的错误码。规则在平台的翻译服务里（单一出处），界面不自己造一套。
   * 平台够不着就照旧显示原文，不让这一屏白掉。
   *
   * @param {string[]} texts 要洗的原文
   * @param {string} kind 平台那边的分类（text=自由文本 / reason=失败原因）
   * @returns {Promise<Map<string,string>>} 原文 → 人话
   */
  async function translateBatch(texts, kind) {
    const wanted = [...new Set((texts || []).filter(Boolean).map((text) => String(text)))];
    if (!wanted.length) return new Map();
    try {
      const result = await call('/wording/translate', { texts: wanted, kind });
      // 钥匙用**发出去的原文**。平台回显的 original 与它一致；但平台那边也会
      // 去掉首尾空白再回显，所以两种都存一份，省得按哪种都找不回来。
      const map = new Map();
      for (const row of result.texts || []) {
        const original = String(row.original || '');
        const text = String(row.text || '');
        if (!original) continue;
        map.set(original, text);
        const trimmed = original.trim();
        if (trimmed && trimmed !== original) map.set(trimmed, text);
      }
      return map;
    } catch {
      // 平台够不着：照旧。
      return new Map();
    }
  }

  /** 输出窗口：结论（自由文本）与失败原因（错误码）分别洗。 */
  async function humanizeRows(rows) {
    const texts = [];
    const reasons = [];
    for (const row of rows || []) {
      for (const attempt of row.attempts || []) {
        for (const item of attempt.texts || []) {
          const text = String(item.text || '');
          if (text) texts.push(text);
        }
        const reason = String(attempt.reason || '');
        if (reason) reasons.push(reason);
      }
    }
    const [cleaned, spoken] = await Promise.all([
      translateBatch(texts, 'text'),
      translateBatch(reasons, 'reason'),
    ]);
    for (const row of rows || []) {
      for (const attempt of row.attempts || []) {
        for (const item of attempt.texts || []) {
          const next = cleaned.get(String(item.text || ''));
          if (next) item.text = next;
        }
        const next = spoken.get(String(attempt.reason || ''));
        if (next) attempt.reason = next;
      }
    }
  }

  /**
   * 方案正文：管理员写的方案也是机器写的，里面有 `exclusive_paths`、
   * `FD-…` 这类标记。给人看的那一份要洗过 —— 批准之前，人读的就是这份。
   */
  async function humanizePlan(plan) {
    const draft = plan && plan.draft;
    const doc = draft && draft.document;
    if (!doc || !doc.markdown) return;
    const cleaned = await translateBatch([String(doc.markdown)], 'text');
    const next = cleaned.get(String(doc.markdown));
    if (next) doc.markdown = next;
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
  /**
   * 把**工单**和平台追加的**现场报告**拆开。
   *
   * 上一轮没做完时，平台会把上一次尝试的现场报告（错误、CLI 自己的话、动过哪些
   * 文件）**追加到工单后面**，让接手的人能接着做 —— 那是给干活的人看的，不是给
   * 人看的。真数据里那条工单有 2957 字，后面 2900 字是一坨机器 JSON。
   *
   * 所以这里按平台自己的标记拆开：工单还给工单，现场报告单独一个字段。
   *
   * 标记来自平台（backend/app/adapter/formal_worker.py 的 build_goal 与
   * backend/app/admin/worker.py 的 _goal_with_handoff）。平台哪天改了这句话，
   * 这里就拆不开 —— 退回今天的样子（工单里带机器那坨），**不会丢东西**。
   */
  const HANDOFF_MARKER='The previous attempt did NOT finish; here is its breakpoint report.';
  const HANDOFF_JSON_MARKER='Breakpoint report (verbatim):';
  function splitHandoff(spec){
    const source=spec||{};
    const goal=String(source.goal||'');
    const at=goal.indexOf(HANDOFF_MARKER);
    if(at<0)return source;
    const human=goal.slice(0,at).trim();
    const rest=goal.slice(at);
    const jsonAt=rest.indexOf(HANDOFF_JSON_MARKER);
    const raw=jsonAt>=0?rest.slice(jsonAt+HANDOFF_JSON_MARKER.length).trim():'';
    let report=null;
    try{report=JSON.parse(raw);}catch{report=null;}
    return {...source,goal:human,handoff:report,handoff_raw:raw};
  }

  /**
   * 现场报告里的失败原因 → 人话。
   *
   * 走**平台**的规则表（跟输出页的失败原因同一条路）：认得出的换成人话，认不出的
   * 原样留着（宁可原样，也不要编一个错的说法）。平台够不着就照旧显示原文。
   */
  async function humanizeHandoff(spec){
    const report=spec&&spec.handoff;
    if(!report||!report.error)return spec;
    try{
      const result=await call('/wording/translate',{texts:[String(report.error)],kind:'reason'});
      const row=(result.texts||[])[0];
      if(row&&row.translated&&row.text)return {...spec,handoff:{...report,error_human:row.text}};
    }catch{
      // 平台够不着：照旧。
    }
    return spec;
  }

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
   // `/installers` 带自己的两个开关（要不要跑版本命令、问哪个系统），在它自己那段里逐个校验；
   // 这里是**全局**白名单，漏了它就会在进路由之前被 400 掉。
   if(url.search&&!['/messages','/tasks','/events','/installers'].includes(route)&&!outputRoute)throw fail(400,'unexpected_query');
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
          base_adapters:[
            ...Object.entries(BASE_ADAPTERS).map(([id,t])=>({harness_id:id,name:t.name,provider:t.provider,
              adapter:t.adapter,adapter_version:t.adapter_version,capabilities:t.capabilities})),
            ...declaredBaseTemplates(members),
          ].filter(t=>!listed.some(h=>h.harness_id===t.harness_id&&h.registration_version))});return true;
      }
      if(req.method==='POST'&&route==='/harnesses'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>12000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        const fields=['source_harness_id','instance_id','name'];
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>!fields.includes(k))||
          fields.some(k=>typeof body[k]!=='string'||!body[k].trim()))throw fail(400,'invalid_harness_registration');
        const listed=await call('/harnesses');
        // 基座 id 以**平台知道的**为准：内置那几家 + 声明出来的。
        const knownBases=new Set([...BASE_HARNESSES,...declaredBaseTemplates(listed).map(t=>t.harness_id)]);
        if(!ident(body.source_harness_id)||!knownBases.has(body.source_harness_id)||
          !ident(body.instance_id)||body.name.length>120)
          throw fail(400,'invalid_harness_registration');
        if(!windowIdFor(body.source_harness_id,body.instance_id))
          throw fail(400,'instance_id_must_be_member_or_window');
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
        const members=await call('/harnesses');
        // 内置那几家用本地模板；声明出来的用**平台给的**模板（同一份，别自己拼）。
        const template=BASE_ADAPTERS[body.harness_id]||
          declaredBaseTemplates(members).find(t=>t.harness_id===body.harness_id);
        if(!template)throw fail(400,'unknown_base_harness');
        const registration={registered_by:operator,harness_id:body.harness_id,name:template.name,
          provider:template.provider,adapter:template.adapter,
          adapter_version:template.adapter_version||'1.0.0',
          capabilities:template.capabilities||['coding'],max_parallel_tasks:1,status:'online',
          registration_version:template.registration_version||'reg-001'};
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
      if(req.method==='GET'&&route==='/outputs'){
        // **每个窗口都说了什么**：一次请求看完本群所有活、每一次尝试的 CLI 原话。
        //
        // 为什么要放在服务端而不是让浏览器自己去问：一条活一次请求，几十条活
        // 就是几十个往返，浏览器那边先把页面卡住。服务端并行取，浏览器只等一次。
        //
        // attempt_id 是**算出来的**（`{task_id}.a{attempt_no}`，见
        // backend/app/claim/service.py），所以不需要先捞一遍事件才知道有哪些
        // 尝试 —— 直接按 1..attempt_no 枚举即可；平台对不存在的 attempt 会回
        // `{output: null}`，那种直接跳过。
        const MAX_ATTEMPTS=8; // 一条活最多摊开多少次尝试（平台的 max_attempts 通常是 3）
        const group=await call('/groups/'+encodeURIComponent(config.group)+'/tasks');
        const taskIds=new Set((group.tasks||[]).map(t=>t.task_id));
        // 每一轮**真正的结果**在事件里，不在 attempt 的状态列上 ——
        // 那列在提交之后就停在 verifying/in_progress 不动了（真数据实测：一条
        // 已经 merged 的活，它的 attempt 状态还是 verifying）。事件才记着
        // 这一轮是"交上去了"还是"放弃了"，以及为什么放弃。
        //
        // 事件**整群一次取**（平台的 /events 一页 1000 条，这个群的历史
        // 一次就装得下），而不是逐条活去取：逐活取要多发 40 个请求，
        // 真平台上这一下就是 3.7 秒。取回来再按本群的活裁掉。
        const outcomes=new Map();
        let since=0;
        for(let page=0;page<10;page+=1){
          const res=await call('/events?since_seq='+since+'&limit=1000').catch(()=>null);
          const events=(res&&res.events)||[];
          for(const evt of events){
            if(!taskIds.has(evt.aggregate_id))continue;
            const aid=evt.payload&&evt.payload.attempt_id;
            if(!aid)continue;
            const name=String(evt.event_name||'');
            const reason=String((evt.payload&&evt.payload.reason)||'');
            if(name==='submit_result')outcomes.set(evt.aggregate_id+'|'+aid,{outcome:'交上去了',reason:''});
            else if(name==='abandon')outcomes.set(evt.aggregate_id+'|'+aid,{outcome:'放弃了',reason});
            else if(name==='release')outcomes.set(evt.aggregate_id+'|'+aid,{outcome:'放开了',reason});
            else if(name==='claim_for_revision'||name==='reassign')outcomes.set(evt.aggregate_id+'|'+aid,{outcome:'被换掉了',reason});
            else if(!outcomes.has(evt.aggregate_id+'|'+aid))outcomes.set(evt.aggregate_id+'|'+aid,{outcome:'',reason:''});
          }
          if(!res||!res.has_more)break;
          since=Number(res.next_seq||since);
        }
        const rows0=(group.tasks||[]).map(async t=>{
          const total=Number(t.attempt_no||0);
          if(!total)return null;
          const from=Math.max(1,total-MAX_ATTEMPTS+1);
          const attempts=await Promise.all(Array.from({length:total-from+1},(_,i)=>from+i).map(async n=>{
            const attemptId=`${t.task_id}.a${n}`;
            const out=await call('/tasks/'+encodeURIComponent(t.task_id)+'/output?attempt_id='+encodeURIComponent(attemptId)).catch(()=>null);
            const output=out&&out.output?out.output:null;
            if(!output)return null;
            const stdout=String(output.stdout||'');
            const stderr=String(output.stderr||'');
            if(!stdout.trim()&&!stderr.trim())return null;
            const round=outcomes.get(t.task_id+'|'+attemptId)||{outcome:'',reason:''};
            // 机器日志在**服务端**就拆成人话与"做了哪些步"：浏览器只要结论，
            // 不用把几十万字的会话日志拉过去再自己拆（真数据里一条活的 stdout
            // 能有 26 KB，几十条活就是几百 KB）。
            const transcript=parseTranscript(stdout);
            return {
              attempt_no:n,
              attempt_id:attemptId,
              attempt_status:output.attempt_status||null,
              outcome:round.outcome,
              reason:round.reason,
              harness_id:output.harness_id||null,
              current:Boolean(output.current),
              // 人话：它说了什么（最后一段在前）与它做了哪些步。
              texts:transcript.texts.map((item)=>({kind:item.kind,text:item.text})),
              tools:transcript.tools.slice(-12).map((item)=>({label:item.label,status:item.status})),
              // 原始记录只留尾巴，给想看原文的人（展开才看）。
              stdout_tail:stdout.slice(-2000),
              stderr:stderr.slice(-1500),
            };
          }));
          const live=attempts.filter(Boolean);
          if(!live.length)return null;
          return {
            task_id:t.task_id,
            title:t.title||t.task_id,
            status:t.status,
            owner:t.owner||null,
            updated_at:t.updated_at||null,
            attempt_total:total,
            attempts:live,
          };
        });
        const rows=(await Promise.all(rows0)).filter(Boolean);
        // 结论里常有编号（FD-…、attempt .a1、commit 的 hash、exclusive_paths）——
        // 让**平台**统一洗成人话（规则在平台的翻译服务里，界面不自己造一套）。
        await humanizeRows(rows);
        json(res,200,{outputs:rows,project_id:config.project,group_id:config.group});return true;
      }
      if(req.method==='GET'&&route==='/user-keys'){
        // 用户自己的 API key：平台替用户保管，但**不自己花** —— 要用得用户点头。
        const result=await call('/pool/user-keys');
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/user-keys'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>8000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body))throw fail(400,'invalid_json');
        const result=await call('/pool/user-keys',{
          label:String(body.label||''),
          protocol:String(body.protocol||'openai_chat'),
          base_url:String(body.base_url||''),
          key:String(body.key||''),
          models:Array.isArray(body.models)?body.models.map(String):[],
        });
        json(res,200,result);return true;
      }
      const keyId=/^\/user-keys\/([A-Za-z0-9_-]+)(\/(approve|deny))?$/.exec(route);
      if(keyId){
        const id=encodeURIComponent(keyId[1]);
        if(req.method==='DELETE'&&!keyId[2]){
          const result=await call('/pool/user-keys/'+id,undefined,undefined,'DELETE');
          json(res,200,result);return true;
        }
        if(req.method==='POST'&&keyId[3]){
          // keyId[2] 是 "/approve"（带斜杠），keyId[3] 才是动作名 —— 拼 URL 用后者，
          // 否则会拼成 ".../k123//approve"，平台那边是 404（点"同意"一点反应也没有）。
          let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>2000)throw fail(413,'body_too_large');}
          let body={};if(raw.trim()){try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}}
          const result=await call('/pool/user-keys/'+id+'/'+keyId[3],{mode:String(body.mode||'once')});
          json(res,200,result);return true;
        }
      }
      if(req.method==='POST'&&(route==='/wording/translate'||route==='/wording/summarize')){
        // 平台自己的"翻译"：机器写的文本 → 人话。界面只负责显示。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>200000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw||'{}');}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body))throw fail(400,'invalid_json');
        // 规则层很快；模型层要跑管理员那台 CLI（几十秒），所以给更长的时限。
        const timeoutMs=route.endsWith('/summarize')?120000:10000;
        const result=await call('/'+route.replace(/^\//,''),body,undefined,undefined,timeoutMs);
        json(res,200,result);return true;
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
      if(req.method==='POST'&&route==='/harnesses/provision'){
        // 让人在办公室点一下就把新窗口变成**能干活**：发凭据、写 key、起 worker。
        // 人只给一个 harness_id；要跑什么命令全由服务端拼（见 window-provisioner）。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>4000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>k!=='harness_id')||typeof body.harness_id!=='string')
          throw fail(400,'invalid_provision_request');
        return await provisionRoute(body.harness_id,res);
      }
      if(req.method==='POST'&&route==='/harnesses/stop'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>4000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||Object.keys(body).some(k=>k!=='harness_id')||typeof body.harness_id!=='string')
          throw fail(400,'invalid_stop_request');
        try{ json(res,200,provisioner.stop(body.harness_id)); }
        catch(error){ throw fail(error.status||503, error.message||'window_stop_failed'); }
        return true;
      }
      if(req.method==='GET'&&route==='/availability'){        // 谁现在能用、用的哪个模型、最近有没有失败（平台侧的事实）。
        const availability=await call('/adapter/availability');
        json(res,200,availability);return true;
      }
      if(req.method==='GET'&&/^\/harnesses\/[A-Za-z0-9_-]{1,100}\/runs$/.test(route)){
        // 这个窗口**自己**跑过的 CLI 会话：干活的那一半 + 对话的那一半（审阅方案、
        // 细化需求、回答问题）。只读，透传平台 —— 办公室不自己造一份。
        const harnessId=route.split('/')[2];
        const runs=await call('/harnesses/'+harnessId+'/runs');
        json(res,200,runs);return true;
      }
      if(req.method==='GET'&&/^\/harnesses\/[A-Za-z0-9_.-]{1,100}\/credentials$/.test(route)){
        // 这个窗口手上有**几条**平台凭据、**哪条在用**。
        //
        // 平台只存哈希，回的是指纹（哈希前 12 位）；凭据明文只在本机
        // data/<窗口>.key 里。用人的凭据读：凭据归人管，窗口自己拿不到这一面。
        const harnessId=route.split('/')[2];
        const list=await call('/harnesses/'+encodeURIComponent(harnessId)+'/credentials');
        json(res,200,list);return true;
      }
      if(req.method==='POST'&&/^\/harnesses\/[A-Za-z0-9_.-]{1,100}\/credentials\/revoke$/.test(route)){
        // 人指定吊销**一条**：浏览器只给指纹。谁在操作由平台按 token 认
        // （decided_by 由服务端填），不让浏览器自称身份。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>4000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body!=='object')throw fail(400,'invalid_revoke_request');
        if(Object.keys(body).some(k=>!['fingerprint','reason','force'].includes(k)))
          throw fail(400,'invalid_revoke_request');
        if(typeof body.fingerprint!=='string'||!/^sha256:[0-9a-f]{6,64}$/.test(body.fingerprint.trim()))
          throw fail(400,'invalid_revoke_request');
        if(body.reason!==undefined&&(typeof body.reason!=='string'||body.reason.length>300))
          throw fail(400,'invalid_revoke_request');
        if(body.force!==undefined&&typeof body.force!=='boolean')throw fail(400,'invalid_revoke_request');
        const harnessId=route.split('/')[2];
        const result=await call('/harnesses/'+encodeURIComponent(harnessId)+'/credentials/revoke',
          {fingerprint:body.fingerprint.trim(),decided_by:await scope(),
            reason:body.reason||'',force:body.force===true});
        json(res,200,result);return true;
      }
      if(req.method==='GET'&&route==='/installers'){
        // 「这台电脑上还缺哪个 CLI、官方的安装地址是什么」。
        //
        // 平台是对着**跑后端的那台机器**探测的（问 PATH 与已知安装目录、
        // 读一行版本号）。人在办公室点「重新探测」时，探的就是这台机器 ——
        // 所以界面必须说清是哪台，不能让人以为探的是自己面前的笔记本。
        for(const key of url.searchParams.keys()){
          if(key!=='probe_local'&&key!=='platform')throw fail(400,'unexpected_query');
        }
        const params=new URLSearchParams();
        const probeLocal=url.searchParams.get('probe_local');
        if(probeLocal!==null)params.set('probe_local',probeLocal==='true'||probeLocal==='1'?'true':'false');
        const platformParam=url.searchParams.get('platform');
        if(platformParam)params.set('platform',platformParam);
        const query=params.toString();
        const installers=await call('/harnesses/installers'+(query?'?'+query:''),undefined,undefined,undefined,60000);
        json(res,200,installers);return true;
      }
      if(req.method==='POST'&&route==='/model-selection'){
        // 人给某个窗口指定「用自带模型 / 用额度池的哪个模型」。平台照办：
        // 下一轮这个窗口干活时就按它安排。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>8000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body))throw fail(400,'invalid_json');
        const result=await call('/adapter/model-selection',{
          harness_id:String(body.harness_id||''),
          model_mode:String(body.model_mode||'cli'),
          model_id:body.model_id==null?'':String(body.model_id),
        });
        json(res,200,result);return true;
      }
      if(req.method==='GET'&&route==='/plan'){
        // 方案定稿：给人看的那一份（草稿 + 传阅记录），以及由人点「批准并派单」。
        const plan=await call('/groups/'+encodeURIComponent(config.group)+'/plans');
        // 批准之前人读的就是这一份，所以先把方案里的机器标记洗成人话。
        await humanizePlan(plan);
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
      if(req.method==='POST'&&route==='/plan/reject'){
        // 驳回是批准的**对称面**：人不能只会点头。R10 §10 有 `draft | plan.reject`
        // 这条边（落到 draft_rejected），旧的人操作面里本来就有「驳回计划」。
        // 原因必填 —— 没有原因的驳回在平台的决策记录里只剩一个动作。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>20000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body.plan_id!=='string'||!ident(body.plan_id)||
          typeof body.reason!=='string'||!body.reason.trim())throw fail(400,'invalid_plan_fields');
        const result=await call('/plans/'+encodeURIComponent(body.plan_id)+'/reject',
          {decided_by:operator,reason:body.reason.trim()});
        json(res,200,result);return true;
      }
      if(req.method==='GET'&&route==='/pool'){
        // 平台自己的额度池：池子里有哪些额度、谁的额度被谁花了。
        // 池子的接口只认 harness 主体，所以这里用管理员 harness 的凭据读。
        // 冷启动那一次要问三个 CLI 的名册（各 spawn 一次），10 秒不够 —— 超时了
        // 办公室会以为「池子是空的」。
        const pool=await call('/pool/status',undefined,config.adminTokenFile,undefined,30000);
        json(res,200,pool);return true;
      }
      if(req.method==='POST'&&route==='/pool/declaration'){
        // 人给「CLI 自己说它能用」的模型定档：免费还是收费由人说了算。平台写进
        // 声明（沿用同一家的调用约定）后重探一遍，立刻就能选。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>8000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body))throw fail(400,'invalid_json');
        const result=await call('/pool/declaration',{
          cli:String(body.cli||''),
          model:String(body.model||''),
          tier:String(body.tier||''),
        },undefined,'POST',30000);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/pool/roster-probe'){
        // 并行探测各家 CLI 都有什么模型；探完一家就记一家，界面轮询下面的 GET。
        const result=await call('/pool/roster-probe',{},undefined,'POST',30000);
        json(res,200,result);return true;
      }
      if(req.method==='GET'&&route==='/pool/roster-probe'){
        const result=await call('/pool/roster-probe',undefined,undefined,undefined,30000);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/pool/model-test'){
        // 测一个模型能不能用：真的调一次（花一次额度）。超时给足 —— 走 CLI 的模型
        // 要 spawn 一个进程，慢的那家能到十几秒。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>4000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body.model!=='string'||!body.model.trim()||body.model.length>200)
          throw fail(400,'invalid_model_test_request');
        const result=await call('/pool/model-test',{model:body.model.trim()},undefined,'POST',180000);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/pool/upstream-tier'){
        // 非 CLI 上游（各家 CLI 自己配置里那几条）的档位：它们的默认值在代码里，
        // 这里让人一句话覆盖它，或撤回到默认。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>4000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body.name!=='string'||typeof body.tier!=='string'||
           !body.name.trim()||body.name.length>120)
          throw fail(400,'invalid_upstream_tier_request');
        const result=await call('/pool/upstream-tier',{name:body.name.trim(),tier:body.tier.trim().toLowerCase()},undefined,'POST',30000);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/pool/upstream-tier/withdraw'){
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>4000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body.name!=='string'||!body.name.trim()||body.name.length>120)
          throw fail(400,'invalid_upstream_tier_request');
        const result=await call('/pool/upstream-tier/withdraw',{name:body.name.trim()},undefined,'POST',30000);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/pool/declaration/withdraw'){
        // 撤掉一条定档：档位是人说的一句话，人会点错。撤的只是**人写的声明**；
        // 它若盖住了内置档位，撤完内置那条照原样回来。
        let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>4000)throw fail(413,'body_too_large');}
        let body;try{body=JSON.parse(raw);}catch{throw fail(400,'invalid_json');}
        if(!body||Array.isArray(body)||typeof body.cli!=='string'||typeof body.model!=='string'||
           !body.cli.trim()||!body.model.trim()||body.cli.length>100||body.model.length>200)
          throw fail(400,'invalid_withdraw_request');
        const result=await call('/pool/declaration/withdraw',{cli:body.cli.trim(),model:body.model.trim()},undefined,'POST',30000);
        json(res,200,result);return true;
      }
      if(req.method==='POST'&&route==='/pool/probe'){
        // 人点的「重新探一遍」：平台清掉发现缓存，重读一遍各家 CLI 的额度，
        // 回一份新的清单（新增的免费额度、变成收费的额度都要看得见）。
        const pool=await call('/pool/probe',undefined,config.adminTokenFile,'POST',30000);
        json(res,200,pool);return true;
      }
      if(req.method==='GET'&&route==='/assistant/runs'){
        // 助手这一轮做了什么（引擎写在服务器本地的 trace，这里读成摘要）。
        const result=await call('/assistant/runs');
        json(res,200,result);return true;
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
        // 改动与事件是**锦上添花**：平台那边算不出 diff（提交不在正式仓库里、
        // 正式仓库路径不对）时，它会回 500 —— 那是"没有改动"，不是"这条活
        // 坏了"。所以这两样各自失败就各自空着，不能把整条详情一起带崩
        // （那会让人以为这条活坏了，其实只是看不到 diff）。
        const diff=await call('/tasks/'+id+'/diff?include_patch=true').catch(()=>null);
        const events=await call('/tasks/'+id+'/events?limit=200').catch(()=>({events:[]}));
        // 规格（工单/验收/路径）一起给：办公室要让人**看着工单**做决定。
        // 工单里可能夹着平台追加的**现场报告**（一坨机器 JSON）—— 拆开，
        // 人看的是工单，机器那部分单独摆（见 splitHandoff）；里面的失败原因
        // 顺手翻成人话（见 humanizeHandoff）。
        const spec=await humanizeHandoff(splitHandoff(task.spec||{}));
        json(res,200,{task:{...safeTask(task),...(task.spec?{spec}:{})},diff,events});return true;
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
  // 「让这个窗口能干活」：发凭据 + 起它自己的 worker。
  //
  // 只给**平台上已经注册**的窗口开：先问注册表，注册表里没有就是 404 —— 浏览器
  // 给什么 id 都不能让本机去跑一个没登记的东西。
  const provisioner=createWindowProvisioner({
    repoRoot:path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..','..'),
    python:config.pythonPath,
    canonicalRepo:config.canonicalRepo,
    worktreeRoot:config.worktreeRoot,
    group:config.group,
    upstream:config.upstream,
    tokenFile:config.tokenFile,
  });
  async function provisionRoute(harnessId,res){
    const registered=await call('/harnesses');
    const listed=(registered.harnesses||[]).map(item=>String(item.harness_id||''));
    if(!listed.includes(harnessId))throw fail(404,'harness_not_registered');
    try{
      const result=await provisioner.provision(harnessId);
      json(res,200,result);return true;
    }catch(error){
      throw fail(error.status||503,error.message||'window_provision_failed');
    }
  }
  return {handle};
}

export function formalGatewayPlugin(config){
  const configure=server=>{const gateway=createFormalGateway(config);
    server.middlewares.use((req,res,next)=>void gateway.handle(req,res).then(handled=>{if(!handled)next();}));};
  return {name:'local-formal-dispatch',configureServer:configure,configurePreviewServer:configure};
}

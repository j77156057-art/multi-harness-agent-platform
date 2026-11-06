import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createFormalGateway} from '../server/formal-dispatch-gateway.mjs';

const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'formal-gateway-')),calls=[],decisionBodies=[];
const OPERATOR_TOKEN='apt_'+'a'.repeat(64),ADMIN_TOKEN='apt_'+'b'.repeat(64);
await fs.writeFile(path.join(tmp,'operator.key'),OPERATOR_TOKEN);
await fs.writeFile(path.join(tmp,'admin.key'),ADMIN_TOKEN);
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(server.address().port)));
const close=server=>new Promise(resolve=>server.close(resolve));
const upstream=http.createServer((req,res)=>{
  calls.push({path:req.url,authorization:req.headers.authorization});
  let data={};
  // 同一把 whoami，两种凭据两种身份：人的 token 是 human，管理员 harness 的
  // token 是 harness —— 网关必须按凭据分辨，不能只看路径。
  if(req.url==='/api/v1/auth/whoami')data=req.headers.authorization==='Bearer '+ADMIN_TOKEN
    ?{mode:'enforce',subject:{type:'harness',id:'codex-window-admin'}}
    :{mode:'enforce',subject:{type:'human',id:'operator'}};
  if(req.url==='/api/v1/groups?project_id=P')data={groups:[{project_id:'P',group_id:'G'}]};
  if(req.url==='/api/v1/harnesses')data={harnesses:[{harness_id:'codex',registration_version:'reg-1',capabilities_json:'["coding"]'}]};
  if(req.url==='/api/v1/adapter/availability')data={availability:[{harness_id:'codex',model:'gpt-6.1-sol',usable:true,reasons:[]}]};
  // 「装 CLI」那一面：安装方式是纯数据；探测才是子进程。这条桩只证明网关转发，
  // 不证明版本命令能跑（那是后端自己的测试）。查询串必须原样带过去 ——
  // probe_local=false 是「只看安装方式、别跑子进程」，丢了它就会白跑六个进程。
  if(req.url.startsWith('/api/v1/harnesses/installers'))data={probed:false,guidance_only:true,platform:{os:'windows',arch:'x86_64'},installers:[{harness_id:'codex',display_name:'Codex CLI',provider:'OpenAI',command:'codex',official_url:'https://developers.openai.com/codex/cli/',install_command:'npm install -g @openai/codex',verify_command:'codex --version',status:'unknown',supported:true}],summary:{unknown:1}};
  if(req.url==='/api/v1/groups/G/tasks')data={tasks:[]};
  if(req.url==='/api/v1/tasks/FD-0123456789abcdef')data={task_id:'FD-0123456789abcdef',group_id:'G'};
  if(req.url==='/api/v1/tasks/FD-0123456789abcdef/diff?include_patch=true')data={available:true,patch:'verified patch'};
  // 任务 id 的形状不是网关定的：平台的派单允许任意 id（`plan-create --task T-1`、
  // 演示用的 `FLOW-1101-2`）。这条桩就是那种形状 —— 网关以前一律 404，
  // 界面于是显示「动作记录 暂无记录」。
  if(req.url==='/api/v1/tasks/FLOW-1101-2')data={task_id:'FLOW-1101-2',group_id:'G'};
  if(req.url==='/api/v1/tasks/FLOW-1101-2/diff?include_patch=true')data={available:true,patch:'flow patch'};
  if(req.url==='/api/v1/tasks/FLOW-1101-2/events?limit=200')data={events:[{seq:1,event_name:'claim'}]};
  if(req.url==='/api/v1/human/review/claim'&&req.method==='POST')data={review_id:'rv-1',reviewed_commit:'a'.repeat(40),status:'claimed'};
  if(req.url==='/api/v1/human/review/submit'&&req.method==='POST')data={event:'approve',status:'approved',review_id:'rv-1'};
  if(req.url==='/api/v1/review/claim'&&req.method==='POST')data={review_id:'rv-1',reviewed_commit:'a'.repeat(40),status:'claimed'};
  if(req.url==='/api/v1/review/submit'&&req.method==='POST')data={event:'approve',status:'approved',review_id:'rv-1'};
  if(req.url==='/api/v1/tasks/FD-ffffffffffffffff')data={task_id:'FD-ffffffffffffffff',group_id:'OTHER'};
  if(req.url==='/api/v1/tasks/FD-0123456789abcdef/experiences')data={experiences:[{experience_id:'exp-0123456789abcdef01234567'}]};
  if(req.url==='/api/v1/tasks/FD-0123456789abcdef/experience-injections')data={injections:[{experience_id:'exp-0123456789abcdef01234567',layer:'admin_dispatch'}]};
  if(req.url==='/api/v1/experience-injection'&&req.method==='GET')data={enabled:false,updated_by:'',updated_at:''};
  if(req.url==='/api/v1/experience-injection'&&req.method==='POST')data={enabled:true,updated_by:'operator',updated_at:'now'};
  if(req.url==='/api/v1/tasks/FD-0123456789abcdef/experiences/exp-0123456789abcdef01234567/decision'&&req.method==='POST'){
    let raw='';req.on('data',chunk=>{raw+=chunk;});req.on('end',()=>decisionBodies.push(JSON.parse(raw)));
    data={confirmation:{status:'confirmed',decided_by:'operator'},injection_layer:'admin_dispatch'};
  }
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
});
const upstreamPort=await listen(upstream);
const gateway=createFormalGateway({upstream:`http://127.0.0.1:${upstreamPort}`,tokenFile:path.join(tmp,'operator.key'),adminTokenFile:path.join(tmp,'admin.key'),project:'P',group:'G'});
const server=http.createServer((req,res)=>void gateway.handle(req,res).then(done=>{if(!done){res.statusCode=404;res.end();}}));
const port=await listen(server),base=`http://127.0.0.1:${port}`;
try{
  const status=await fetch(base+'/api/formal/status');assert.equal(status.status,200);
  const text=await status.text();assert(!text.includes('apt_'));assert(JSON.parse(text).connected);
  const count=calls.length;
  const cross=await fetch(base+'/api/formal/dispatch',{method:'POST',headers:{Origin:'http://evil.example','X-Office-Formal':'1'},body:'{}'});assert.equal(cross.status,403);assert.equal(calls.length,count);
  const noOrigin=await fetch(base+'/api/formal/dispatch',{method:'POST',headers:{'X-Office-Formal':'1'},body:'{}'});assert.equal(noOrigin.status,403);
  const wrongHost=await new Promise(resolve=>{const request=http.request(new URL('/api/formal/status',base),{hostname:'127.0.0.1',port,headers:{Host:'evil.example'}},response=>{response.resume();response.on('end',()=>resolve(response));});request.end();});assert.equal(wrongHost.statusCode,421);
  const adapter=await fetch(base+'/api/formal/adapter/claim',{method:'POST',headers:{Origin:base,'X-Office-Formal':'1'},body:'{}'});assert.equal(adapter.status,404);assert(!calls.some(c=>c.path.includes('adapter/claim')));
  // 人工派单这条路已经被删掉：UI 不得再能直接派单（派单只走管理员 Harness 的
  // /admin/dispatch）。所以这个路由必须是 404，且上游一次都不能被叫到。
  const extra=await fetch(base+'/api/formal/dispatch',{method:'POST',headers:{Origin:base,'X-Office-Formal':'1'},body:JSON.stringify({request_id:'x',title:'x',goal:'x',harness_id:'codex',exclusive_paths:['docs/x'],acceptance:'x',decided_by:'fake'})});assert.equal(extra.status,404);assert(!calls.some(c=>c.path.includes('/dispatch')));
  // 「谁现在能用」是只读的观察面：网关透传平台的可用性，不做任何写动作。
  const availability=await fetch(base+'/api/formal/availability');assert.equal(availability.status,200);assert(Array.isArray((await availability.json()).availability));
  // 安装清单：查询串必须转发（probe_local=false 表示「别跑版本命令」）。
  const installers=await fetch(base+'/api/formal/installers?probe_local=false');assert.equal(installers.status,200);
  const installersBody=await installers.json();assert.equal(installersBody.probed,false);assert.equal(installersBody.installers.length,1);
  assert(calls.some(c=>c.path==='/api/v1/harnesses/installers?probe_local=false'));
  // 不认识的查询串不放行：这条面只有「探不探」一个开关，多塞参数说明调用方搞错了。
  const bogusQuery=await fetch(base+'/api/formal/installers?token=leak');assert.equal(bogusQuery.status,400);
  const detail=await fetch(base+'/api/formal/tasks/FD-0123456789abcdef');assert.equal(detail.status,200);assert.equal((await detail.json()).diff.patch,'verified patch');
  // 非 FD- 形状的 id 也要能点开：越权由 group_id 比对挡，不由 id 形状挡。
  const otherShape=await fetch(base+'/api/formal/tasks/FLOW-1101-2');assert.equal(otherShape.status,200);
  const otherShapeBody=await otherShape.json();
  assert.equal(otherShapeBody.task.task_id,'FLOW-1101-2');
  assert.equal(otherShapeBody.diff.patch,'flow patch');
  assert.equal(otherShapeBody.events.events.length,1);
  const otherShapeOutput=await fetch(base+'/api/formal/tasks/FLOW-1101-2/output?attempt_id=FLOW-1101-2.a1');
  assert(otherShapeOutput.status===200||otherShapeOutput.status===502,`output route must be routed, got ${otherShapeOutput.status}`);
  const reviewClaim=await fetch(base+'/api/formal/review/claim',{method:'POST',headers:{Origin:base,'X-Office-Formal':'1','Content-Type':'application/json'},body:JSON.stringify({task_id:'FD-0123456789abcdef',attempt_id:'FD-0123456789abcdef.a1'})});assert.equal(reviewClaim.status,200);assert.equal((await reviewClaim.json()).review_id,'rv-1');
  // 管理员动作必须用管理员自己的凭据：人的 token 不能借到 harness 的权限。
  const adminCall=calls.find(c=>c.path==='/api/v1/review/claim');assert(adminCall&&adminCall.authorization==='Bearer '+ADMIN_TOKEN);
  const reviewSubmit=await fetch(base+'/api/formal/review/submit',{method:'POST',headers:{Origin:base,'X-Office-Formal':'1','Content-Type':'application/json'},body:JSON.stringify({task_id:'FD-0123456789abcdef',attempt_id:'FD-0123456789abcdef.a1',review_id:'rv-1',verdict:'approve',reviewed_commit:'a'.repeat(40),review_revision:1,findings:[]})});assert.equal(reviewSubmit.status,200);assert.equal((await reviewSubmit.json()).status,'approved');
  const foreign=await fetch(base+'/api/formal/tasks/FD-ffffffffffffffff');assert.equal(foreign.status,404);assert(!calls.some(c=>c.path.includes('FD-ffffffffffffffff/diff')));
  // 经验读面与写面都走操作人凭据；网关覆盖操作人，不接受浏览器伪造身份。
  const candidates=await fetch(base+'/api/formal/tasks/FD-0123456789abcdef/experiences');assert.equal(candidates.status,200);assert.equal((await candidates.json()).experiences.length,1);
  const decision=await fetch(base+'/api/formal/tasks/FD-0123456789abcdef/experiences/exp-0123456789abcdef01234567/decision',{method:'POST',headers:{Origin:base,'X-Office-Formal':'1','Content-Type':'application/json'},body:JSON.stringify({action:'confirm'})});
  assert.equal(decision.status,200);assert.equal(decisionBodies[0].decided_by,'operator');
  const injectionSwitch=await fetch(base+'/api/formal/experience-injection',{method:'POST',headers:{Origin:base,'X-Office-Formal':'1','Content-Type':'application/json'},body:JSON.stringify({enabled:true})});assert.equal(injectionSwitch.status,200);assert.equal((await injectionSwitch.json()).enabled,true);
  const injections=await fetch(base+'/api/formal/tasks/FD-0123456789abcdef/experience-injections');assert.equal(injections.status,200);assert.equal((await injections.json()).injections[0].layer,'admin_dispatch');
  // 窗口自己跑过的 CLI 会话：只读，透传平台。id 不认识形状的（带斜杠/点）
  // 不放行 —— 它会被拼进上游路径，拼坏了就是把路径当查询串用。
  const runs=await fetch(base+'/api/formal/harnesses/claude-code/runs');assert.equal(runs.status,200);
  assert(calls.some(c=>c.path==='/api/v1/harnesses/claude-code/runs'));
  const badRuns=await fetch(base+'/api/formal/harnesses/..%2Fetc/runs');assert(badRuns.status===404||badRuns.status===400);
  assert(!calls.some(c=>c.path.includes('..%2Fetc')));
  await close(upstream);
  const offline=await fetch(base+'/api/formal/status');assert.equal(offline.status,503);
  assert.throws(()=>createFormalGateway({upstream:'http://external.example',tokenFile:'x',project:'P',group:'G'}));
  const result={checks:30,result:'PASS',scope:'real local HTTP gateway with isolated stub upstream; not model execution'};
  await fs.mkdir('handoff/codex-formal-dispatch-2026-10-14',{recursive:true});await fs.writeFile('handoff/codex-formal-dispatch-2026-10-14/gateway-check-final.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await close(server);if(upstream.listening)await close(upstream);await fs.rm(tmp,{recursive:true,force:true});}

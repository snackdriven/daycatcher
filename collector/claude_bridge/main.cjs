'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {app,BrowserWindow,session}=require('electron');
const {runScan}=require('./adapter.cjs');
const args=process.argv.slice(1),configIndex=args.indexOf('--config');
if(configIndex<0||!args[configIndex+1])throw Error('CONFIG_REQUIRED');
const config=JSON.parse(fs.readFileSync(args[configIndex+1],'utf8'));
for(const key of ['data_dir','db','package_root','profile_dir'])if(typeof config[key]!=='string'||!path.isAbsolute(config[key]))throw Error('ABSOLUTE_PATH_REQUIRED');
if(typeof config.python!=='string')throw Error('PYTHON_REQUIRED');
if(config.since&&!Number.isFinite(Date.parse(config.since)))throw Error('SINCE_INVALID');
if(config.title_allowlist!==undefined&&(!Array.isArray(config.title_allowlist)||config.title_allowlist.some(x=>typeof x!=='string')))throw Error('ALLOWLIST_INVALID');
const bounded=(v,fallback,min,max)=>{const n=v??fallback;if(!Number.isInteger(n)||n<min||n>max)throw Error('SCAN_LIMIT_INVALID');return n;};
const poll=bounded(config.poll_seconds,60,10,86400),maxPages=bounded(config.max_pages,5,1,1000),maxConversations=bounded(config.max_conversations,100,1,10000);
const since=config.since||new Date(Date.now()-86400000).toISOString();
fs.mkdirSync(config.data_dir,{recursive:true});
app.setPath('userData',config.profile_dir);
const lockPaths=[path.join(config.data_dir,'claude-bridge.lock'),path.join(config.profile_dir,'collector-bridge.lock')];
fs.mkdirSync(config.profile_dir,{recursive:true});
const held=[];
function release(){for(const file of held){try{fs.unlinkSync(file);}catch{}}held.length=0;}
function acquire(file){
 try{const fd=fs.openSync(file,'wx');fs.writeFileSync(fd,String(process.pid));fs.closeSync(fd);held.push(file);}
 catch(error){
  if(error.code!=='EEXIST')throw error;
  const pid=Number(fs.readFileSync(file,'utf8'));
  if(!Number.isSafeInteger(pid)||pid<1)throw Error('LOCK_INVALID');
  try{process.kill(pid,0);}catch(e){if(e.code==='ESRCH'){fs.unlinkSync(file);return acquire(file);}throw Error('LOCK_CHECK_FAILED');}
  throw Error('BRIDGE_ALREADY_RUNNING');
 }
}
try{for(const file of lockPaths)acquire(file);}catch(error){release();throw error;}
process.on('exit',release);
let active=true,window,orgId=null,failures=0,current={status:'starting'};
const statusPath=path.join(config.data_dir,'claude-status.json');
function status(extra={}){current={...current,...extra,active,pid:process.pid,at:new Date().toISOString(),hidden:!args.includes('--login')};const tmp=statusPath+'.tmp';fs.writeFileSync(tmp,JSON.stringify(current)+'\n');fs.renameSync(tmp,statusPath);}
function core(command,additional=[],input){return new Promise((resolve,reject)=>{
 const child=spawn(config.python,['-m','journal_collector',command,'--db',config.db,...additional],{cwd:config.package_root,windowsHide:true,env:{...process.env,PYTHONPATH:config.package_root},stdio:['pipe','pipe','pipe']});
 let output='',size=0;const timer=setTimeout(()=>{child.kill();reject(Error('CORE_TIMEOUT'));},60000);
 child.stdout.on('data',chunk=>{size+=chunk.length;if(size>1048576){child.kill();return;}output+=chunk;});child.stderr.on('data',()=>{});
 child.on('error',()=>{clearTimeout(timer);reject(Error('CORE_LAUNCH_FAILED'));});
 child.on('close',code=>{clearTimeout(timer);if(code!==0||size>1048576)return reject(Error('CORE_REQUEST_FAILED'));try{resolve(JSON.parse(output));}catch{reject(Error('CORE_RESPONSE_INVALID'));}});
 child.stdin.on('error',()=>{});child.stdin.end(input===undefined?undefined:JSON.stringify(input));
});}
async function ingest(batch){for(let attempt=0;attempt<2;attempt++){try{return await core('ingest',[],batch);}catch(error){if(attempt===1)throw error;}}}
const stopping=()=>fs.existsSync(path.join(config.data_dir,'STOP'));
async function delay(ms){const end=Date.now()+ms;while(active&&Date.now()<end&&!stopping())await new Promise(r=>setTimeout(r,Math.min(500,end-Date.now())));}
app.on('before-quit',()=>{active=false;status();release();});
app.on('window-all-closed',()=>app.quit());
app.whenReady().then(async()=>{
 const agentSession=session.fromPartition(config.partition||'persist:ownyourchat-probe');
 agentSession.setPermissionRequestHandler((_a,_b,callback)=>callback(false));agentSession.setPermissionCheckHandler(()=>false);
 agentSession.webRequest.onCompleted({urls:['https://claude.ai/api/*']},details=>{
  const u=new URL(details.url),match=u.pathname.match(/^\/api\/organizations\/([a-f0-9-]{36})(?:\/|$)/i);
  if(match&&details.statusCode===200)orgId=match[1];
 });
 window=new BrowserWindow({show:args.includes('--login'),width:1100,height:800,webPreferences:{session:agentSession,contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false}});
 window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
 window.webContents.on('will-navigate',(event,url)=>{if(new URL(url).origin!=='https://claude.ai')event.preventDefault();});
 const read=async url=>{const parsed=new URL(url);if(parsed.origin!=='https://claude.ai'||!parsed.pathname.startsWith('/api/organizations/'))throw Error('URL_REJECTED');const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),45000);try{const response=await agentSession.fetch(url,{method:'GET',credentials:'include',redirect:'error',signal:abort.signal});if(!response.ok)throw Error('HTTP_'+response.status);return await response.json();}finally{clearTimeout(timer);}};
 do{
  if(stopping())break;
  if(!orgId){status({status:'discovering_authentication'});try{await window.loadURL('https://claude.ai/new');}catch{}await delay(5000);}
  if(stopping())break;
  if(args.includes('--login')){status({status:orgId?'login_scope_observed':'authentication_required'});await delay(1000);continue;}
  if(orgId){
   status({status:'scanning'});
   const result=await runScan({orgId,maxPages,maxConversations,pageSize:30,since,titleAllowlist:config.title_allowlist,read,getCursor:(source,key)=>core('cursor',['--source',source,'--key',key]),ingest});
   status(result);failures=result.status==='error'||result.status==='authentication_required'?failures+1:0;
   if(result.status==='authentication_required')orgId=null;
  }else{failures++;status({status:'authentication_required',error:'ORGANIZATION_NOT_OBSERVED'});}
  if(args.includes('--once'))break;
  await delay(Math.min(900,poll*Math.pow(2,Math.min(failures,6)))*1000);
 }while(active&&!stopping());
 app.quit();
}).catch(()=>{status({status:'error',error:'BRIDGE_RUNTIME_FAILED'});app.exit(1);});

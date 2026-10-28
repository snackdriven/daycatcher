'use strict';
// One-off hidden runner. It deliberately has no login/show/focus path.
const fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {app,BrowserWindow,session}=require('electron');
const {sha}=require('./adapter.cjs');
const {validate,checkSource,inventory,collect,boundedJson}=require('./backfill_logic.cjs');
const held=[],children=new Set();let config,window,deadline,current={status:'starting'},finished=false;
const safeError=e=>/^HTTP_(401|403)$/.test(e?.message||'')?'AUTHENTICATION_REQUIRED':/^[A-Z_]+(?:_\d+)?$/.test(e?.message||'')?e.message:'BACKFILL_RUNTIME_FAILED';
function release(){for(const child of children)try{child.kill();}catch{}for(const file of held)try{fs.unlinkSync(file);}catch{}held.length=0;}
function save(name,value){const file=path.join(config.run_dir,name),tmp=file+'.tmp';fs.writeFileSync(tmp,JSON.stringify(value,null,2)+'\n');fs.renameSync(tmp,file);}
function receipt(extra){current={...current,...extra,at:new Date().toISOString(),hidden:true};save('receipt.json',current);}
function finish(code,extra){if(finished)return;finished=true;clearTimeout(deadline);try{receipt(extra);process.stdout.write(JSON.stringify(current)+'\n');}finally{release();app.exit(code);}}
function acquire(file){try{const fd=fs.openSync(file,'wx');fs.writeFileSync(fd,String(process.pid));fs.closeSync(fd);held.push(file);}catch(error){if(error.code!=='EEXIST')throw error;const pid=Number(fs.readFileSync(file,'utf8'));if(!Number.isSafeInteger(pid)||pid<1)throw Error('LOCK_INVALID');try{process.kill(pid,0);}catch(e){if(e.code==='ESRCH'){fs.unlinkSync(file);return acquire(file);}throw Error('LOCK_CHECK_FAILED');}throw Error('BRIDGE_ALREADY_RUNNING');}}
function checkStop(){if(fs.existsSync(path.join(config.run_dir,'STOP')))throw Error('STOPPED');}
function core(batch){return new Promise((resolve,reject)=>{
 checkStop();const child=spawn(config.python,['-m','journal_collector','ingest','--db',config.db,'--historical'],{cwd:config.package_root,windowsHide:true,env:{...process.env,PYTHONPATH:config.package_root},stdio:['pipe','pipe','pipe']});children.add(child);
 let output='',size=0;const timer=setTimeout(()=>{child.kill();reject(Error('CORE_TIMEOUT'));},60000);
 child.stdout.on('data',chunk=>{size+=chunk.length;if(size>1048576)child.kill();else output+=chunk;});child.stderr.on('data',()=>{});
 child.on('error',()=>{clearTimeout(timer);children.delete(child);reject(Error('CORE_LAUNCH_FAILED'));});
 child.on('close',code=>{clearTimeout(timer);children.delete(child);if(code!==0||size>1048576)return reject(Error('CORE_REQUEST_FAILED'));try{resolve(JSON.parse(output));}catch{reject(Error('CORE_RESPONSE_INVALID'));}});
 child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify(batch));
 });}
async function ingest(batch){for(let attempt=0;attempt<2;attempt++)try{return await core(batch);}catch(e){if(attempt===1)throw e;}}
process.on('exit',release);
process.on('SIGTERM',()=>finish(1,{status:'stopped',error:'STOPPED'}));
process.on('SIGINT',()=>finish(1,{status:'stopped',error:'STOPPED'}));
try{
 const args=process.argv.slice(1),index=args.indexOf('--config');if(index<0||!args[index+1]||args.includes('--login'))throw Error('CONFIG_REQUIRED');
 config=validate(JSON.parse(fs.readFileSync(args[index+1],'utf8')));
 fs.mkdirSync(config.run_dir,{recursive:true});fs.mkdirSync(config.data_dir,{recursive:true});fs.mkdirSync(config.profile_dir,{recursive:true});
 app.setPath('userData',config.profile_dir);
 for(const file of [path.join(config.data_dir,'claude-bridge.lock'),path.join(config.profile_dir,'collector-bridge.lock')])acquire(file);
 receipt({status:'starting',mode:config.mode});
 // Entire process is bounded even if Electron navigation or a child hangs.
 deadline=setTimeout(()=>finish(1,{status:'error',error:'RUN_TIMEOUT'}),600000);
 app.on('window-all-closed',()=>{if(!finished)finish(1,{status:'error',error:'WINDOW_CLOSED'});});
 app.whenReady().then(async()=>{
  checkStop();let orgId=null;
  const agentSession=session.fromPartition(config.partition||'persist:ownyourchat-probe');
  agentSession.setPermissionRequestHandler((_a,_b,callback)=>callback(false));agentSession.setPermissionCheckHandler(()=>false);
  agentSession.webRequest.onCompleted({urls:['https://claude.ai/api/*']},details=>{const u=new URL(details.url),match=u.pathname.match(/^\/api\/organizations\/([a-f0-9-]{36})(?:\/|$)/i);if(match&&details.statusCode===200)orgId=match[1];});
  window=new BrowserWindow({show:false,skipTaskbar:true,width:1100,height:800,webPreferences:{session:agentSession,contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false}});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',(event,url)=>{try{if(new URL(url).origin!=='https://claude.ai')event.preventDefault();}catch{event.preventDefault();}});
  receipt({status:'discovering_authentication'});
  // Do not extract cookies, request headers, or credentials. Observe a successful
  // first-party organization response, then use the same ambient session.
  await Promise.race([window.loadURL('https://claude.ai/new').catch(()=>{}),new Promise(resolve=>setTimeout(resolve,20000))]);
  const authEnd=Date.now()+15000;while(!orgId&&Date.now()<authEnd){checkStop();await new Promise(resolve=>setTimeout(resolve,250));}
  if(!orgId)throw Error('AUTHENTICATION_REQUIRED');checkSource(orgId,config.expected_source);
  const budget={remaining:16*1024*1024};
  const read=async url=>{
   checkStop();const parsed=new URL(url);if(parsed.origin!=='https://claude.ai'||!parsed.pathname.startsWith('/api/organizations/'+orgId+'/'))throw Error('URL_REJECTED');
   const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),45000);
   try{const response=await agentSession.fetch(url,{method:'GET',credentials:'include',redirect:'error',signal:abort.signal});if(!response.ok)throw Error('HTTP_'+response.status);return await boundedJson(response,budget);}finally{clearTimeout(timer);}
  };
  receipt({status:config.mode==='inventory'?'listing_metadata':'collecting'});
  const options={...config,orgId,read,ingest,checkStop,run_id:sha(config.run_dir).slice(0,20)};
  if(config.mode==='inventory'){
   const result=await inventory(options);save('inventory.json',result.rows);finish(0,result.summary);
  }else{
   options.inventory=JSON.parse(fs.readFileSync(path.join(config.run_dir,'inventory.json'),'utf8'));
   options.selection=JSON.parse(fs.readFileSync(path.join(config.run_dir,'selection.json'),'utf8'));
   const result=await collect(options);finish(0,{...result,since:config.since,until:config.until,coverage:'bounded window; full conversation and account coverage not asserted'});
  }
 }).catch(e=>{const error=safeError(e);finish(1,{...(e.backfill_stats||{}),status:error==='AUTHENTICATION_REQUIRED'?'authentication_required':error==='STOPPED'?'stopped':'error',error});});
}catch(e){release();const error=safeError(e);if(config?.run_dir&&path.isAbsolute(config.run_dir))try{receipt({status:'error',error});}catch{}process.stdout.write(JSON.stringify({status:'error',error,hidden:true})+'\n');app.exit(1);}

'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {normalizeConversation,runScan}=require('./adapter.cjs');
const conv={uuid:'conversation-1',updated_at:'2026-10-26T12:00:00Z'};
const msg={uuid:'message-1',sender:'human',text:'hello',created_at:'2026-10-26T11:00:00Z'};
const body=messages=>({uuid:conv.uuid,chat_messages:messages});
test('malformed/truncated trees cannot commit a complete checkpoint',()=>{
 assert.throws(()=>normalizeConversation(conv,{uuid:conv.uuid},'now'));
 assert.throws(()=>normalizeConversation(conv,body([{sender:'human',text:'missing id'}]),'now'));
 assert.throws(()=>normalizeConversation(conv,body([{...msg,text:undefined,content:'truncated'}]),'now'));
 assert.equal(normalizeConversation(conv,body([{...msg,truncated:true}]),'now').complete,false);
});
test('embedded arbitrary long text survives; unknown file keeps retry incomplete',()=>{
 const long='arbitrary\n'.repeat(10000);
 const value=normalizeConversation(conv,body([{...msg,text:'',attachments:[{file_type:'text/plain',extracted_content:long}]}]),'now');
 assert.equal(value.messages[0].text,long); assert.equal(value.complete,true);
 const unknown=normalizeConversation(conv,body([{...msg,files:[{id:'opaque'}]}]),'now');
 assert.equal(unknown.complete,false); assert.equal(unknown.messages[0].complete,false);
});
test('partial placeholder stays incomplete',()=>{
 const v=normalizeConversation(conv,body([{...msg,text:undefined,content:[]}]),'now');
 assert.equal(v.complete,false);
});
test('changed/repeated trees use acknowledged cursor; auth does not commit',async()=>{
 const cursors=new Map(),batches=[]; let requests=0,auth=false;
 const options={orgId:'org',maxPages:1,maxConversations:5,pageSize:2,observedAt:'now',
 read:async url=>{if(auth)throw Error('HTTP_401'); if(url.includes('?limit='))return [conv];requests++;return body([msg]);},
 getCursor:async(source,key)=>cursors.get(key),ingest:async batch=>{batches.push(batch);if(batch.cursor)cursors.set(batch.cursor_key,batch.cursor);return {};}};
 await runScan(options);await runScan(options);assert.equal(requests,1);
 const changed={...conv,updated_at:'2026-10-26T13:00:00Z'};
 options.read=async url=>{if(auth)throw Error('HTTP_401');if(url.includes('?limit='))return [changed];requests++;return body([{...msg,text:'edited'}]);};
 await runScan(options);assert.equal(requests,2);assert.equal(batches.at(-2).messages[0].text,'edited');
 const previous=[...cursors];auth=true;const status=await runScan(options);
 assert.equal(status.status,'authentication_required');assert.deepEqual([...cursors],previous);
 assert.equal(batches.at(-1).coverage.status,'authentication_required');
});
test('capped listing reports partial; incomplete bodies retry identical batch safely',async()=>{
 const batches=[];let count=0;
 const options={orgId:'org',maxPages:1,maxConversations:1,pageSize:1,observedAt:'now',
 read:async url=>url.includes('?limit=')?[conv]:body([{...msg,files:[{}]}]),
 getCursor:async()=>null,ingest:async b=>{batches.push(b);count++;return {};}};
 const a=await runScan(options);await runScan(options);
 assert.equal(a.status,'partial');assert.equal(a.list_reached_end,false);
 assert.equal(batches[0].batch_id,batches[2].batch_id);assert.deepEqual(batches[0].cursor,{});
});

function fixture(rows, {maxConversations=1,maxPages=1,pageSize=10,incomplete=new Set()}={}){
 const cursors=new Map(),requests=[],batches=[];
 const options={orgId:'org',maxPages,maxConversations,pageSize,observedAt:'2026-10-26T14:00:00Z',
  getCursor:async(_source,key)=>cursors.get(key),
  ingest:async b=>{assert.ok(b.coverage.status==='error'||b.coverage.status==='authentication_required'||(b.cursor&&typeof b.cursor==='object'));batches.push(b);if(b.cursor)cursors.set(b.cursor_key,b.cursor);},
  read:async url=>{const u=new URL(url);if(u.searchParams.has('limit')){const offset=Number(u.searchParams.get('offset'));return rows.slice(offset,offset+pageSize);}const id=u.pathname.split('/').at(-1);requests.push(id);return {uuid:id,chat_messages:[{...msg,uuid:'message-'+id,...(incomplete.has(id)?{files:[{}]}:{})}]};}};
 return {options,cursors,requests,batches};
}
const conversations=n=>Array.from({length:n},(_,i)=>({...conv,uuid:'conversation-'+(i+1)}));
test('body cap skips acknowledged conversations and reaches later changed rows',async()=>{
 const f=fixture(conversations(3));for(let i=0;i<3;i++)await runScan(f.options);
 assert.deepEqual(f.requests,['conversation-1','conversation-2','conversation-3']);
});
test('incomplete first body does not starve later conversations',async()=>{
 const f=fixture(conversations(3),{incomplete:new Set(['conversation-1'])});
 for(let i=0;i<3;i++)await runScan(f.options);
 assert.deepEqual(f.requests,['conversation-1','conversation-2','conversation-3']);
 assert.deepEqual(f.cursors.get('conversation:'+require('./adapter.cjs').sha('conversation-1')),{});
});
test('bounded pages resume older listings and wrap only at the end',async()=>{
 const f=fixture(conversations(5),{pageSize:2,maxConversations:10});
 let s=await runScan(f.options);assert.equal(s.status,'partial');assert.equal(f.cursors.get('scan').next_list_offset,2);
 s=await runScan(f.options);assert.equal(f.cursors.get('scan').next_list_offset,4);
 s=await runScan(f.options);assert.equal(s.list_reached_end,true);assert.equal(s.status,'partial');assert.equal(f.cursors.get('scan').next_list_offset,0);
 assert.equal(new Set(f.requests).size,5);
});
test('repeated full page cannot falsely complete pagination and preserves cursor',async()=>{
 const f=fixture(conversations(2),{pageSize:2,maxConversations:10});
 await runScan(f.options);const previous={...f.cursors.get('scan')};
 const reader=f.options.read;f.options.read=async url=>url.includes('?limit=')?conversations(2):reader(url);
 const s=await runScan(f.options);assert.equal(s.status,'error');assert.equal(s.error,'PAGINATION_STALLED');assert.deepEqual(f.cursors.get('scan'),previous);
});
test('partial changed body retains its previously acknowledged version',async()=>{
 const f=fixture(conversations(1));await runScan(f.options);
 const key='conversation:'+require('./adapter.cjs').sha('conversation-1');const prior={...f.cursors.get(key)};
 f.options.read=async url=>url.includes('?limit=')?[{...conv,updated_at:'2026-10-26T15:00:00Z'}]:body([{...msg,files:[{}]}]);
 const s=await runScan(f.options);assert.equal(s.status,'partial');assert.deepEqual(f.cursors.get(key),prior);
});
test('real Python CLI Store accepts complete and incomplete batches and recovers cursors',async()=>{
 const {spawnSync}=require('node:child_process'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'collector-bridge-'));
 const db=path.join(temp,'test.sqlite3'),cwd=path.resolve(__dirname,'..');
 const core=(cmd,args=[],input)=>{const p=spawnSync(process.env.PYTHON||'python',['-m','journal_collector',cmd,'--db',db,...args],{cwd,input:input?JSON.stringify(input):undefined,encoding:'utf8',windowsHide:true});assert.equal(p.status,0,p.stderr);return JSON.parse(p.stdout);};
 try{
  const f=fixture(conversations(2),{maxConversations:5,incomplete:new Set(['conversation-2'])});
  f.options.getCursor=async(source,key)=>core('cursor',['--source',source,'--key',key]);
  f.options.ingest=async b=>core('ingest',[],b);
  assert.equal((await runScan(f.options)).status,'partial');
  const first=core('status');assert.equal(first.counts.messages,2);assert.equal(first.counts.pending_jobs,1);
  f.requests.length=0;assert.equal((await runScan(f.options)).status,'partial');assert.deepEqual(f.requests,['conversation-2']);
  assert.equal(core('status').counts.messages,2);
 }finally{fs.rmSync(temp,{recursive:true,force:true});}
});

test('incomplete items stay fair across multiple capped listing segments',async()=>{
 const f=fixture(conversations(4),{pageSize:2,maxConversations:1,incomplete:new Set(['conversation-1','conversation-3'])});
 for(let i=0;i<7;i++)await runScan(f.options);
 assert.ok(f.requests.includes('conversation-2'));
 assert.ok(f.requests.includes('conversation-4'));
});

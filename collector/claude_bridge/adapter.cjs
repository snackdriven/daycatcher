'use strict';
const crypto=require('node:crypto');
const sha=value=>crypto.createHash('sha256').update(String(value)).digest('hex');
const identifier=value=>typeof value==='string'&&/^[a-z0-9_-]{1,200}$/i.test(value);
function normalizeConversation(conv,body,observedAt){
 if(!identifier(conv.uuid)||!body||body.uuid!==conv.uuid||!Array.isArray(body.chat_messages))throw Error('BODY_SCHEMA_INVALID');
 const messages=[];
 for(const m of body.chat_messages){
  if(!m||!identifier(m.uuid))throw Error('MESSAGE_ID_INVALID');
  const role=m.sender==='human'?'user':m.sender==='assistant'?'assistant':null;
  if(!role)continue;
  if(m.content!==undefined&&!Array.isArray(m.content))throw Error('CONTENT_SCHEMA_INVALID');
  if(m.text!==undefined&&typeof m.text!=='string')throw Error('TEXT_SCHEMA_INVALID');
  let complete=!(body.truncated||body.is_partial||m.truncated||m.is_partial||m.content_truncated);
  const blocks=m.content||[];
  const textBlocks=blocks.filter(b=>b&&b.type==='text'&&typeof b.text==='string').map(b=>b.text);
  let text=typeof m.text==='string'&&m.text.length?m.text:textBlocks.join('\n');
  const provenance={adapter:'claude-chat-ambient-v1',parent_native_id:m.parent_message_uuid||null,source_updated_at:m.updated_at||null,attachments:[]};
  for(const field of ['attachments','files','files_v2']){
   if(m[field]!==undefined&&!Array.isArray(m[field]))throw Error('ATTACHMENT_SCHEMA_INVALID');
   for(const a of m[field]||[]){
    const mime=a?.file_type||a?.mime_type||a?.content_type;
    // Only plain text already embedded in the fetched conversation is accepted.
    const plain=typeof a?.extracted_content==='string'&&(mime==='text/plain'||mime==='txt'||(!mime&&/\.txt$/i.test(a?.file_name||a?.filename||'')));
    if(plain){text+=(text?'\n\n':'')+a.extracted_content;}
    else complete=false;
    provenance.attachments.push({field,embedded_plain_text:plain,content_sha256:plain?sha(a.extracted_content):null});
   }
  }
  if(blocks.some(b=>!b||b.type!=='text'||typeof b.text!=='string'))complete=false;
  if(!text.length)complete=false;
  messages.push({native_id:m.uuid,conversation_id:conv.uuid,role,text,source_time:m.created_at||null,observed_at:observedAt,complete,provenance});
 }
 return {messages,complete:messages.length>0&&messages.every(m=>m.complete)};
}
function batchId(batch){
 // Hash the exact payload: child-process acknowledgement retries reuse this batch.
 // Later observations get their own IDs and Store deduplicates message revisions.
 return 'claude-'+sha(JSON.stringify(batch));
}
function makeBatch(values){const batch={...values};batch.batch_id=batchId(batch);return batch;}
async function runScan(o){
 const source='claude-chat:'+sha(o.orgId).slice(0,20);
 const observedAt=o.observedAt||new Date().toISOString();
 const stats={source,status:'partial',pages:0,rows:0,body_requests:0,unchanged:0,ingested:0,list_reached_end:false};
 try{
  const scan=await o.getCursor(source,'scan')||{};
  const startOffset=Number.isSafeInteger(scan.next_list_offset)&&scan.next_list_offset>=0?scan.next_list_offset:0;
  let offset=startOffset,lastSignature=scan.last_page_signature,lastOffset=scan.last_page_offset;
  const discovered=new Map(),seenPages=new Set();
  for(let page=0;page<o.maxPages;page++){
   const rows=await o.read('https://claude.ai/api/organizations/'+o.orgId+'/chat_conversations?limit='+o.pageSize+'&offset='+offset+'&consistency=eventual');
   if(!Array.isArray(rows))throw Error('LIST_SCHEMA_INVALID');stats.pages++;stats.rows+=rows.length;
   for(const c of rows)if(!c||!identifier(c.uuid)||typeof c.updated_at!=='string'||!Number.isFinite(Date.parse(c.updated_at)))throw Error('LIST_ROW_INVALID');
   const signature=sha(rows.map(c=>c.uuid).join('\n'));
   // Some servers silently ignore offset. Never acknowledge false coverage.
   if(rows.length&&(seenPages.has(signature)||(offset>0&&lastOffset!==offset&&lastSignature===signature)))throw Error('PAGINATION_STALLED');
   seenPages.add(signature);lastSignature=signature;lastOffset=offset;
   for(const c of rows){
    if(o.since&&Date.parse(c.updated_at)<Date.parse(o.since))continue;
    if(o.titleAllowlist&&!o.titleAllowlist.includes(c.name))continue;
    discovered.set(c.uuid,c);
   }
   offset+=rows.length;
   if(rows.length<o.pageSize){stats.list_reached_end=true;break;}
  }
  const selected=[...discovered.values()];
  // Rotate before spending body requests, including when an earlier item stays incomplete.
  const rotations={...(scan.body_rotations||{})};
  const segmentKey=String(startOffset);
  const segmentAfter=rotations[segmentKey]||scan.afterConversationHash;
  const previousIndex=selected.findIndex(c=>sha(c.uuid)===segmentAfter);
  if(previousIndex>=0)selected.push(...selected.splice(0,previousIndex+1));
  let incomplete=false,afterConversationHash=scan.afterConversationHash||null;
  for(const conv of selected){
   const key='conversation:'+sha(conv.uuid);
   const previous=await o.getCursor(source,key);
   if(previous?.updated_at===conv.updated_at){stats.unchanged++;continue;}
   if(stats.body_requests>=o.maxConversations){incomplete=true;continue;}
   stats.body_requests++;
   const body=await o.read('https://claude.ai/api/organizations/'+o.orgId+'/chat_conversations/'+conv.uuid+'?tree=True&rendering_mode=messages&render_all_tools=true&consistency=eventual');
   const normalized=normalizeConversation(conv,body,observedAt);incomplete ||= !normalized.complete;
   const batch=makeBatch({source,cursor_key:key,cursor:normalized.complete?{updated_at:conv.updated_at}:(previous||{}),coverage:{status:normalized.complete?'complete':'partial',detail:'one fetched conversation tree; unsupported content remains retryable'},messages:normalized.messages});
   await o.ingest(batch);stats.ingested++;afterConversationHash=sha(conv.uuid);
  }
  // A segment ending at the last page is not an account-wide complete scan.
  stats.status=startOffset===0&&stats.list_reached_end&&!incomplete?'complete':'partial';
  if(afterConversationHash)rotations[segmentKey]=afterConversationHash;
  const cursor={body_rotations:rotations,next_list_offset:stats.list_reached_end?0:offset,afterConversationHash,last_page_signature:lastSignature,last_page_offset:lastOffset};
  await o.ingest(makeBatch({source,cursor_key:'scan',cursor,coverage:{status:stats.status,detail:'bounded metadata listing; horizon and optional title scope apply; pages='+stats.pages+'; reached_end='+stats.list_reached_end},messages:[]}));
 }catch(error){
  stats.status=/^HTTP_(401|403)$/.test(error.message)?'authentication_required':'error';
  stats.error=/^[A-Z_]+(?:_\d+)?$/.test(error.message)?error.message:'REQUEST_OR_STORE_FAILED';
  await o.ingest(makeBatch({source,cursor_key:'scan',cursor:null,coverage:{status:stats.status,detail:stats.error},messages:[]}));
 }
 return stats;
}
module.exports={normalizeConversation,runScan,sha,makeBatch};

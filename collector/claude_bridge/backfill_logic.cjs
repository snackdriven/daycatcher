'use strict';
const path=require('node:path');
const {sha,normalizeConversation,makeBatch}=require('./adapter.cjs');
const id=x=>typeof x==='string'&&/^[a-z0-9_-]{1,200}$/i.test(x);
async function boundedJson(response,budget){
 const reader=response.body?.getReader();if(!reader)throw Error('RESPONSE_STREAM_REQUIRED');
 const chunks=[];
 try{for(;;){const {value,done}=await reader.read();if(done)break;budget.remaining-=value.byteLength;if(budget.remaining<0)throw Error('PASS_BYTE_LIMIT');chunks.push(Buffer.from(value));}
 return JSON.parse(Buffer.concat(chunks).toString('utf8'));
 }finally{await reader.cancel().catch(()=>{});}
}
function timestamp(value){
 // Date.parse alone accepts local-time values and rolls impossible dates such
 // as February 30 forward. Source timestamps must identify a real instant.
 if(typeof value!=='string')return false;
 const parts=value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))$/);
 if(!parts)return false;
 const [year,month,day,hour,minute,second]=parts.slice(1,7).map(Number);
 const leap=year%4===0&&(year%100!==0||year%400===0);
 const days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
 if(year<1||month<1||month>12||day<1||day>days[month-1]||hour>23||minute>59||second>59)return false;
 if(parts[7]!=='Z'&&(Number(parts[8])>23||Number(parts[9])>59))return false;
 return Number.isFinite(Date.parse(value));
}
function validate(c){
 for(const key of ['data_dir','db','package_root','profile_dir','python','run_dir'])if(typeof c[key]!=='string'||!path.isAbsolute(c[key]))throw Error('ABSOLUTE_PATH_REQUIRED');
 if(!/^claude-chat:[a-f0-9]{20}$/.test(c.expected_source||''))throw Error('EXPECTED_SOURCE_REQUIRED');
 if(!['inventory','collect'].includes(c.mode))throw Error('MODE_INVALID');
 for(const [key,fallback,max] of [['max_pages',10,10],['page_size',30,30],['max_conversations',20,20]]){c[key]??=fallback;if(!Number.isInteger(c[key])||c[key]<1||c[key]>max)throw Error('LIMIT_INVALID');}
 c.start_offset??=0;if(!Number.isSafeInteger(c.start_offset)||c.start_offset<0||c.start_offset>10000000)throw Error('OFFSET_INVALID');
 if(c.exclude_ids!==undefined&&(!Array.isArray(c.exclude_ids)||c.exclude_ids.some(x=>!id(x))))throw Error('EXCLUSIONS_INVALID');
 if(c.mode==='collect'&&(!timestamp(c.since)||!timestamp(c.until)||Date.parse(c.since)>=Date.parse(c.until)))throw Error('WINDOW_INVALID');
 return c;
}
function checkSource(orgId,expected){if('claude-chat:'+sha(orgId).slice(0,20)!==expected)throw Error('SOURCE_SCOPE_MISMATCH');}
function probe(row,excluded=[]){return excluded.includes(row.uuid)||/^MEAT_SUIT(?:_|\b)/.test(row.name||'')||/^Synthetic collection probe(?:\b|$)/i.test(row.name||'');}
async function inventory(o){
 checkSource(o.orgId,o.expected_source);
 const start=o.start_offset??0;if(!Number.isSafeInteger(start)||start<0||start>10000000)throw Error('OFFSET_INVALID');
 let offset=start;const seen=new Set(),rows=new Map();let reachedEnd=false,pages=0;
 for(;pages<o.max_pages;){
  o.checkStop?.();const size=o.page_size;
  const list=await o.read('https://claude.ai/api/organizations/'+o.orgId+'/chat_conversations?limit='+size+'&offset='+offset+'&consistency=eventual');
  if(!Array.isArray(list)||list.length>size)throw Error('LIST_SCHEMA_INVALID');
  for(const row of list)if(!row||!id(row.uuid)||!timestamp(row.updated_at))throw Error('LIST_ROW_INVALID');
  const signature=sha(list.map(x=>x.uuid).join('\n'));
  if(list.length&&seen.has(signature))throw Error('PAGINATION_STALLED');seen.add(signature);pages++;
  for(const row of list){if(rows.has(row.uuid))throw Error('PAGINATION_OVERLAP');rows.set(row.uuid,{uuid:row.uuid,updated_at:row.updated_at,probe:probe(row,o.exclude_ids)});}
  offset+=list.length;if(list.length<size){reachedEnd=true;break;}
 }
 const values=[...rows.values()],dates={};for(const r of values){const date=new Date(r.updated_at).toISOString().slice(0,10);dates[date]=(dates[date]||0)+1;}
 return {rows:values,summary:{status:'inventory_complete',pages,rows:values.length,probes:values.filter(r=>r.probe).length,start_offset:start,next_offset:reachedEnd?null:offset,list_reached_end:reachedEnd,account_complete:false,dates}};
}
function selectRows(selection,inv,o){
 if(!Array.isArray(selection)||selection.length>o.max_conversations)throw Error('SELECTION_INVALID');
 if(!Array.isArray(inv))throw Error('INVENTORY_REQUIRED');
 const seen=new Set();return selection.map(row=>{
  if(!row||!id(row.uuid)||!timestamp(row.updated_at)||Object.keys(row).some(k=>!['uuid','updated_at'].includes(k))||seen.has(row.uuid))throw Error('SELECTION_INVALID');
  seen.add(row.uuid);const match=inv.find(x=>x.uuid===row.uuid&&x.updated_at===row.updated_at);
  if(!match||match.probe!==false||(o.exclude_ids||[]).includes(row.uuid))throw Error('SELECTION_NOT_ALLOWED');return row;
 });
}
async function collect(o){
 checkSource(o.orgId,o.expected_source);
 const selected=selectRows(o.selection,o.inventory,o),stats={status:'partial',selected:selected.length,body_requests:0,conversations_ingested:0,new:0,revised:0,unchanged:0,incomplete:0,excluded:0,preserved:0,conflicting:0,filtered_outside_window:0,invalid_source_time:0,normalized_messages:0,incomplete_conversations:0};
 try{for(const conv of selected){
  o.checkStop?.();stats.body_requests++;
  const body=await o.read('https://claude.ai/api/organizations/'+o.orgId+'/chat_conversations/'+conv.uuid+'?tree=True&rendering_mode=messages&render_all_tools=true&consistency=eventual');
  const normalized=normalizeConversation(conv,body,new Date().toISOString());stats.normalized_messages+=normalized.messages.length;
  const messages=normalized.messages.filter(m=>{if(!timestamp(m.source_time)){stats.invalid_source_time++;return false;}const t=Date.parse(m.source_time);if(t<Date.parse(o.since)||t>=Date.parse(o.until)){stats.filtered_outside_window++;return false;}return true;});
  if(!normalized.complete)stats.incomplete_conversations++;
  const result=await o.ingest(makeBatch({source:o.expected_source,cursor_key:'backfill:'+o.run_id+':'+sha(conv.uuid),cursor:{since:o.since,until:o.until,updated_at:conv.updated_at},coverage:{status:'partial',detail:'bounded backfill window only; original timestamps filtered; full account and conversation coverage not asserted'},messages}));
  for(const key of ['new','revised','unchanged','incomplete','excluded','preserved','conflicting'])stats[key]+=Number.isInteger(result?.[key])?result[key]:0;
  stats.conversations_ingested++;
 }}catch(error){error.backfill_stats={...stats};throw error;}
 return stats;
}
module.exports={timestamp,validate,checkSource,probe,inventory,selectRows,collect,boundedJson};

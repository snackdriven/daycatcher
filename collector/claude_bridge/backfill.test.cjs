'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {timestamp,validate,checkSource,probe,inventory,selectRows,collect}=require('./backfill_logic.cjs');
const {sha}=require('./adapter.cjs');
const org='00000000-1111-2222-3333-444444444444',source='claude-chat:'+sha(org).slice(0,20);
const date='2026-10-25T12:00:00Z';
const config=()=>({data_dir:path.resolve('fixture-data'),db:path.resolve('fixture-db'),package_root:path.resolve('fixture-package'),profile_dir:path.resolve('fixture-profile'),python:path.resolve('fixture-python'),run_dir:path.resolve('fixture-run'),mode:'inventory',expected_source:source});
const options=()=>({...validate(config()),orgId:org});
const row=(uuid='conversation1',extra={})=>({uuid,updated_at:date,...extra});
test('timestamps require timezone and calendar-valid dates and clock values',()=>{
 for(const value of ['2026-10-25T12:00:00Z','2026-10-25T12:00:00.123456Z','2026-10-25T12:00:00-05:00','2024-02-29T23:59:59+00:00','2000-02-29T00:00:00Z'])assert.equal(timestamp(value),true,value);
 for(const value of ['2026-10-25T12:00:00','2026-02-30T12:00:00Z','2026-02-29T12:00:00Z','1900-02-29T12:00:00Z','2026-04-31T00:00:00Z','2026-13-01T00:00:00Z','2026-00-01T00:00:00Z','2026-01-00T00:00:00Z','2026-10-25T24:00:00Z','2026-10-25T12:60:00Z','2026-10-25T12:00:60Z','2026-10-25T12:00:00+24:00','2026-10-25T12:00:00+01:60','0000-01-01T00:00:00Z',null,3])assert.equal(timestamp(value),false,String(value));
});
test('validates strict small bounds, absolute paths, source, and collect window',()=>{
 assert.equal(validate(config()).max_conversations,20);
 for(const override of [{max_pages:11},{max_conversations:21},{page_size:31},{max_pages:0},{python:'python'},{expected_source:'wrong'},{exclude_ids:['bad/id']},{mode:'login'},{mode:'collect',since:date,until:date}])assert.throws(()=>validate({...config(),...override}));
 assert.equal(validate({...config(),mode:'collect',since:'2026-10-01T00:00:00Z',until:date}).mode,'collect');
});
test('source scope mismatch is checked before any metadata or body request',async()=>{
 let requests=0;const o={...options(),expected_source:'claude-chat:'+'f'.repeat(20),read:()=>requests++};
 assert.throws(()=>checkSource(org,o.expected_source),/SOURCE_SCOPE_MISMATCH/);
 await assert.rejects(inventory(o),/SOURCE_SCOPE_MISMATCH/);await assert.rejects(collect(o),/SOURCE_SCOPE_MISMATCH/);assert.equal(requests,0);
});
test('probe detection is strict and supports known local exclusions',()=>{
 assert.equal(probe(row('a',{name:'MEAT_SUIT_SYNTHETIC_123'})),true);
 assert.equal(probe(row('a',{name:'Synthetic collection probe 123'})),true);
 assert.equal(probe(row('a',{name:'A real discussion of synthetic collection probes'})),false);
 assert.equal(probe(row('a',{name:'MEAT_SUITLESS'})),false);
 assert.equal(probe(row('known'),['known']),true);
});
test('inventory contains only metadata and caps requests at 90 rows',async()=>{
 const urls=[];const result=await inventory({...options(),read:async url=>{urls.push(url);const u=new URL(url),offset=Number(u.searchParams.get('offset')),n=Number(u.searchParams.get('limit'));return Array.from({length:n},(_,i)=>row('conversation'+(offset+i),{name:'Private title never retained',body:'private body never retained'}));}});
 assert.equal(urls.length,3);assert.equal(result.rows.length,90);assert.equal(result.summary.list_reached_end,false);
 assert.deepEqual(Object.keys(result.rows[0]),['uuid','updated_at','probe']);assert.equal(JSON.stringify(result).includes('Private'),false);
 assert.equal(JSON.stringify(result.summary).includes('conversation'),false);assert.equal(result.summary.dates['2026-10-25'],90);
});
test('inventory respects requested page cap and records actual end',async()=>{
 const r=await inventory({...options(),max_pages:1,read:async()=>[row()]});assert.equal(r.summary.pages,1);assert.equal(r.summary.list_reached_end,true);
});
test('inventory refuses repeated pages, overlap, invalid rows, oversized payload',async()=>{
 const o={...options(),page_size:1,read:async()=>[row()]};await assert.rejects(inventory(o),/PAGINATION_STALLED/);
 let calls=0;await assert.rejects(inventory({...options(),page_size:2,read:async()=>++calls===1?[row('a'),row('b')]:[row('b'),row('c')]}),/PAGINATION_OVERLAP/);
 await assert.rejects(inventory({...options(),read:async()=>[row('a',{updated_at:'invalid'})]}),/LIST_ROW_INVALID/);
 await assert.rejects(inventory({...options(),page_size:1,read:async()=>[row('a'),row('b')]}),/LIST_SCHEMA_INVALID/);
});
test('selection requires exact nonprobe inventory metadata, unique rows and capped whitelist',()=>{
 const inv=[{...row(),probe:false},{...row('probe'),probe:true}],o=options();assert.equal(selectRows([row()],inv,o).length,1);
 for(const selected of [[row(),row()],[row('unknown')],[row('probe')],[row('conversation1',{updated_at:'2026-10-26T00:00:00Z'})],[row('conversation1',{name:'extra'})]])assert.throws(()=>selectRows(selected,inv,o));
 assert.throws(()=>selectRows([row()],inv,{...o,exclude_ids:['conversation1']}),/SELECTION_NOT_ALLOWED/);
 assert.throws(()=>selectRows([row()],inv,{...o,max_conversations:0}),/SELECTION_INVALID/);
});
const message=(uuid,created_at,extra={})=>({uuid,sender:'human',text:'fixture text',created_at,...extra});
function collectOptions(messages){return {...options(),mode:'collect',since:'2026-10-20T00:00:00Z',until:'2026-10-27T00:00:00Z',selection:[row()],inventory:[{...row(),probe:false}],run_id:'syntheticrun',read:async()=>({uuid:'conversation1',chat_messages:messages})};}
test('collect filters original timestamps with inclusive since/exclusive until before ingestion',async()=>{
 const batches=[];const result=await collect({...collectOptions([message('old','2020-01-01T00:00:00Z'),message('start','2026-10-20T00:00:00Z'),message('inside','2026-10-26T23:59:59Z'),message('end','2026-10-27T00:00:00Z'),message('missing',undefined),message('invalid','bad')]),ingest:async batch=>{batches.push(batch);return {new:2,revised:0,unchanged:0,incomplete:0,excluded:0};}});
 assert.deepEqual(batches[0].messages.map(x=>x.native_id),['start','inside']);assert.equal(result.filtered_outside_window,2);assert.equal(result.invalid_source_time,2);assert.equal(result.new,2);
 assert.equal(batches[0].source,source);assert.equal(batches[0].messages[0].conversation_id,'conversation1');assert.equal(batches[0].messages[0].source_time,'2026-10-20T00:00:00Z');
 assert.equal(batches[0].cursor_key,'backfill:syntheticrun:'+sha('conversation1'));assert.equal(batches[0].coverage.status,'partial');assert.equal(result.status,'partial');
 assert.equal(JSON.stringify(result).includes('fixture text'),false);assert.equal(JSON.stringify(result).includes('conversation1'),false);
});
test('unsupported blocks and truncation remain incomplete through window filtering',async()=>{
 let stored;const o=collectOptions([message('incomplete',date,{content:[{type:'tool_use',id:'tool'}]}),message('truncated',date,{truncated:true})]);
 const stats=await collect({...o,ingest:async batch=>{stored=batch;return {new:0,revised:0,unchanged:0,incomplete:2,excluded:0};}});
 assert.equal(stats.incomplete,2);assert.equal(stats.incomplete_conversations,1);assert.equal(stored.messages.every(x=>x.complete===false),true);assert.equal(stored.coverage.status,'partial');
});
test('malformed rollover and timezone-less source times are skipped before ingestion',async()=>{
 let stored;const o={...collectOptions([message('rollover','2026-02-30T12:00:00Z'),message('ambiguous','2026-10-25T12:00:00'),message('real',date)]),since:'2026-02-01T00:00:00Z'};
 const stats=await collect({...o,ingest:async batch=>{stored=batch;return {new:1};}});
 assert.equal(stats.invalid_source_time,2);assert.deepEqual(stored.messages.map(x=>x.native_id),['real']);assert.equal(stats.new,1);
});
test('empty filtered tree still acknowledges only bounded partial backfill cursor',async()=>{
 const batches=[];await collect({...collectOptions([message('old','2000-01-01T00:00:00Z')]),ingest:async b=>{batches.push(b);return {};}});
 assert.equal(batches.length,1);assert.equal(batches[0].messages.length,0);assert.match(batches[0].cursor_key,/^backfill:/);assert.equal(batches[0].coverage.status,'partial');
});
test('stop hook prevents both metadata and body requests',async()=>{
 let n=0;const checkStop=()=>{throw Error('STOPPED');};await assert.rejects(inventory({...options(),checkStop,read:async()=>{n++;}}),/STOPPED/);
 await assert.rejects(collect({...collectOptions([]),checkStop,read:async()=>{n++;}}),/STOPPED/);assert.equal(n,0);
});
test('authentication, schema and core failures propagate without fabricated success',async()=>{
 await assert.rejects(inventory({...options(),read:async()=>{throw Error('HTTP_401');}}),/HTTP_401/);
 await assert.rejects(collect({...collectOptions([]),read:async()=>({uuid:'wrong',chat_messages:[]})}),/BODY_SCHEMA_INVALID/);
 await assert.rejects(collect({...collectOptions([message('a',date)]),ingest:async()=>{throw Error('CORE_TIMEOUT');}}),/CORE_TIMEOUT/);
});

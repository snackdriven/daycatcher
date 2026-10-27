"""Bounded native ChatGPT user-message normalization; no network or model calls."""
import hashlib
import json
import math
import re
from datetime import datetime, timezone

SOURCE = 'chatgpt-native'
MAX_BYTES = 2 * 1024 * 1024
MAX_TURNS = 1000

def _iso(value):
    if not isinstance(value, str) or len(value)>100 or 'T' not in value:
        raise ValueError('invalid_timestamp')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError('invalid_timestamp')
    return parsed.astimezone(timezone.utc)

def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}',value):
        raise ValueError('invalid_identifier')
    return value

def _truncated(value):
    if isinstance(value,dict):
        return any(('truncat' in str(k).lower() and v not in (False,None,0,'')) or _truncated(v)
                   for k,v in value.items())
    if isinstance(value,list): return any(_truncated(v) for v in value)
    return False

def _unsupported_metadata(value):
    if isinstance(value,dict):
        return any((any(word in str(k).lower().replace('_','') for word in
                       ('attachment','contextref','contentref')) and bool(v)) or
                   (_unsupported_metadata(v) if k not in ('text','content') else False)
                   for k,v in value.items())
    if isinstance(value,list): return any(_unsupported_metadata(v) for v in value)
    return False

def _generated(value):
    """Inspect attribution metadata only; never interpret message prose as flags."""
    if not isinstance(value,dict): return False
    human={'human','user','native-user-role'}
    for key,val in value.items():
        name=re.sub(r'[^a-z]','',str(key).lower())
        if any(word in name for word in ('generated','automation','scheduler','system','relay')) or name in ('ismeta','synthetic'):
            if val not in (None,False,0,''): return True
        if name in ('metadata','provenance'):
            if val is not None and (not isinstance(val,dict) or _generated(val)): return True
        elif name=='author':
            if isinstance(val,dict):
                # An explicit but unknown author must not become origin=human.
                attribution=val.get('role',val.get('origin'))
                if not isinstance(attribution,str) or attribution.lower() not in human or _generated(val):
                    return True
            elif val is not None and (not isinstance(val,str) or val.lower() not in human): return True
        elif name in ('origin','role'):
            if val is not None and (not isinstance(val,str) or val.lower() not in human): return True
        elif name=='source' and isinstance(val,str) and val.lower() in (
                'system','automation','scheduler','generated','assistant','agent','relay'):
            return True
    return False

def normalize_page(payload, *, expected_thread_id, since, until, now):
    """Produce a Store batch from one decoded schemaVersion 1 read_thread page.

    Only complete plaintext user items within [since, until) are included. Unsupported
    observations are counted and never substituted for previously stored bodies.
    """
    expected_thread_id=_id(expected_thread_id)
    start,end,observed=map(_iso,(since,until,now))
    if start>=end: raise ValueError('invalid_window')
    if not isinstance(payload,dict) or type(payload.get('schemaVersion')) is not int or payload['schemaVersion']!=1:
        raise ValueError('invalid_schema')
    thread=payload.get('thread'); page=payload.get('page'); turns=payload.get('turns')
    if not isinstance(thread,dict) or thread.get('kind')!='chatgpt' or thread.get('id')!=expected_thread_id:
        raise ValueError('thread_mismatch')
    if not isinstance(page,dict) or page.get('order')!='newest_first' or type(page.get('limit')) is not int or not 1<=page['limit']<=MAX_TURNS:
        raise ValueError('invalid_page')
    if type(page.get('hasMore')) is not bool or 'nextCursor' not in page:
        raise ValueError('invalid_page')
    cursor=page['nextCursor']
    if cursor is not None and (not isinstance(cursor,str) or not cursor or len(cursor)>4096):
        raise ValueError('invalid_cursor')
    if page['hasMore'] != (cursor is not None): raise ValueError('invalid_cursor_state')
    if not isinstance(turns,list) or len(turns)>page['limit'] or len(turns)>MAX_TURNS:
        raise ValueError('invalid_turns')
    messages=[]; incomplete=0; outside=0; assistants=0; seen=set()
    global_bad=_truncated({k:v for k,v in payload.items() if k!='turns'}) or _unsupported_metadata({k:v for k,v in payload.items() if k!='turns'}) or _generated({k:v for k,v in payload.items() if k not in ('turns','thread')}) or _generated(thread)
    for turn in turns:
        if not isinstance(turn,dict) or not isinstance(turn.get('items'),list) or len(turn['items'])>1000:
            raise ValueError('invalid_turn')
        assistants+=sum(isinstance(i,dict) and i.get('type')=='agentMessage' for i in turn['items'])
        for item in turn['items']:
            if not isinstance(item,dict): incomplete+=1; continue
            if item.get('type')=='agentMessage': continue
            if item.get('type')!='userMessage': incomplete+=1; continue
            try:
                if global_bad or _truncated(turn) or _generated(turn) or _generated(item) or _unsupported_metadata(item) or _unsupported_metadata({k:v for k,v in turn.items() if k!='items'}): raise ValueError('incomplete')
                native=_id(item.get('id'))
                if native!=_id(turn.get('id')): raise ValueError('ambiguous_source_time')
                if item.get('attachments') or item.get('contextReferences') or item.get('contextRefs'): raise ValueError('unsupported')
                content=item.get('content')
                if not isinstance(content,list) or not content or len(content)>1000: raise ValueError('unsupported')
                if any(not isinstance(c,dict) or set(c)-{'type','text','truncated','isTruncated'} or c.get('type')!='text' or not isinstance(c.get('text'),str) for c in content):
                    raise ValueError('unsupported')
                text=''.join(c['text'] for c in content)
                if not text or '::chatgpt-content-reference' in text or len(text.encode('utf-8'))>MAX_BYTES: raise ValueError('unsupported')
                stamp=turn.get('startedAt')
                if type(stamp) not in (int,float) or not math.isfinite(stamp): raise ValueError('invalid_source_time')
                source_time=datetime.fromtimestamp(stamp,timezone.utc)
                if not start<=source_time<end: outside+=1; continue
                if native in seen: incomplete+=1; continue
                seen.add(native)
                provenance={'adapter':'chatgpt-native-v1','origin':'human','timestamp_valid':True,'user_only':True}
                if 'MEAT_SUIT' in text: provenance.update(known_probe=True,synthetic=True)
                messages.append({'native_id':native,'conversation_id':expected_thread_id,'role':'user',
                                 'text':text,'source_time':source_time.isoformat(),
                                 'observed_at':observed.isoformat(),'complete':True,'provenance':provenance})
            except (ValueError,OverflowError,OSError): incomplete+=1
    coverage={'status':'partial','detail':'user-only bounded native page; account list completeness unverified',
              'assistant_coverage':False,'assistant_unavailable':assistants,'incomplete':incomplete,
              'outside_window':outside,'accepted':len(messages),'normalized':len(messages),'skipped':incomplete+outside}
    if global_bad and not turns: coverage['incomplete']=1
    batch={'source':SOURCE,'cursor_key':'thread:'+hashlib.sha256(expected_thread_id.encode()).hexdigest(),
           'cursor':{'next_cursor':cursor,'has_more':page['hasMore'],'since':start.isoformat(),'until':end.isoformat()},
           'coverage':coverage,'messages':messages}
    batch['batch_id']='chatgpt-native:'+hashlib.sha256(json.dumps(batch,sort_keys=True,ensure_ascii=True,allow_nan=False).encode()).hexdigest()
    return batch

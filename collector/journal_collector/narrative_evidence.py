"""Private, read-only verified-human evidence. No model calls or publication.

Text stays in the caller's private runtime. Source roles alone cannot prove that
pasted text is human-authored; known ambiguous forms abstain conservatively.
"""
from collections import defaultdict, deque
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo


_ACCOUNT = re.compile(r'claude-chat:[0-9a-f]{20}\Z')
_HARNESS = re.compile(
    r'<(?:environment_context|system-reminder|task-notification|external_codex_apps|skills_instructions|permissions)\b|'
    r'\bMessage Type\s*:|\bNEW_TASK\b|\bPayload\s*:|\b(?:HOST|GUEST)\s*:|'
    r'This is an automated background-task event|\[Request interrupted|'
    r'^\s*(?:assistant|agent|system|tool|user)\s*:|^\s*>|'
    r'\b(?:here is|pasted|forwarded) (?:the |a )?(?:transcript|assistant response|conversation|tool output)|'
    r'::(?:chatgpt-content-reference|code-comment)|```|~~~|`|'
    r'^\s*(?:PS [^>]*>|\$ |>>> |(?:def|class|import|from|function|const|let|var)\s)|'
    r'^\s*[\[{]\s*["\[{]|^\s*</?\w+[^>]*>', re.I | re.M)
_RECEIPT = re.compile(r'^\s*(?:Created|Published|Committed|Pushed|Updated|Wrote|Saved)\b'
                      r'[\s\S]*?\bCommit:\s*[0-9a-f]{40,64}\b',re.I)
_MODEL_REFERENCE = re.compile(r'[\ue200-\ue20f]|\bcontentReference\[|\b(?:oaicite|oai_citation):',re.I)
# These recognizable credential forms abstain before any writer input exists.
# This is a bounded safeguard, not a general secret-detection guarantee.
_CREDENTIAL = re.compile(
    r'(?<![A-Za-z0-9])(?:sk-(?:proj-|svcacct-)?|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|'
    r'\bBearer\s+[A-Za-z0-9._~+/-]{8,}={0,2}|'
    r'\b(?:password|passwd|passphrase|secret|(?:access[_ -]?|auth[_ -]?|refresh[_ -]?)?token|'
    r'api[_ -]?key)\b["\']?\s*(?:=|:|\bis\b)\s*["\']?[^\s"\'<>]+',re.I)
_BAD_FLAGS = frozenset({'synthetic','processinggenerated','relay','ismeta','unsupportedcontent',
                        'automation','scheduler','generated','system','issidechain',
                        'iscompactsummary','knownprobe','probe','isvisibleintranscriptonly',
                        'isapierrormessage','truncated','istruncated','ispartial'})
_HUMAN = frozenset({'human','user','native-user-role'})


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _instant(value):
    if not isinstance(value,str) or 'T' not in value:
        raise ValueError('invalid_timestamp')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('invalid_timestamp')
    return parsed.astimezone(timezone.utc)


def day_window(day):
    """Independent local bounds preserve the 23/25-hour DST days."""
    if not isinstance(day,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
        raise ValueError('invalid_day')
    start=date.fromisoformat(day)
    zone=ZoneInfo('America/Chicago')
    return (datetime.combine(start,time(3),zone).astimezone(timezone.utc),
            datetime.combine(start+timedelta(days=1),time(3),zone).astimezone(timezone.utc))


def _unsafe_metadata(value):
    if isinstance(value,list):
        return any(_unsafe_metadata(item) for item in value)
    if not isinstance(value,dict):
        return False
    for key,item in value.items():
        name=re.sub('[^a-z]','',str(key).lower())
        if (name in _BAD_FLAGS or any(token in name for token in ('generated','automation','scheduler','relay','truncat'))):
            if item not in (None,False,0,''):
                return True
        if name in {'attachments','files','filesv','contentreferences','contextreferences','contextrefs'} and item:
            return True
        if name in {'origin','author'}:
            if isinstance(item,str) and item not in _HUMAN:
                return True
            if item is not None and not isinstance(item,(str,dict)):
                return True
            if isinstance(item,dict) and item.get('role',item.get('origin')) not in _HUMAN:
                return True
        if name=='role' and item not in _HUMAN:
            return True
        if name=='source' and isinstance(item,str) and item.lower() in {
                'agent','assistant','system','tool','automation','scheduler','generated','relay'}:
            return True
        if name in {'timestampvalid','useronly'} and item is not True:
            return True
        if _unsafe_metadata(item):
            return True
    return False


def eligible_message(row):
    """Return a source kind only for a supported complete authored message."""
    try:
        if row['role']!='user' or type(row['complete']) not in (bool,int) or row['complete']!=1:
            return None
        text=row['text']
        if (not isinstance(text,str) or not text.strip() or '\x00' in text
                or _HARNESS.search(text) or _RECEIPT.search(text)
                or _MODEL_REFERENCE.search(text) or _CREDENTIAL.search(text)):
            return None
        p=json.loads(row['provenance'])
        if not isinstance(p,dict) or _unsafe_metadata(p):
            return None
        _instant(row['source_time']);_instant(row['observed_at'])
        source=row['source']
        if source=='codex-local':
            valid=(p.get('adapter')=='codex-local-jsonl-v1' and p.get('origin')=='native-user-role'
                   and p.get('timestamp_valid') is True)
        elif source=='claude-code-local':
            valid=(p.get('adapter')=='claude-code-local-jsonl-v1' and p.get('origin')=='human'
                   and p.get('timestamp_valid') is True)
        elif source=='chatgpt-native':
            valid=(p.get('adapter')=='chatgpt-native-v1' and p.get('origin')=='human'
                   and p.get('timestamp_valid') is True and p.get('user_only') is True)
        elif isinstance(source,str) and _ACCOUNT.fullmatch(source):
            # This adapter maps native sender=human to role=user. Its embedded
            # attachment bodies are deliberately excluded, even plain text.
            valid=p.get('adapter')=='claude-chat-ambient-v1'
            source='claude-chat'
        else:
            return None
        return source if valid else None
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        return None


def _revision(row):
    return _hash({key:row[key] for key in ('message_key','source','native_id','conversation_id',
                  'role','text','source_time','complete','revision','provenance')})


def _round_robin(groups):
    queues=[deque(sorted(group,key=lambda item:(_instant(item['time']),item['id'])))
            for _,group in sorted(groups.items())]
    while queues:
        for queue in queues:
            if queue:
                yield queue.popleft()
        queues=[queue for queue in queues if queue]


def export_day(source_db,day,*,profile='personal',max_items=200,max_bytes=204800,
               offset=0,expected_snapshot=None,excluded_conversations=()):
    """Export one bounded fair page from a consistent read-only SQLite snapshot.

    Pagination is bound to the first page's sourceFingerprint. Coverage always
    remains partial because collector cursors cannot establish account coverage.
    Oversize evidence is counted and never truncated. No output is written here.
    """
    if profile not in {'personal','work'}:
        raise ValueError('unsupported_profile')
    if type(max_items) is not int or not 1<=max_items<=10000:
        raise ValueError('invalid_limit')
    if type(max_bytes) is not int or not 1<=max_bytes<=16*1024*1024:
        raise ValueError('invalid_byte_limit')
    if type(offset) is not int or offset<0:
        raise ValueError('invalid_offset')
    if offset and (not isinstance(expected_snapshot,str) or not re.fullmatch('[0-9a-f]{64}',expected_snapshot)):
        raise ValueError('snapshot_required')
    if (not isinstance(excluded_conversations,(list,tuple,set,frozenset))
            or any(not isinstance(x,str) for x in excluded_conversations)):
        raise ValueError('invalid_exclusions')
    excluded_conversations=set(excluded_conversations)
    since,until=day_window(day)
    path=Path(source_db).resolve(strict=True)
    if not path.is_file():
        raise ValueError('invalid_source_db')
    groups=defaultdict(list)
    counts=defaultdict(lambda:{'eligible':0,'exported':0})
    scanned=rejected=undated=0
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        # Compare aware instants in Python; SQLite lexical comparison would
        # mishandle equivalent timestamps written with different UTC offsets.
        rows=connection.execute('''SELECT m.* FROM messages m
            WHERE NOT EXISTS (SELECT 1 FROM exclusions x WHERE x.message_key=m.message_key)
            ORDER BY m.message_key''')
        for row in rows:
            try:
                instant=_instant(row['source_time'])
            except (ValueError,TypeError,OverflowError):
                undated+=1
                continue
            if not since<=instant<until:
                continue
            scanned+=1
            kind=eligible_message(row)
            associated=connection.execute('SELECT conversation_id FROM associations WHERE message_key=?',
                                          (row['message_key'],)).fetchall()
            if (not kind or (profile=='work' and kind not in {'codex-local','claude-code-local'})
                    or row['conversation_id'] in excluded_conversations
                    or any(a[0] in excluded_conversations for a in associated)):
                rejected+=1
                continue
            item={'id':_hash(['narrative-evidence-v1',row['source'],row['native_id']]),
                  'text':row['text'],'time':row['source_time'],'observedAt':row['observed_at'],
                  'sourceKind':kind,'revision':_revision(row)}
            groups[kind].append(item)
            counts[kind]['eligible']+=1
        coverage_by_source={}
        status_rank={'complete':0,'partial':1,'unknown':2,'authentication_required':3,'error':4}
        for row in connection.execute('SELECT source,coverage,updated_at FROM cursors ORDER BY source,cursor_key'):
            source=row['source']
            kind='claude-chat' if _ACCOUNT.fullmatch(source) else source
            allowed={'codex-local','claude-code-local'} if profile=='work' else {'codex-local','claude-code-local','claude-chat','chatgpt-native'}
            if kind not in allowed:
                continue
            try:
                status=json.loads(row['coverage']).get('status')
                if status not in {'complete','partial','error','authentication_required'}:status='unknown'
                checked=_instant(row['updated_at']).isoformat()
            except (ValueError,TypeError,AttributeError):
                status='unknown';checked=None
            previous=coverage_by_source.setdefault(kind,{'sourceKind':kind,'status':status,
                                                         'checkedAt':checked,'observations':0})
            previous['observations']+=1
            if status_rank[status]>status_rank[previous['status']]:previous['status']=status
            if checked is not None and (previous['checkedAt'] is None or checked>previous['checkedAt']):
                previous['checkedAt']=checked
    finally:
        connection.close()
    ordered=list(_round_robin(groups))
    binding=_hash(['narrative-source-v1',str(path)])
    fingerprint=_hash({'sourceBinding':binding,'profile':profile,'day':day,
                       'evidence':[[x['id'],x['revision']] for x in ordered]})
    if expected_snapshot is not None and expected_snapshot!=fingerprint:
        raise ValueError('snapshot_changed')
    if offset>len(ordered):
        raise ValueError('invalid_offset')
    evidence=[];used=0;position=offset
    oversize=sum(len(_json(item).encode('utf-8'))>max_bytes for item in ordered)
    while position<len(ordered) and len(evidence)<max_items:
        item=ordered[position]
        size=len(_json(item).encode('utf-8'))
        if size>max_bytes:
            position+=1
            continue
        if used+size>max_bytes:
            break
        evidence.append(item);used+=size;position+=1
        counts[item['sourceKind']]['exported']+=1
    result={'schemaVersion':1,'profile':profile,'day':day,'timezone':'America/Chicago','cutoff':'03:00',
            'window':{'since':since.isoformat(),'until':until.isoformat()},
            'sourceBinding':binding,'sourceFingerprint':fingerprint,
            'evidence':evidence,'nextOffset':position if position<len(ordered) else None,
            'coverage':{'status':'partial','scanned':scanned,'eligible':len(ordered),
                        'exported':len(evidence),'rejected':rejected,'undatedRejected':undated,
                        'omitted':len(ordered)-len(evidence),
                        'remaining':len(ordered)-position,'oversize':oversize,
                        'budgetExhausted':position<len(ordered) or oversize>0,
                        'sourceCounts':[{'sourceKind':kind,**value} for kind,value in sorted(counts.items())],
                        'sourceCoverage':[coverage_by_source[kind] for kind in sorted(coverage_by_source)]}}
    result['exportId']=_hash(result)
    return result

"""Source-side reviewed work descriptions and a pinned private handoff.

No inference runs here. The trusted Mac worker must perform genuine on-device
semantic privacy AND grounding review before approving exact candidate digests.
Pattern checks are defense in depth, not proof that prose is deidentified or
entailed. Receivers trust that producer contract; JSON cannot attest execution.
Raw evidence, review records and local source IDs must remain on the Mac.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata

from .narrative_evidence import day_window
from .summary_publish import Publisher, PublishError, REPOSITORY, BRANCH
from .work_language import is_controlled_description


MAX_BYTES=64*1024
MAX_MOMENTS=32
KINDS=frozenset({'intent','reported_action','explicit_reaction','discussion','uncertain'})
_HEX=re.compile(r'(?:[0-9a-f]{32}|[0-9a-f]{64})\Z')
_REVISION=re.compile(r'[0-9a-f]{64}\Z')
_IDENTIFYING=re.compile(
    r'\b[a-z][a-z0-9+.-]*://|\bwww\.|\b[^\s@]+@[^\s@]+\.[^\s@]+|'
    r'\b[A-Za-z]:[\\/]|[\\/](?:Users|home|tmp|var|private|etc)[\\/]|~[\\/]|'
    r'\b[A-Z][A-Z0-9]{1,15}-[0-9]+\b|#[0-9]+\b|[0-9]{5,}|'
    r'\b[0-9a-fA-F]{16,}\b|[{}\[\]<>`\\]|=>|==|:=|::|\$\(|'
    r'\b(?:def |function |const |SELECT .+ FROM |INSERT INTO )',re.I)
_CANDIDATE_KEYS={'text','kind','evidence_ids'}
_MOMENT_KEYS={'id','time','kind','text','sanitized'}
_HEADER={'version':1,'timezone':'America/Chicago','cutoff':'03:00',
         'source':'work','processing':'on-device','coverage':'partial'}


class WorkMomentError(ValueError):
    """Content-free diagnostic; never interpolate source or model output."""
    def __init__(self,code='invalid_work_handoff'):
        self.code=code
        super().__init__(code)


def _canonical(value):
    try:
        return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError,RecursionError):
        raise WorkMomentError() from None


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _instant(value):
    try:
        if type(value) is not str or 'T' not in value:raise ValueError()
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (TypeError,ValueError,OverflowError):
        raise WorkMomentError('invalid_work_time') from None


def _window(day):
    try:return day_window(day)
    except (TypeError,ValueError,OverflowError):raise WorkMomentError('invalid_work_day') from None


def _text(value):
    if (type(value) is not str or not 1<=len(value)<=600 or value!=value.strip()
            or any(unicodedata.category(char).startswith('C') or char in '\u2028\u2029' for char in value)
            or _IDENTIFYING.search(value)):
        raise WorkMomentError('work_privacy_review_required')
    return value


def _evidence(day,export):
    since,until=_window(day)
    if (type(export) is not dict or type(export.get('schemaVersion')) is not int
            or export['schemaVersion']!=1 or export.get('profile')!='work' or export.get('day')!=day
            or export.get('timezone')!='America/Chicago' or export.get('cutoff')!='03:00'
            or type(export.get('window')) is not dict or set(export['window'])!={'since','until'}
            or _instant(export['window']['since'])!=since or _instant(export['window']['until'])!=until
            or type(export.get('evidence')) is not list or len(export['evidence'])>10000):
        raise WorkMomentError('invalid_work_evidence')
    known={}
    for item in export['evidence']:
        if (type(item) is not dict or type(item.get('id')) is not str or not _REVISION.fullmatch(item['id'])
                or item['id'] in known or type(item.get('revision')) is not str
                or not _REVISION.fullmatch(item['revision'])
                or item.get('sourceKind') not in {'codex-local','claude-code-local'}
                or type(item.get('text')) is not str or not item['text'].strip()
                or not since<=_instant(item.get('time'))<until):
            raise WorkMomentError('invalid_work_evidence')
        _instant(item.get('observedAt'))
        known[item['id']]=item
    return known


def _candidate(day,candidate,export):
    known=_evidence(day,export)
    if (type(candidate) is not dict or set(candidate)!=_CANDIDATE_KEYS
            or type(candidate['kind']) is not str or candidate['kind'] not in KINDS
            or type(candidate['evidence_ids']) is not list or not 1<=len(candidate['evidence_ids'])<=100
            or any(type(key) is not str or key not in known for key in candidate['evidence_ids'])
            or len(set(candidate['evidence_ids']))!=len(candidate['evidence_ids'])):
        raise WorkMomentError('invalid_work_candidate')
    _text(candidate['text'])
    if not is_controlled_description(candidate['text'],candidate['kind']):
        raise WorkMomentError('work_privacy_review_required')
    support=[known[key] for key in sorted(candidate['evidence_ids'])]
    # Stable identity follows the supporting episode, not model wording/topic.
    moment_id=_hash(['work-moment-v1',day,sorted(candidate['evidence_ids'])])
    stamp=min(support,key=lambda item:(_instant(item['time']),item['id']))['time']
    return moment_id,stamp,support


def review_digest(day,candidate,evidence_export):
    """Bind local review to exact text/kind/support and current source revisions.

    Callers may calculate this before local model review, but approval must only
    be recorded after that review. A digest by itself does not imply approval.
    """
    _,_,support=_candidate(day,candidate,evidence_export)
    normalized=dict(candidate,evidence_ids=sorted(candidate['evidence_ids']))
    return _hash(['work-review-v1',day,normalized,support])


def build_handoff(day,candidates,evidence_export,local_review):
    """Build only from trusted export timestamps/IDs and exact local approvals.

    Candidates cannot supply IDs, timestamps, processing flags or extra metadata.
    The trusted worker, not the model, owns export and local_review arguments.
    """
    _evidence(day,evidence_export)
    if (type(candidates) is not list or len(candidates)>MAX_MOMENTS
            or type(local_review) is not dict
            or set(local_review)!={'processing','privacy_reviewed','grounding_reviewed','approved'}
            or local_review['processing']!='on-device' or local_review['privacy_reviewed'] is not True
            or local_review['grounding_reviewed'] is not True or type(local_review['approved']) is not list
            or any(type(key) is not str or not _REVISION.fullmatch(key) for key in local_review['approved'])
            or len(local_review['approved'])>MAX_MOMENTS):
        raise WorkMomentError('local_work_review_required')
    moments=[];seen=set();expected=set()
    for item in candidates:
        moment_id,stamp,_=_candidate(day,item,evidence_export)
        digest=review_digest(day,item,evidence_export)
        if digest not in local_review['approved'] or moment_id in seen:
            raise WorkMomentError('local_work_review_required')
        seen.add(moment_id);expected.add(digest)
        moments.append({'id':moment_id,'time':stamp,'kind':item['kind'],'text':item['text'],'sanitized':True})
    if set(local_review['approved'])!=expected or len(local_review['approved'])!=len(expected):
        raise WorkMomentError('local_work_review_required')
    result=dict(_HEADER,day=day,moments=moments)
    validate_handoff(_canonical(result),day)
    return result


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise WorkMomentError()
        result[key]=value
    return result


def _parse(content,day):
    since,until=_window(day)
    try:
        if type(content) is not bytes or len(content)>MAX_BYTES:raise WorkMomentError()
        value=json.loads(content.decode('utf-8'),object_pairs_hook=_object,
                         parse_constant=lambda _:(_ for _ in ()).throw(WorkMomentError()))
        if (type(value) is not dict or set(value)!=set(_HEADER)|{'day','moments'}
                or type(value['version']) is not int or any(value[key]!=expected for key,expected in _HEADER.items())
                or value['day']!=day or type(value['moments']) is not list or len(value['moments'])>MAX_MOMENTS):
            raise WorkMomentError()
        seen=set()
        for moment in value['moments']:
            if (type(moment) is not dict or set(moment)!=_MOMENT_KEYS
                    or type(moment['id']) is not str or not _HEX.fullmatch(moment['id'])
                    or moment['id'] in seen or type(moment['kind']) is not str or moment['kind'] not in KINDS
                    or moment['sanitized'] is not True or not since<=_instant(moment['time'])<until):
                raise WorkMomentError()
            _text(moment['text'])
            if not is_controlled_description(moment['text'],moment['kind']):raise WorkMomentError()
            seen.add(moment['id'])
        return value
    except (ValueError,TypeError,UnicodeError,KeyError,RecursionError):
        raise WorkMomentError() from None


def validate_handoff(content,day=None,*,expected_day=None):
    """Validate wire format; producer declarations are not semantic attestation."""
    if expected_day is not None:
        if day is not None and day!=expected_day:raise WorkMomentError('invalid_work_day')
        day=expected_day
    if type(content) is dict:content=_canonical(content)
    value=_parse(content,day)
    return {'version':1,'day':day,'evidence':[dict(item,day=day,source='work') for item in value['moments']]}


class WorkMomentPublisher:
    """Append immutable moments with CAS/readback; never write journal entries."""
    def __init__(self,gh_path,*,transport=None,repository=REPOSITORY,branch=BRANCH):
        self.publisher=Publisher(gh_path,transport=transport,repository=repository,branch=branch)
        self.transport=self.publisher.transport

    def publish(self,handoff):
        if type(handoff) is not dict:raise WorkMomentError()
        day=handoff.get('day');incoming=_parse(_canonical(handoff),day)
        path=f'imports/work-moments/v1/{day}.json'
        for _ in range(3):
            current=self.publisher._read(path)
            existing=_parse(current['content'],day) if current else dict(_HEADER,day=day,moments=[])
            moments={item['id']:item for item in existing['moments']}
            added=[]
            for item in incoming['moments']:
                if item['id'] in moments:
                    if moments[item['id']]!=item:raise WorkMomentError('review_required')
                else:
                    moments[item['id']]=item;added.append(item)
            if current and not added:
                return {'state':'reconciled','day':day,'moments':len(moments),'blob_sha':current['sha']}
            expected=dict(existing,moments=existing['moments']+added)
            content=_canonical(expected);_parse(content,day)
            self.publisher._verify()
            error=None
            try:
                self.transport.write(path,content,current['sha'] if current else None)
            except PublishError as failure:
                error=failure
            remote=self.publisher._read(path)
            if remote is not None and _parse(remote['content'],day)==expected:
                return {'state':'reconciled' if error else 'published','day':day,
                        'moments':len(moments),'blob_sha':remote['sha']}
            if error is not None:
                if error.code=='conflict':continue
                raise error
            raise WorkMomentError('verification')
        raise WorkMomentError('conflict')

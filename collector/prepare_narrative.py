"""Prepare one private native-writer input. No model calls or journal writes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from export_narrative_evidence import load_config
from journal_collector.narrative_evidence import export_day, eligible_message, _revision, _hash as _source_hash
from journal_collector.narrative_publish import _span, render_entry, validate_entry
from journal_collector.summary_publish import Publisher


def _canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _directory(base,*parts):
    current=base
    for part in parts:
        current=current/part
        if current.is_symlink():raise ValueError('private_output_binding')
        current.mkdir(mode=0o700,exist_ok=True)
    return current


def _write(path,value):
    if path.is_symlink():raise ValueError('private_output_binding')
    descriptor,temporary=tempfile.mkstemp(prefix='.prepare-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def _read_json(path):
    if path.is_symlink() or path.stat().st_size>1024*1024:raise ValueError('support_input')
    return json.loads(path.read_text(encoding='utf-8'))


def _support_signature(item):
    if item.get('source')=='personal':
        return (item.get('source'),item.get('day'),item.get('revision'),
                item.get('text'),item.get('time'),item.get('sourceKind'))
    return _canonical(item)


_REVIEW_KEYS={'version','type','day','prior_input_digest','evidence_id','old_signature',
              'new_signature','current_revision','paragraph_digests','source_binding','lineage',
              'decision','proposal_hash','reviewed_at'}
_PERSONAL_KEYS={'id','text','time','observedAt','sourceKind','revision','source','day'}


def _text_only_lineage(source_db,old,new):
    """Verify the original support against retained source history, read-only."""
    if (set(old)!=_PERSONAL_KEYS or set(new)!=_PERSONAL_KEYS or old['source']!='personal'
            or new['source']!='personal' or old['id']!=new['id'] or old['day']!=new['day']
            or old['time']!=new['time'] or old['sourceKind']!=new['sourceKind']
            or old['text']==new['text']):
        raise ValueError('revision_review_not_text_only')
    path=Path(source_db).resolve(strict=True)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10);db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        current=None
        for row in db.execute('SELECT * FROM messages WHERE role=?',('user',)):
            if _source_hash(['narrative-evidence-v1',row['source'],row['native_id']])==new['id']:
                current=dict(row);break
        if (current is None or eligible_message(current)!=new['sourceKind']
                or _revision(current)!=new['revision'] or current['text']!=new['text']
                or current['source_time']!=new['time'] or db.execute(
                    'SELECT 1 FROM exclusions WHERE message_key=?',(current['message_key'],)).fetchone()):
            raise ValueError('revision_review_source_changed')
        previous=None
        for history in db.execute('SELECT * FROM revisions WHERE message_key=? ORDER BY revision',
                                   (current['message_key'],)):
            candidate={**current,**dict(history)}
            if _revision(candidate)==old['revision']:
                previous=candidate;break
        if (previous is None or previous['text']!=old['text'] or previous['source_time']!=current['source_time']
                or previous['role']!=current['role']
                or json.loads(previous['provenance'])!=json.loads(current['provenance'])
                or previous['revision']>=current['revision']):
            raise ValueError('revision_review_lineage_changed')
        return _digest({'old_revision':previous['revision'],'new_revision':current['revision'],
                        'provenance':json.loads(current['provenance'])})
    finally:db.close()


def _review_proposal(config,baseline,old,new,source_binding):
    row,entry,_=baseline
    if row is None or row['state']!='delivered':raise ValueError('revision_review_receipt')
    paragraphs=sorted(_digest(p) for p in entry['paragraphs'] if old['id'] in p['evidence_ids'])
    if not paragraphs:raise ValueError('revision_review_unused')
    return {'version':1,'type':'personal-evidence-revision-review','day':entry['day'],
            'prior_input_digest':row['input_digest'],'evidence_id':old['id'],
            'old_signature':_digest(_support_signature(old)),'new_signature':_digest(_support_signature(new)),
            'current_revision':new['revision'],'paragraph_digests':paragraphs,'source_binding':source_binding,
            'lineage':_text_only_lineage(config['source_db'],old,new),
            'decision':'pending','proposal_hash':None,'reviewed_at':None}


def _proposal_form(record):
    return {**record,'decision':'pending','proposal_hash':None,'reviewed_at':None}


def _review_anchor(data,day,digest,current_baseline):
    """A prior approval may follow a verified append-only delivered ancestry."""
    if current_baseline[0]['input_digest']==digest:return current_baseline
    path=data/'narrative-writer'/'receipts.sqlite3'
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10);db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        attempt=db.execute('SELECT entry_json,state FROM narrative_attempts WHERE day=? AND input_digest=?',
                           (day,digest)).fetchone()
        support=db.execute('SELECT manifest_json FROM narrative_support WHERE day=? AND input_digest=?',
                           (day,digest)).fetchone()
    finally:db.close()
    if attempt is None or attempt['state']!='delivered' or support is None:raise ValueError('review_ancestry')
    entry=json.loads(attempt['entry_json']);manifest=json.loads(support['manifest_json'])
    if _digest({'entry':entry,'manifest':manifest})!=digest:raise ValueError('review_ancestry')
    entry=validate_entry(entry,manifest)
    if current_baseline[1]['paragraphs'][:len(entry['paragraphs'])]!=entry['paragraphs']:
        raise ValueError('review_ancestry')
    return {'state':'delivered','input_digest':digest},entry,manifest


def _approved_revision(config,baseline,old,new,source_binding):
    data=Path(config['data_dir']).resolve();day=baseline[1]['day']
    directory=data/'narrative-writer'/'evidence-reviews'/day
    if directory.is_symlink() or not directory.is_dir():return False
    paths=sorted(directory.glob('*.approval.json'))
    if len(paths)>1000:return False
    for path in paths:
        try:
            record=_read_json(path)
            if (not isinstance(record,dict) or set(record)!=_REVIEW_KEYS or type(record['version']) is not int
                    or record['version']!=1 or record['decision']!='preserve_delivered_paragraphs'
                    or not isinstance(record['proposal_hash'],str)
                    or not re.fullmatch('[0-9a-f]{64}',record['proposal_hash'])
                    or not isinstance(record['reviewed_at'],str)
                    or datetime.fromisoformat(record['reviewed_at']).tzinfo is None
                    or record['evidence_id']!=old['id'] or record['day']!=day
                    or _digest(_proposal_form(record))!=record['proposal_hash']):
                continue
            anchor=_review_anchor(data,day,record['prior_input_digest'],baseline)
            anchored=next((item for item in anchor[2]['evidence'] if item['id']==old['id']),None)
            if anchored is None or _support_signature(anchored)!=_support_signature(old):continue
            current_paragraphs=sorted(_digest(p) for p in baseline[1]['paragraphs'] if old['id'] in p['evidence_ids'])
            if current_paragraphs!=record['paragraph_digests']:continue
            expected=_review_proposal(config,anchor,old,new,source_binding)
            if expected==_proposal_form(record):return True
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):
            continue
    return False


def _append_review(path,record):
    """Never replace an existing review record, even under concurrent attempts."""
    descriptor,temporary=tempfile.mkstemp(prefix='.review-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(record,stream,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        try:os.link(temporary,path)
        except FileExistsError:
            if _read_json(path)!=record:raise ValueError('review_conflict')
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def revision_review(config_path,day,*,evidence_id=None,proposal_hash=None,transport=None):
    """Explicit owner review only. Automated writers must never invoke approval."""
    if bool(evidence_id)==bool(proposal_hash):raise ValueError('review_action')
    config=load_config(Path(config_path),'personal');data=Path(config['data_dir']).resolve()
    publisher=Publisher(config['gh_path'],transport=transport)
    remote=publisher._read(f'entries/{day[:4]}/{day[5:7]}/{day}.md')
    baseline=_receipt_baseline(data,day)
    row,entry,prior=baseline
    if row is None or row['state']!='delivered' or remote is None:raise ValueError('review_receipt')
    span=_span(remote['content'],row['block_id'])
    if span is None or remote['content'][span[0]:span[1]]!=row['body']:raise ValueError('review_remote_changed')
    exported=_personal_pages(config,day,max_items=200,max_bytes=204800,offset=0,expected_snapshot=None)
    directory=None;original=None
    if proposal_hash is not None:
        if not isinstance(proposal_hash,str) or not re.fullmatch('[0-9a-f]{64}',proposal_hash):raise ValueError('proposal_hash')
        directory=data/'narrative-writer'/'evidence-reviews'/day
        try:original=_read_json(directory/(proposal_hash+'.proposal.json'))
        except OSError:raise ValueError('proposal_hash') from None
        if (not isinstance(original,dict) or set(original)!=_REVIEW_KEYS
                or original['decision']!='pending' or _digest(original)!=proposal_hash):raise ValueError('proposal_hash')
        evidence_id=original['evidence_id']
    old=next((item for item in prior['evidence'] if item['id']==evidence_id),None)
    current=next(({**item,'source':'personal','day':day} for item in exported['evidence'] if item['id']==evidence_id),None)
    if old is None or current is None:raise ValueError('review_source_missing')
    proposed=_review_proposal(config,baseline,old,current,exported['sourceBinding'])
    if original is not None and original!=proposed:raise ValueError('proposal_stale')
    directory=_directory(data,'narrative-writer','evidence-reviews',day)
    if proposal_hash is None:
        digest=_digest(proposed);path=directory/(digest+'.proposal.json');_append_review(path,proposed)
        return {'state':'revision_review_proposed','day':day,'proposal_hash':digest,'proposal':str(path)}
    approved={**proposed,'decision':'preserve_delivered_paragraphs','proposal_hash':proposal_hash,
              'reviewed_at':datetime.now(timezone.utc).isoformat()}
    path=directory/(_digest(approved)+'.approval.json');_append_review(path,approved)
    return {'state':'revision_review_approved','day':day,'approval':str(path)}


def _receipt_baseline(data,day):
    """Read exact immutable publisher support, with a digest-checked legacy fallback."""
    receipt_path=data/'narrative-writer'/'receipts.sqlite3'
    if not receipt_path.exists():
        return None,None,None
    if receipt_path.is_symlink():raise ValueError('review_required')
    connection=sqlite3.connect(receipt_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        row=connection.execute('SELECT * FROM narrative_receipts WHERE day=?',(day,)).fetchone()
        saved=None
        if row is not None and connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='narrative_support'").fetchone():
            saved=connection.execute('SELECT manifest_json FROM narrative_support WHERE day=? AND input_digest=?',
                                     (day,row['input_digest'])).fetchone()
    finally:
        connection.close()
    if row is None:return None,None,None
    if row['state'] not in {'delivered','pending'}:raise ValueError('review_required')
    body=bytes(row['body'])
    if hashlib.sha256(body).hexdigest()!=row['body_hash']:
        raise ValueError('review_required')
    entry=json.loads(row['entry_json'])
    snapshots=data/'narrative-writer'/'inputs'/(day+'-manifests')
    if snapshots.is_symlink():raise ValueError('review_required')
    prior=json.loads(saved['manifest_json']) if saved is not None else None
    candidates=sorted(snapshots.glob('*.json')) if snapshots.is_dir() else []
    # One reviewed baseline from the first manual delivery can seed ownership.
    # Its exact publisher input digest must match before it is trusted or saved.
    legacy=data/'narrative-writer'/(day+'-manifest.json')
    if legacy.exists():candidates.append(legacy)
    if prior is None:
        for path in candidates:
            candidate=_read_json(path)
            if _digest({'entry':entry,'manifest':candidate})==row['input_digest']:
                prior=candidate
                break
    if prior is None:raise ValueError('review_required')
    if _digest({'entry':entry,'manifest':prior})!=row['input_digest']:raise ValueError('review_required')
    entry=validate_entry(entry,prior)
    if render_entry(entry,row['block_id'])!=body:raise ValueError('review_required')
    return dict(row),entry,prior


def _old_paragraphs(baseline,content,current,*,bounded=False,work_missing=False,review_checker=None):
    row,entry,prior=baseline
    if row is None:
        if b'<!-- journal-narrative:' in content:raise ValueError('review_required')
        return [],None
    if row['state']!='delivered':raise ValueError('review_required')
    span=_span(content,row['block_id'])
    if span is None or content[span[0]:span[1]]!=row['body']:raise ValueError('review_required')
    prior_evidence={item['id']:item for item in prior['evidence']}
    current_evidence={item['id']:item for item in current['evidence']}
    for paragraph in entry['paragraphs']:
        for identity in paragraph['evidence_ids']:
            old=prior_evidence.get(identity);new=current_evidence.get(identity)
            if old is not None and old.get('source')=='work' and new is None and work_missing:
                continue
            if old is not None and old.get('source')=='personal' and new is None and bounded:
                raise LookupError('support_outside_bounded_export')
            if (old is None or new is None or _support_signature(old)!=_support_signature(new)
                    or (old.get('source')=='personal' and not old.get('revision'))):
                if old is not None and new is not None and review_checker is not None and review_checker(old,new):
                    continue
                raise ValueError('review_required')
    return entry['paragraphs'],prior


def _pending_replay(data,day,baseline):
    _,entry,manifest=baseline
    pending=_directory(data,'narrative-writer','pending')
    _write(pending/(day+'-entry.json'),entry)
    _write(pending/(day+'-manifest.json'),manifest)
    return {'state':'pending_publish','day':day,'entry':str(pending/(day+'-entry.json')),
            'manifest':str(pending/(day+'-manifest.json')),
            'receipts':str(data/'narrative-writer'/'receipts.sqlite3')}


def _personal_pages(config,day,*,max_items,max_bytes,offset,expected_snapshot):
    items=[];pages=0;last=None;counts={}
    # Leave room for the work handoff's maximum 32 moments in a 1000-item manifest.
    while pages<5 and len(items)<968:
        current=export_day(config['source_db'],day,max_items=min(max_items,968-len(items)),
            max_bytes=max_bytes,offset=offset,expected_snapshot=expected_snapshot,
            excluded_conversations=config.get('narrative_excluded_conversations',[]))
        pages+=1;items.extend(current['evidence']);last=current
        expected_snapshot=current['sourceFingerprint']
        for row in current['coverage']['sourceCounts']:
            counts[row['sourceKind']]=counts.get(row['sourceKind'],0)+row['exported']
        offset=current['nextOffset']
        if offset is None:break
    coverage=dict(last['coverage'])
    coverage.update(pages=pages,exported=len(items),omitted=coverage['eligible']-len(items))
    coverage['sourceCounts']=[{**row,'exported':counts.get(row['sourceKind'],0)}
                              for row in coverage['sourceCounts']]
    return {**last,'evidence':items,'coverage':coverage}


def prepare(config_path,day,*,transport=None,work_validator=None,max_items=200,max_bytes=204800,
            offset=0,expected_snapshot=None):
    """Read pinned private remote files and export local user evidence only.

    A missing work handoff remains missing. No raw work fallback is supported.
    Existing ownership or changed support ambiguity returns review_required.
    """
    if type(max_items) is not int or not 1<=max_items<=200:raise ValueError('bounds')
    if type(max_bytes) is not int or not 1<=max_bytes<=204800:raise ValueError('bounds')
    config=load_config(Path(config_path),'personal')
    data=Path(config['data_dir']).resolve()
    work_stop=data/'work-handoff-STOP'
    work_paused=work_stop.exists() or work_stop.is_symlink()
    exclusions=config.get('narrative_excluded_conversations',[])
    if not isinstance(exclusions,list):raise ValueError('exclusions')
    publisher=Publisher(config['gh_path'],transport=transport)
    remote=publisher._read(f'entries/{day[:4]}/{day[5:7]}/{day}.md')
    content=remote['content'] if remote else b''
    existing=content.decode('utf-8')
    try:
        baseline=_receipt_baseline(data,day)
    except Exception:
        return {'state':'review_required','day':day,'error_code':'prior_receipt_or_support_invalid'}
    if baseline[0] is not None and baseline[0]['state']=='pending':
        if work_paused and any(item.get('source')=='work' for item in baseline[2]['evidence']):
            return {'state':'review_required','day':day,'work_state':'paused',
                    'error_code':'work_handoff_paused_pending'}
        return _pending_replay(data,day,baseline)
    exported=_personal_pages(config,day,max_items=max_items,max_bytes=max_bytes,
        offset=offset,expected_snapshot=expected_snapshot)
    handoff=None if work_paused else publisher._read(f'imports/work-moments/v1/{day}.json')
    work=[];work_state='paused' if work_paused else 'missing'
    if handoff is not None:
        if len(handoff['content'])>1024*1024:raise ValueError('work_handoff_size')
        if work_validator is None:
            from journal_collector.work_moments import validate_handoff
            work_validator=validate_handoff
        validated=work_validator(json.loads(handoff['content'].decode('utf-8')),expected_day=day)
        if (not isinstance(validated,dict) or validated.get('version')!=1 or validated.get('day')!=day
                or not isinstance(validated.get('evidence'),list)):
            raise ValueError('work_handoff_schema')
        work=validated['evidence']
        # Defense in depth around injected validators and later schema drift.
        for item in work:
            if (not isinstance(item,dict) or item.get('source')!='work' or item.get('sanitized') is not True
                    or item.get('day')!=day or not isinstance(item.get('text'),str)):
                raise ValueError('work_handoff_schema')
        work_state='ready' if work else 'empty'
    personal=[{**item,'source':'personal','day':day} for item in exported['evidence']]
    manifest={'version':1,'day':day,'evidence':personal+work}
    # Validate IDs and support profile even before a writer has made paragraphs.
    validate_entry({'version':1,'day':day,'paragraphs':[]},manifest)
    metadata={'state':'prepared','day':day,'personal_count':len(personal),'work_count':len(work),
              'work_state':work_state,'coverage':exported['coverage'],
              'nextOffset':exported['nextOffset']}
    preservation_only=set()
    def reviewed(previous,current):
        accepted=_approved_revision(config,baseline,previous,current,exported['sourceBinding'])
        if accepted:preservation_only.add(previous['id'])
        return accepted
    try:
        old,prior=_old_paragraphs(baseline,content,manifest,
            bounded=exported['coverage']['omitted']>0,work_missing=work_state in {'missing','paused'},
            review_checker=reviewed)
    except LookupError:
        return {**metadata,'state':'need_pagination','error_code':'prior_support_outside_export'}
    except Exception:
        return {**metadata,'state':'review_required','error_code':'prior_span_or_support_changed'}
    if prior is not None:
        used={identity for paragraph in old for identity in paragraph['evidence_ids']}
        frozen={item['id']:item for item in prior['evidence'] if item['id'] in used}
        # Current meaning was checked above. Preserve all old record bytes,
        # including observation metadata, so harmless refreshes are not edits.
        available={item['id']:item for item in manifest['evidence']}
        available.update(frozen)
        manifest['evidence']=list(available.values())
        personal=[frozen.get(item['id'],item) for item in personal]
        work=[frozen.get(item['id'],item) for item in work]
    manifest['evidence']=sorted(manifest['evidence'],key=lambda item:item['id'])
    validate_entry({'version':1,'day':day,'paragraphs':old},manifest)
    bundle={'version':1,'day':day,'existing_prose':existing,
            'personal_evidence':personal,'work_evidence':work,'old_owned_paragraphs':old,
            'preservation_only_evidence_ids':sorted(preservation_only),
            'coverage':exported['coverage'],'work_state':work_state,
            'source_snapshot':exported['sourceFingerprint'],'nextOffset':exported['nextOffset'],
            'existing_sha':remote['sha'] if remote else None,'manifest_digest':_digest(manifest)}
    inputs=_directory(data,'narrative-writer','inputs')
    archive=_directory(inputs,day+'-manifests')
    if prior is not None:_write(archive/(_digest(prior)+'.json'),prior)
    _write(archive/(_digest(manifest)+'.json'),manifest)
    _write(inputs/(day+'-manifest.json'),manifest)
    _write(inputs/(day+'.json'),bundle)
    return {**metadata,'input':str(inputs/(day+'.json')),'manifest':str(inputs/(day+'-manifest.json'))}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--day',required=True)
    parser.add_argument('--max-items',type=int,default=200)
    parser.add_argument('--max-bytes',type=int,default=204800)
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--expected-snapshot')
    review=parser.add_mutually_exclusive_group()
    review.add_argument('--review-evidence',help='Prepare an owner-review proposal for one private evidence ID')
    review.add_argument('--approve-revision',help='Approve only the exact previously reviewed proposal SHA256')
    args=parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.review_evidence or args.approve_revision:
            result=revision_review(args.config,args.day,evidence_id=args.review_evidence,
                                   proposal_hash=args.approve_revision)
        else:
            result=prepare(args.config,args.day,max_items=args.max_items,max_bytes=args.max_bytes,
                           offset=args.offset,expected_snapshot=args.expected_snapshot)
        code=0 if result['state'] in {'prepared','revision_review_proposed','revision_review_approved'} else 2
    except Exception:
        result={'state':'error','error_code':'narrative_preparation_failed'};code=1
    print(json.dumps(result,ensure_ascii=True,allow_nan=False))
    return code


if __name__=='__main__':raise SystemExit(main())

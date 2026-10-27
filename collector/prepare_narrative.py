"""Prepare one private native-writer input. No model calls or journal writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from export_narrative_evidence import load_config
from journal_collector.narrative_evidence import export_day
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


def _old_paragraphs(baseline,content,current,*,bounded=False,work_missing=False):
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
        return _pending_replay(data,day,baseline)
    exported=_personal_pages(config,day,max_items=max_items,max_bytes=max_bytes,
        offset=offset,expected_snapshot=expected_snapshot)
    handoff=publisher._read(f'imports/work-moments/v1/{day}.json')
    work=[];work_state='missing'
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
    try:
        old,prior=_old_paragraphs(baseline,content,manifest,
            bounded=exported['coverage']['omitted']>0,work_missing=work_state=='missing')
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
    args=parser.parse_args(argv)
    os.umask(0o077)
    try:
        result=prepare(args.config,args.day,max_items=args.max_items,max_bytes=args.max_bytes,
                       offset=args.offset,expected_snapshot=args.expected_snapshot)
        code=0 if result['state']=='prepared' else 2
    except Exception:
        result={'state':'error','error_code':'narrative_preparation_failed'};code=1
    print(json.dumps(result,ensure_ascii=True,allow_nan=False))
    return code


if __name__=='__main__':raise SystemExit(main())

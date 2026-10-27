"""Bounded Mac-local work interpretation. Default is private preview, no delivery.

Freeze reviewed outbox bytes before publication. Existing outbox retries never
regenerate wording. Raw source SQLite is read-only; only sanitized handoffs leave.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
from zoneinfo import ZoneInfo

from journal_collector.cli import atomic_json
from journal_collector.runtime import worker_lock
from journal_collector.narrative_evidence import export_day, day_window
from journal_collector.local_work_model import OllamaSession, LocalModelError, LocalStop, process_export, MAX_MESSAGES, MAX_CHARS, STAGED_OUTPUT_ERRORS
from journal_collector.work_moments import WorkMomentPublisher, validate_handoff, WorkMomentError
from journal_collector.summary_publish import PublishError, GitHubTransport


def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def _digest(value):return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()
def _now():return datetime.now(timezone.utc).isoformat()


def _termination(_signum,_frame):raise LocalStop()


class StopGuardedTransport:
    """Recheck STOP before every private repository request, including writes."""
    def __init__(self,transport,stop):self.transport=transport;self.stop=stop
    def _call(self,name,*args):
        if self.stop.exists():raise LocalStop()
        return getattr(self.transport,name)(*args)
    def repo(self):return self._call('repo')
    def read(self,path):return self._call('read',path)
    def write(self,path,content,sha):return self._call('write',path,content,sha)


def load_config(path):
    path=Path(path).expanduser()
    if not path.is_absolute() or path.is_symlink():raise ValueError('configuration_binding')
    value=json.loads(path.read_text(encoding='utf-8'))
    if type(value) is not dict:raise ValueError('configuration_binding')
    for key in ('data_dir','db'):
        if type(value.get(key)) is not str or not Path(value[key]).is_absolute():raise ValueError('configuration_binding')
    data=Path(value['data_dir']).resolve()
    if path.resolve()!=data/'collector.json' or Path(value['db']).resolve()!=data/'collector.sqlite3':
        raise ValueError('configuration_binding')
    return value


def _runtime(config):
    data=Path(config['data_dir']).resolve();runtime=data/'work-narrative'
    if runtime.is_symlink():raise ValueError('runtime_binding')
    runtime.mkdir(mode=0o700,parents=True,exist_ok=True)
    return data,runtime


def _write(path,value):
    if path.is_symlink() or path.with_name(path.name+'.tmp').is_symlink():raise ValueError('runtime_binding')
    atomic_json(path,value);os.chmod(path,0o600)


def _load(path,model,ollama_path):
    if not path.exists():return {'version':1,'model':model,'ollama':str(ollama_path),'evidence':{},'outbox':[]}
    if path.is_symlink() or path.stat().st_size>4*1024*1024:raise ValueError('ledger_review_required')
    value=json.loads(path.read_text(encoding='utf-8'))
    if (type(value) is not dict or set(value)!={'version','model','ollama','evidence','outbox'}
            or value['version']!=1 or value['model']!=model or value['ollama']!=str(ollama_path)
            or type(value['evidence']) is not dict or type(value['outbox']) is not list):
        raise ValueError('ledger_review_required')
    seen=set()
    for record in value['outbox']:
        if (type(record) is not dict or set(record)!={'id','day','payload','digest','state','evidence'}
                or record['id'] in seen or record['state'] not in {'pending','delivered'}
                or record['digest']!=_digest(record['payload']) or record['id']!=record['digest']
                or type(record['evidence']) is not list):raise ValueError('ledger_review_required')
        validate_handoff(record['payload'],expected_day=record['day']);seen.add(record['id'])
        if any(key not in value['evidence'] for key in record['evidence']):raise ValueError('ledger_review_required')
    for key,item in value['evidence'].items():
        if (type(key) is not str or type(item) is not dict or set(item)!={'day','revision','state'}
                or item['state'] not in {'processed','withheld','review_required'} or type(item['revision']) is not str):
            raise ValueError('ledger_review_required')
        day_window(item['day'])
    return value


def _days(day,now=None):
    if day is not None:day_window(day);return [day]
    local=(datetime.now(timezone.utc) if now is None else now).astimezone(ZoneInfo('America/Chicago'))
    current=local.date()-(timedelta(days=1) if local.hour<3 else timedelta())
    return [current.isoformat(),(current-timedelta(days=1)).isoformat()]


def _batches(items):
    batch=[];size=0;oversize=0
    for item in items:
        length=len(item['text'])
        if length>MAX_CHARS:
            oversize+=1;continue
        if batch and (len(batch)>=MAX_MESSAGES or size+length>MAX_CHARS):
            yield batch,0;batch=[];size=0
        batch.append(item);size+=length
    if batch:yield batch,0
    if oversize:yield [],oversize


def _fair_batches(days,pending):
    """Offer one bounded batch per day before revisiting any day's backlog."""
    streams=[(selected,iter(batch for batch,_ in _batches(
        [item for item_day,item in pending if item_day==selected]) if batch)) for selected in days]
    while streams:
        remaining=[]
        for selected,stream in streams:
            try:batch=next(stream)
            except StopIteration:continue
            remaining.append((selected,stream))
            yield selected,batch
        streams=remaining


def execute(config,*,model,ollama_path,day=None,publish=False,gh_path=None,
            client_factory=OllamaSession,publisher_factory=WorkMomentPublisher,exporter=export_day,now=None):
    if publish and (type(gh_path) is not str or not Path(gh_path).is_absolute()):raise ValueError('publish_gh_required')
    data,runtime=_runtime(config)
    stop=data/'work-narrative-STOP'
    if stop.exists():return {'state':'stopped','checked_at':_now()}
    lock=worker_lock(runtime/'.worker')
    try:lock.__enter__()
    except RuntimeError:return {'state':'busy','checked_at':_now()}
    handlers={}
    if threading.current_thread() is threading.main_thread():
        for name in (signal.SIGTERM,signal.SIGINT):
            handlers[name]=signal.getsignal(name);signal.signal(name,_termination)
    status={'state':'running','checked_at':_now(),'processing':'on-device','processed':0,
            'delivered':0,'pending':0,'review_required':0,'withheld':0,'batches':0}
    def report():
        status['checked_at']=_now();_write(runtime/'status.json',status)
        return dict(status)
    def publisher():
        return publisher_factory(gh_path,transport=StopGuardedTransport(GitHubTransport(gh_path),stop))
    try:
        ledger_path=runtime/'state.json';ledger=_load(ledger_path,model,ollama_path)
        days=_days(day,now)
        for record in ledger['outbox']:
            if record['state']=='pending' and record['day'] not in days:days.append(record['day'])
        if len(days)>14:raise ValueError('ledger_review_required')
        exports={}
        for selected in days:
            if stop.exists():status['state']='stopped';return report()
            result=exporter(config['db'],selected,profile='work',max_items=10000,max_bytes=16*1024*1024)
            # Strict work export check without sending any source to a model.
            from journal_collector.work_moments import build_handoff
            build_handoff(selected,[],result,{'processing':'on-device','privacy_reviewed':True,'grounding_reviewed':True,'approved':[]})
            exports[selected]=result
        current={item['id']:item for value in exports.values() for item in value['evidence']}
        for key,item in ledger['evidence'].items():
            if item['day'] in exports and (key not in current or current[key]['revision']!=item['revision']):
                item['state']='review_required'
        if any(item['state']=='review_required' for item in ledger['evidence'].values()):
            _write(ledger_path,ledger);status['state']='review_required'
            status['review_required']=sum(item['state']=='review_required' for item in ledger['evidence'].values())
            return report()
        # Retry existing frozen payloads before starting inference.
        if publish:
            for record in ledger['outbox']:
                if record['state']!='pending' or status['delivered']>=2:continue
                if stop.exists():status['state']='stopped';return report()
                receipt=publisher().publish(record['payload'])
                if type(receipt) is not dict or receipt.get('state') not in {'published','reconciled'}:
                    raise WorkMomentError('verification')
                record['state']='delivered';_write(ledger_path,ledger);status['delivered']+=1
        pending=[]
        for selected in days:
            for item in exports[selected]['evidence']:
                if item['id'] not in ledger['evidence']:pending.append((selected,item))
        # Each context is owned and torn down before any GitHub transport call.
        for selected,batch in _fair_batches(days,pending):
            if status['batches']>=2:break
            if stop.exists():status['state']='stopped';return report()
            bounded=dict(exports[selected],evidence=batch)
            with client_factory(ollama_path,model) as client:
                result=process_export(bounded,client)
            status['batches']+=1;status['processed']+=len(batch)
            status['withheld']+=result['withheld']
            for item in batch:
                ledger['evidence'][item['id']]={'day':selected,'revision':item['revision'],
                    'state':'withheld' if item['id'] in result['withheld_ids'] else 'processed'}
            handoff=result['handoff'];record=None
            if handoff['moments']:
                digest=_digest(handoff)
                record={'id':digest,'day':selected,'payload':handoff,'digest':digest,'state':'pending',
                        'evidence':result['approved_ids']}
                ledger['outbox'].append(record)
            # Freeze reviewed wording and revisions before any network write.
            _write(ledger_path,ledger)
            if publish and record is not None and status['delivered']<2:
                if stop.exists():status['state']='stopped';return report()
                receipt=publisher().publish(record['payload'])
                if type(receipt) is not dict or receipt.get('state') not in {'published','reconciled'}:
                    raise WorkMomentError('verification')
                record['state']='delivered';_write(ledger_path,ledger);status['delivered']+=1
        status['pending']=sum(item['id'] not in ledger['evidence'] for value in exports.values() for item in value['evidence'])
        status['pending']+=sum(max(0,value.get('coverage',{}).get('eligible',len(value['evidence']))-len(value['evidence'])) for value in exports.values())
        status['frozen_pending']=sum(record['state']=='pending' for record in ledger['outbox'])
        status['state']=('review_required' if status['review_required'] else
                         'preview' if not publish and status['frozen_pending'] else
                         'pending' if status['pending'] or status['frozen_pending'] else 'idle')
        return report()
    except LocalStop:
        status['state']='stopped';return report()
    except Exception as error:
        codes={'network','auth','privacy','conflict','verification','review_required',
               'local_model_unverified','local_model_unavailable','local_model_request_failed',
               'local_model_invalid_output','local_model_batch_bounds','local_model_review_required',
               'local_model_timeout','local_model_response_too_large','local_model_redirect_refused'}|STAGED_OUTPUT_ERRORS
        code=getattr(error,'code',None)
        status.update(state='error',error_code=code if code in codes else 'work_processing_failed')
        return report()
    finally:
        for name,handler in handlers.items():signal.signal(name,handler)
        lock.__exit__(None,None,None)


def verify_published(config,*,model,ollama_path,gh_path,publisher_factory=WorkMomentPublisher):
    """Read-only proof for installer start; STOP intentionally does not block it."""
    data=Path(config['data_dir']).resolve();runtime=data/'work-narrative'
    if runtime.is_symlink():raise ValueError('runtime_binding')
    ledger=_load(runtime/'state.json',model,ollama_path)
    for record in reversed(ledger['outbox']):
        if record['state']!='delivered' or not record['payload']['moments']:continue
        publisher=publisher_factory(gh_path)
        remote=publisher.publisher._read('imports/work-moments/v1/'+record['day']+'.json')
        if remote is None:continue
        manifest=validate_handoff(remote['content'],expected_day=record['day'])
        actual={item['id']:item for item in manifest['evidence']}
        expected=validate_handoff(record['payload'],expected_day=record['day'])['evidence']
        if all(actual.get(item['id'])==item for item in expected):
            return {'state':'verified','day':record['day'],'moments':len(expected),'blob_sha':remote['sha']}
    raise WorkMomentError('verification')


def main(argv=None):
    if sys.platform!='darwin':
        print(json.dumps({'state':'error','error_code':'work_requires_macos'},allow_nan=False))
        return 1
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--model',required=True)
    parser.add_argument('--ollama',required=True);parser.add_argument('--gh')
    parser.add_argument('--day');parser.add_argument('--publish',action='store_true')
    parser.add_argument('--once',action='store_true',help='One bounded pass (the default)')
    parser.add_argument('--dry-run',action='store_true');parser.add_argument('--verify-handoff-only',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.publish and args.dry_run:raise ValueError('conflicting_modes')
        config=load_config(args.config.resolve())
        if args.verify_handoff_only:
            if not args.gh or not Path(args.gh).is_absolute():raise ValueError('publish_gh_required')
            result=verify_published(config,model=args.model,ollama_path=args.ollama,gh_path=args.gh)
        else:result=execute(config,model=args.model,ollama_path=args.ollama,gh_path=args.gh,day=args.day,publish=args.publish)
    except Exception:result={'state':'error','error_code':'work_configuration_or_verification_failed','checked_at':_now()}
    print(json.dumps(result,ensure_ascii=True,allow_nan=False))
    return 1 if result.get('state')=='error' else 0


if __name__=='__main__':raise SystemExit(main())

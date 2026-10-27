"""One bounded local-only classification and private journal delivery pass."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from journal_collector.cli import atomic_json
from journal_collector.runtime import worker_lock
from journal_collector.work_summary import SummaryQueue
from journal_collector.summary_publish import Publisher, validate_fragment


def now():
    return datetime.now(timezone.utc).isoformat()


def load_config(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict): raise ValueError('configuration')
    for key in ('data_dir','source_db','queue_db','gh_path'):
        if not isinstance(value.get(key),str) or not Path(value[key]).is_absolute():
            raise ValueError('absolute_paths_required')
    if value.get('repository','snackdriven/day-crumbs') != 'snackdriven/day-crumbs' or value.get('branch','main') != 'main':
        raise ValueError('destination_not_allowed')
    if type(value.get('enabled',False)) is not bool: raise ValueError('enabled_boolean_required')
    fixed_enabled=value.get('fixed_publishing_enabled',True)
    if type(fixed_enabled) is not bool: raise ValueError('fixed_publishing_enabled_boolean_required')
    value['fixed_publishing_enabled']=fixed_enabled
    for key, default, maximum in [('limit',500,500),('max_publish',20,20)]:
        item=value.get(key,default)
        if type(item) is not int or not 1 <= item <= maximum: raise ValueError('invalid_limit')
        value[key]=item
    if Path(value['source_db']).resolve() == Path(value['queue_db']).resolve():
        raise ValueError('separate_queue_required')
    data=Path(value['data_dir']).resolve()
    if path.resolve().parent != data:
        raise ValueError('configuration_runtime_binding_required')
    if (Path(value['source_db']).resolve() != data/'collector.sqlite3'
            or Path(value['queue_db']).resolve() != data/'work-summary.sqlite3'):
        raise ValueError('private_runtime_binding_required')
    return value


def safe_counts(value):
    if not isinstance(value,dict): return {}
    counts=value.get('counts',value)
    if not isinstance(counts,dict): return {}
    result={key: counts[key] for key in ('prepared','queued','review_required','delivered','pending',
                                       'processed','ready','publishing','invalidated','scanned','processed_revisions')
            if type(counts.get(key)) is int}
    if 'ready' in result: result['queued']=result['ready']+result.get('publishing',0)
    return result


def execute(config, *, dry_run=False, runtime=None):
    # Bindings come only from trusted entrypoint code, never source/config text.
    bindings=runtime or {'queue':SummaryQueue,'publisher':Publisher,'validate':validate_fragment,
        'stop':'summary-STOP','lock':'.summary-worker','status':'summary-status.json'}
    data=Path(config['data_dir']); data.mkdir(parents=True,exist_ok=True)
    stop=data/bindings['stop']
    if stop.exists(): return {'state':'stopped','enabled':config.get('enabled',False)}
    if not config.get('enabled',False) and not dry_run: return {'state':'disabled','enabled':False}
    lock=worker_lock(data/bindings['lock'])
    try:
        lock.__enter__()
    except RuntimeError:
        return {'state':'busy','enabled':config.get('enabled',False)}
    health={'state':'running','enabled':config.get('enabled',False),'pid':os.getpid(),
            'checked_at':now(),'prepared':0,'published':0}
    def report():
        health['checked_at']=now(); atomic_json(data/bindings['status'],health)
    try:
        if config.get('enabled') is True and config.get('fixed_publishing_enabled',True) is False:
            health={'state':'retired','enabled':True,'fixed_publishing_enabled':False}
            report()
            return health
        report()
        with bindings['queue'](config['source_db'],config['queue_db']) as queue:
            preparation=queue.prepare(limit=config['limit'])
            health.update(safe_counts(preparation))
            if dry_run:
                items=queue.ready(limit=config['max_publish'])
                fragments=[bindings['validate'](item) for item in items]
                health.update(state='preview',**safe_counts(queue.status())); report()
                return dict(health,fragments=fragments)
            publisher=bindings['publisher'](config['gh_path'])
            for _ in range(config['max_publish']):
                if stop.exists():
                    health['state']='stopped'; break
                items=queue.ready(limit=1)
                if not items: break
                item=bindings['validate'](items[0])
                if not queue.begin_publish(item['fragment_id']):
                    continue
                try:
                    receipt=publisher.publish(item)
                    if not isinstance(receipt,dict) or receipt.get('state') not in {'published','reconciled'}:
                        raise RuntimeError('delivery_unconfirmed')
                    outcome=queue.delivered(item['fragment_id'],receipt)
                    health['published']+=1
                    if isinstance(outcome,dict) and outcome.get('state')=='review_required':
                        health['state']='review_required'; break
                except Exception as error:
                    code=getattr(error,'code','publish_failed')
                    allowed={'privacy','conflict','manual_edit','malformed_markers','network','verification',
                             'invalid_fragment','destination','transport','auth','remote_data'}
                    code=code if code in allowed else 'publish_failed'
                    queue.failed(item['fragment_id'],code)
                    health.update(state='error',error_code=code)
                    break
            health.update(safe_counts(queue.status()))
            if health['state']=='running':
                health['state']='review_required' if health.get('review_required',0) else 'idle'
                health['last_success']=now()
            report()
            return health
    except Exception as error:
        code='timezone_data_unavailable' if type(error).__name__=='ZoneInfoNotFoundError' else 'processing_failed'
        health.update(state='error',error_code=code); report()
        return health
    finally:
        lock.__exit__(None,None,None)


def main(argv=None):
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--once',action='store_true',help='One pass (also the default)')
    parser.add_argument('--dry-run',action='store_true',help='Prepare local safe fragments without remote access')
    args=parser.parse_args(argv)
    try:
        config=load_config(args.config.expanduser().resolve())
        result=execute(config,dry_run=args.dry_run)
    except Exception:
        result={'state':'error','error_code':'configuration_failed'}
    print(json.dumps(result,ensure_ascii=True,allow_nan=False))
    return 1 if result.get('state')=='error' else 0


if __name__=='__main__':
    raise SystemExit(main())

"""Bounded source-local historical coordinator; metadata-only stdout/status."""
import argparse
import json
import os
from pathlib import Path
import sys

from journal_collector.history_collect import run_once as collect_history, load_config as load_history_config
from journal_collector.runtime import worker_lock
from journal_collector.work_history import HistoryQueue, discover_days
from work_narrative_service import execute, load_config, verify_published, _days, _write, _now


def run_once(config,*,model,ollama_path,gh_path,publish=False,now=None,
             collector=collect_history,worker=execute,discover=discover_days):
    data=Path(config['data_dir']);runtime=data/'work-history'
    if runtime.is_symlink():raise ValueError('history_binding')
    runtime.mkdir(parents=True,mode=0o700,exist_ok=True)
    stop=data/'work-history-STOP'
    if stop.exists():return {'state':'stopped'}
    lock=worker_lock(runtime/'.worker')
    try:lock.__enter__()
    except RuntimeError:return {'state':'busy'}
    try:
        ingestion=collector(load_history_config(data/'collector.json'))
        if stop.exists():return {'state':'stopped'}
        ingestion_state=ingestion.get('status',ingestion.get('state','unknown'))
        if ingestion_state in {'stopped','error'}:return {'state':ingestion_state,'ingestion_state':ingestion_state}
        queue=HistoryQueue(runtime/'queue.json');queue.discover(discover(config['db']))
        live=_days(None,now)
        results=[]
        for day in live:
            if stop.exists():return {'state':'stopped'}
            result=worker(config,model=model,ollama_path=ollama_path,gh_path=gh_path,
                day=day,publish=publish,history=True)
            results.append(result)
            if day in queue.value['days']:queue.ack(day,result)
            if result.get('state') in {'busy','stopped','error','review_required'}:break
        else:
            selected=queue.next_day(exclude=live)
            if selected and not stop.exists():
                result=worker(config,model=model,ollama_path=ollama_path,gh_path=gh_path,
                    day=selected,publish=publish,history=True)
                results.append(result);queue.ack(selected,result)
        states=[item.get('state') for item in results]
        queue_summary=queue.summary()
        state=next((state for state in ('error','review_required','stopped','busy') if state in states),
                   'pending' if queue_summary['pending'] or ingestion_state in {'partial','unknown'} else
                   'review_required' if queue_summary['review_required'] else
                   'gaps' if queue_summary['settled_with_gaps'] or ingestion_state=='gaps' else 'idle')
        status={'state':state,'checked_at':_now(),'processing':'on-device',
                'queue':queue_summary,'passes':len(results),'ingestion_state':ingestion_state,
                'ingestion_queue':{key:ingestion.get('queue',{}).get(key,0) for key in ('pending','eof','gap','error','missing')},
                'delivered':sum(item.get('delivered',0) for item in results),
                'account_coverage':'partial'}
        _write(runtime/'status.json',status)
        return status
    finally:lock.__exit__(None,None,None)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--model',required=True);parser.add_argument('--ollama',required=True)
    parser.add_argument('--gh',required=True);parser.add_argument('--publish',action='store_true')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--inventory-only',action='store_true')
    parser.add_argument('--verify-handoff-only',action='store_true')
    args=parser.parse_args(argv)
    try:
        if sys.platform!='darwin':raise ValueError('mac_required')
        os.umask(0o077)
        config=load_config(args.config)
        if sum((args.inventory_only,args.verify_handoff_only,args.publish))>1:raise ValueError('conflicting_modes')
        if args.inventory_only:
            days=discover_days(config['db']);queue=Path(config['data_dir'])/'work-history'/'queue.json'
            result={'state':'inventory','eligible_days':len(days),'account_coverage':'partial',
                    'queue':HistoryQueue(queue).summary()}
        elif args.verify_handoff_only:
            result=verify_published(config,model=args.model,ollama_path=args.ollama,gh_path=args.gh)
        else:result=run_once(config,model=args.model,ollama_path=args.ollama,gh_path=args.gh,publish=args.publish)
    except Exception:result={'state':'error','error_code':'work_history_failed'}
    print(json.dumps(result,sort_keys=True))
    return int(result.get('state')=='error')


if __name__=='__main__':raise SystemExit(main())

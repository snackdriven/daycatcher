"""Import one already-collected native ChatGPT page into the local source store."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from journal_collector.chatgpt_native import MAX_BYTES, normalize_page
from journal_collector.store import Store

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('config','input','expected-thread','since','until'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    try:
        path=Path(args.config).resolve()
        config=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(config,dict): raise ValueError('config')
        if not all(isinstance(config.get(k),str) and Path(config[k]).is_absolute() for k in ('data_dir','db')):
            raise ValueError('paths')
        data=Path(config['data_dir']).resolve(); db=Path(config['db']).resolve()
        if path!=data/'collector.json' or db!=data/'collector.sqlite3': raise ValueError('binding')
        if 'source_db' in config and Path(config['source_db']).resolve()!=db: raise ValueError('source_binding')
        if (data/'native-chatgpt-STOP').exists():
            print(json.dumps({'state':'stopped'}));return 0
        with Path(args.input).open('rb') as stream: raw=stream.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES: raise ValueError('input_bound')
        value=json.loads(raw.decode('utf-8-sig'))
        batch=normalize_page(value,expected_thread_id=args.expected_thread,since=args.since,
                             until=args.until,now=datetime.now(timezone.utc).isoformat())
        report={'state':'dry_run' if args.dry_run else 'imported','coverage':batch['coverage']}
        if not args.dry_run:
            if (data/'native-chatgpt-STOP').exists():
                print(json.dumps({'state':'stopped'}));return 0
            with Store(db) as store: result=store.ingest_batch(batch)
            report['counts']={k:v for k,v in result.items() if k!='batch_id'}
        print(json.dumps(report,allow_nan=False));return 0
    except Exception:
        print(json.dumps({'state':'error','error_code':'native_chatgpt_import_failed'}));return 1

if __name__=='__main__': raise SystemExit(main())

"""Export verified human evidence privately. stdout contains metadata only.

Work mode runs on its source Mac. Its output contains raw work user messages
and must remain there for an approved on-device sanitizer. This is no transport.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

from journal_collector.narrative_evidence import export_day


def load_config(path,profile):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('config_binding')
    if profile=='personal':
        from personal_summary_service import load_config as load_personal, configure_timezone
        value=load_personal(path)
        configure_timezone(value)
    elif profile=='work':
        if sys.platform!='darwin':
            raise ValueError('work_source_mac_required')
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or value.get('version')!=1:
            raise ValueError('config_schema')
        for key in ('data_dir','db'):
            if not isinstance(value.get(key),str) or not Path(value[key]).is_absolute():
                raise ValueError('absolute_paths')
        data=Path(value['data_dir']).resolve()
        if path.resolve()!=data/'collector.json' or Path(value['db']).resolve()!=data/'collector.sqlite3':
            raise ValueError('runtime_binding')
        value['source_db']=value['db']
        ZoneInfo('America/Chicago')
    else:
        raise ValueError('profile')
    data=Path(value['data_dir']).resolve(strict=True)
    checkout=Path(__file__).resolve().parent.parent
    if data==checkout or checkout in data.parents:
        raise ValueError('private_runtime_required')
    if (data/'narrative-STOP').exists():
        raise ValueError('stopped')
    return value


def private_write(data,profile,result):
    """Atomic body output beneath the canonical runtime, never an arbitrary path."""
    directory=data
    for part in ('narrative-evidence',profile):
        directory=directory/part
        if directory.is_symlink():
            raise ValueError('output_binding')
        directory.mkdir(mode=0o700,exist_ok=True)
    name=result['day']+'-'+result['exportId']+'.json'
    target=directory/name
    if target.is_symlink():
        raise ValueError('output_binding')
    descriptor,temporary=tempfile.mkstemp(prefix='.narrative-',suffix='.tmp',dir=directory)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return name


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--profile',choices=('personal','work'),required=True)
    parser.add_argument('--day',required=True)
    parser.add_argument('--max-items',type=int,default=200)
    parser.add_argument('--max-bytes',type=int,default=204800)
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--expected-snapshot')
    parser.add_argument('--exclude-conversation',action='append',default=[])
    args=parser.parse_args(argv)
    os.umask(0o077)
    try:
        config=load_config(args.config,args.profile)
        exclusions=config.get('narrative_excluded_conversations',[])
        if not isinstance(exclusions,list) or any(not isinstance(x,str) for x in exclusions):
            raise ValueError('invalid_exclusions')
        result=export_day(config['source_db'],args.day,profile=args.profile,max_items=args.max_items,
                          max_bytes=args.max_bytes,offset=args.offset,expected_snapshot=args.expected_snapshot,
                          excluded_conversations=[*exclusions,*args.exclude_conversation])
        name=private_write(Path(config['data_dir']).resolve(),args.profile,result)
        metadata={'state':'exported','profile':args.profile,'day':args.day,'exportId':result['exportId'],
                  'file':name,'coverage':result['coverage'],'nextOffset':result['nextOffset'],
                  'sourceFingerprint':result['sourceFingerprint']}
        code=0
    except Exception:
        metadata={'state':'error','error_code':'narrative_export_failed'}
        code=1
    print(json.dumps(metadata,ensure_ascii=True,allow_nan=False))
    return code


if __name__=='__main__':
    raise SystemExit(main())

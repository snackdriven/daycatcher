"""Native Windows personal-context pass; no model calls or desktop control."""
import argparse
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo, reset_tzpath

import summary_service as base
from journal_collector.personal_summary import PersonalSummaryQueue
from journal_collector.summary_publish import Publisher, validate_fragment


def load_config(path):
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or value.get('profile')!='personal': raise ValueError('profile')
    for key in ('data_dir','source_db','queue_db','gh_path','tzdata_dir'):
        if not isinstance(value.get(key),str) or not Path(value[key]).is_absolute(): raise ValueError('absolute_paths')
    data=Path(value['data_dir']).resolve()
    if path.resolve()!=data/'personal-summary.json': raise ValueError('config_binding')
    for key,name in [('source_db','collector.sqlite3'),('queue_db','personal-summary.sqlite3'),('tzdata_dir','tzdata')]:
        if Path(value[key]).resolve()!=data/name: raise ValueError('runtime_binding')
    if value.get('repository')!='snackdriven/day-crumbs' or value.get('branch')!='main': raise ValueError('destination')
    if type(value.get('enabled')) is not bool: raise ValueError('enabled')
    fixed_enabled=value.get('fixed_publishing_enabled',True)
    if type(fixed_enabled) is not bool: raise ValueError('fixed_publishing_enabled')
    value['fixed_publishing_enabled']=fixed_enabled
    dot_enabled=value.get('dot_handoff_enabled',False)
    dot_days=value.get('dot_handoff_days',3)
    if type(dot_enabled) is not bool: raise ValueError('dot_handoff_enabled')
    if type(dot_days) is not int or not 1<=dot_days<=14: raise ValueError('dot_handoff_days')
    value['dot_handoff_enabled']=dot_enabled
    value['dot_handoff_days']=dot_days
    for key,default in [('limit',500),('max_publish',20)]:
        count=value.get(key,default)
        if type(count) is not int or not 1<=count<=default: raise ValueError('bounds')
        value[key]=count
    if not isinstance(value.get('tzdata_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',value['tzdata_sha256']):
        raise ValueError('timezone_binding')
    return value


def configure_timezone(config):
    path=Path(config['tzdata_dir'])/'America'/'Chicago'
    if path.is_symlink(): raise ValueError('timezone_file')
    blob=path.read_bytes()
    if not blob.startswith(b'TZif') or hashlib.sha256(blob).hexdigest()!=config['tzdata_sha256']:
        raise ValueError('timezone_integrity')
    reset_tzpath([str(Path(config['tzdata_dir']).resolve())]); ZoneInfo.clear_cache()
    ZoneInfo('America/Chicago')


def execute(config, *, dry_run=False):
    configure_timezone(config)
    dot_result=None
    if (config.get('dot_handoff_enabled') is True and config.get('enabled') is True
            and not dry_run and not (Path(config['data_dir'])/'personal-summary-STOP').exists()):
        try:
            # Lazy import avoids the entrypoint/config-loader import cycle.
            # Dot has its own lock, STOP gate and durable status receipt.
            from pull_dot_handoff import pull
            dot_result=_dot_metadata(pull(config,days=config.get('dot_handoff_days',3)))
        except Exception:
            dot_result={'state':'error','error_code':'dot_pull_failed'}
    result=base.execute(config,dry_run=dry_run,runtime={
        'queue':PersonalSummaryQueue,'publisher':partial(Publisher,profile='personal'),
        'validate':partial(validate_fragment,profile='personal'),
        'stop':'personal-summary-STOP','lock':'.personal-summary-worker',
        'status':'personal-summary-status.json'})
    # Do not rewrite the worker's health file: another worker may own it when
    # base returns busy. A Dot failure must not block other personal sources.
    return dict(result,dot_handoff=dot_result) if dot_result is not None else result


def _dot_metadata(value):
    if type(value) is not dict or value.get('state') not in {
            'idle','preview','stopped','disabled','busy','error'}:
        return {'state':'error','error_code':'dot_pull_failed'}
    result={'state':value['state']}
    for key in ('files','missing','fragments','new','revised','unchanged'):
        if type(value.get(key)) is int and value[key]>=0:
            result[key]=value[key]
    if type(value.get('enabled')) is bool:
        result['enabled']=value['enabled']
    if 'error_code' in value:
        code=value['error_code']
        allowed={'invalid_handoff','handoff_conflict','dot_pull_failed','invalid_fragment','destination','privacy',
                 'transport','network','auth','conflict','manual_edit','malformed_markers',
                 'remote_data','verification'}
        result['error_code']=code if type(code) is str and code in allowed else 'dot_pull_failed'
    return result


def main(argv=None):
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    # pythonw has absent streams. Log metadata only, never arbitrary exceptions.
    with ExitStack() as stack:
        if sys.stdout is None or sys.stderr is None:
            stream=stack.enter_context((args.config.resolve().parent/'personal-summary-worker.log').open('a',encoding='utf-8'))
            stack.enter_context(redirect_stdout(stream)); stack.enter_context(redirect_stderr(stream))
        config=None
        try:
            config=load_config(args.config.resolve())
            result=execute(config,dry_run=args.dry_run)
        except Exception:
            result={'state':'error','error_code':'personal_configuration_or_timezone_failed'}
            if config is not None:
                base.atomic_json(Path(config['data_dir'])/'personal-summary-status.json',result)
        print(json.dumps(result,ensure_ascii=True,allow_nan=False))
        return 1 if result.get('state')=='error' else 0


if __name__=='__main__':
    raise SystemExit(main())

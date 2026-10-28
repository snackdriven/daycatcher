"""Owned, disabled-first coordinator. Reuses the installed controlled producer.

Private data/ledgers and original producer code/settings are never replaced.
The source bundle supplies history code; all dependencies are pinned by hash.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time

_spec=importlib.util.spec_from_file_location('history_narrative_install',Path(__file__).with_name('narrative_install.py'))
base=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(base)
LABEL='com.snackdriven.daycatcher-work-history'
launchctl=base.launchctl


def paths(home):
    p=base.paths(home)
    p.update(code=p['base']/'history-code',
        plist=p['home']/'Library'/'LaunchAgents'/(LABEL+'.plist'),
        settings=p['data']/'work-history-install.json',stop=p['data']/'work-history-STOP',
        status=p['data']/'work-history'/'status.json')
    for path in p.values():base.safe(path)
    return p


def plist_value(p,value,enabled=False):
    return {'Label':LABEL,'ProgramArguments':[value['python'],str(p['code']/'work_history_service.py'),
        '--config',str(p['config']),'--model',value['model'],'--ollama',value['ollama'],
        '--gh',value['gh'],'--publish','--once'],
        'WorkingDirectory':str(p['code']),
        'EnvironmentVariables':{'HOME':str(p['home']),'PYTHONPATH':str(p['code']),
            'PYTHONUNBUFFERED':'1','OLLAMA_MODELS':value['models_dir']},
        'Disabled':not enabled,'RunAtLoad':True,'StartInterval':900,'KeepAlive':False,
        'Umask':0o077,'ExitTimeOut':30,
        'StandardOutPath':str(p['data']/'work-history.launchd.stdout.log'),
        'StandardErrorPath':str(p['data']/'work-history.launchd.stderr.log')}


def settings(p,check_code=True):
    value=base._json(p['settings'])
    if (type(value) is not dict or set(value)!={'version','policy','python','model','ollama','gh','models_dir','staged_at','files'}
            or value['version']!=1 or value['policy']!=base.POLICY or type(value['files']) is not dict
            or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}',value['model'])):raise ValueError('history_settings_binding')
    for key in ('python','ollama','gh'):
        if check_code:base.executable(value[key])
        elif type(value[key]) is not str or not Path(value[key]).is_absolute():raise ValueError('history_settings_binding')
    if base.model_root(value['models_dir'])!=value['models_dir']:raise ValueError('model_root_binding')
    datetime.fromisoformat(value['staged_at'])
    required={'work_history_service.py','work_narrative_service.py','journal_collector/work_history.py',
              'journal_collector/history_collect.py','journal_collector/work_language.py'}
    if not required.issubset(value['files']):raise ValueError('history_code_binding')
    for relative,digest in value['files'].items():
        if (type(relative) is not str or Path(relative).is_absolute() or '..' in Path(relative).parts
                or type(digest) is not str or not re.fullmatch('[0-9a-f]{64}',digest)):
            raise ValueError('history_code_binding')
        path=base.regular(p['code']/relative)
        if check_code and hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('history_code_changed')
    return value


def owned(p,check_code=True):
    value=settings(p,check_code)
    actual=plistlib.loads(base.regular(p['plist']).read_bytes())
    if type(actual.get('Disabled')) is not bool or actual!=plist_value(p,value,not actual['Disabled']):
        raise ValueError('history_plist_binding')
    return value,actual


def loaded(domain):return launchctl('print',domain+'/'+LABEL,check=False).returncode==0


def worker_locked(p):return base._base.worker_locked(p['data']/'work-history'/'.worker')


def prepare(p,source=None):
    original=base.paths(p['home']);previous,_=base.owned(original)
    base.collector_binding(original)
    if p['plist'].exists():
        _,plist=owned(p)
        if not plist['Disabled'] or not p['stop'].is_file() or worker_locked(p):raise ValueError('stop_before_reinstall')
    elif p['code'].exists() or p['settings'].exists():raise ValueError('history_partial_install_review_required')
    source=Path(source).resolve() if source is not None else Path(__file__).resolve().parents[1]
    originals=sorted((source/'journal_collector').glob('*.py'))+[
        source/'work_history_service.py',source/'work_narrative_service.py',
        source/'macos'/'history_install.py',source/'macos'/'narrative_install.py',source/'macos'/'install.py']
    required={'journal_collector/'+name for name in base.REQUIRED_PACKAGE}|{
        'journal_collector/history_collect.py','journal_collector/work_history.py',
        'journal_collector/store.py','journal_collector/local.py'}
    found={str(item.relative_to(source).as_posix()) for item in originals}
    if not required.issubset(found):raise ValueError('complete_history_bundle_required')
    bodies={str(item.relative_to(source).as_posix()):base.regular(item).read_bytes() for item in originals}
    # The privacy/model implementation must be byte-identical to the verified
    # installed controlled producer. Changes require its own deliberate migration.
    for name in ('work_language.py','local_work_model.py','work_moments.py'):
        relative='journal_collector/'+name
        if hashlib.sha256(bodies[relative]).hexdigest()!=previous['files'][relative]:
            raise ValueError('controlled_implementation_changed')
    for relative,body in bodies.items():
        target=p['code']/relative;base.safe(target.parent);target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        base._write(target,body)
    value={key:previous[key] for key in ('policy','python','model','ollama','gh','models_dir')}
    value.update(version=1,staged_at=datetime.now(timezone.utc).isoformat(),
        files={relative:hashlib.sha256(body).hexdigest() for relative,body in bodies.items()})
    base._write(p['settings'],json.dumps(value,sort_keys=True,indent=2)+'\n')
    base._write(p['stop'],'Historical coordinator disabled until verified start\n')
    base._write(p['plist'],plistlib.dumps(plist_value(p,value)))
    return {'prepared':True,'enabled':False,'launchd_loaded':False,'processing':'on-device'}


def start(p,domain):
    value,_=owned(p)
    original=base.paths(p['home']);installed,_=base.owned(original)
    base.raw_health(original,domain)
    # Actual delivered controlled moments must reconcile with the remote bytes.
    proof=base.verify_handoff(original,installed)
    if loaded(domain):
        if not p['stop'].exists():return {'enabled':True,'already_loaded':True,'publish_verified':True}
        stop(p,domain)
    if worker_locked(p):raise ValueError('history_worker_active')
    base.regular(p['stop']).unlink(missing_ok=True)
    base._write(p['plist'],plistlib.dumps(plist_value(p,value,True)))
    try:
        launchctl('enable',domain+'/'+LABEL);launchctl('bootstrap',domain,str(p['plist']))
        if not loaded(domain):raise ValueError('history_registration_not_confirmed')
    except Exception:
        base._write(p['stop'],'Historical coordinator startup failed\n')
        base._write(p['plist'],plistlib.dumps(plist_value(p,value)))
        raise
    return {'enabled':True,'launchd_loaded':True,'publish_verified':True,'moments':proof['moments']}


def stop(p,domain):
    value,_=owned(p,False)
    base._write(p['stop'],'Historical coordinator stopped\n')
    base._write(p['plist'],plistlib.dumps(plist_value(p,value)))
    launchctl('disable',domain+'/'+LABEL)
    if loaded(domain):launchctl('bootout',domain+'/'+LABEL)
    deadline=time.monotonic()+35
    while loaded(domain) or worker_locked(p):
        if time.monotonic()>=deadline:raise ValueError('history_stop_not_confirmed')
        time.sleep(.1)
    return {'stopped':True,'enabled':False,'retained_private_files':True}


def status(p,domain):
    _,plist=owned(p,False)
    result={'enabled':not plist['Disabled'],'stop_requested':p['stop'].exists(),
            'launchd_loaded':loaded(domain)}
    # Select exact known aggregate fields only; never echo arbitrary local JSON.
    if p['status'].is_file():
        value=base._json(p['status'])
        result['passes']=value.get('passes',0) if type(value.get('passes',0)) is int else 0
    return result


def inspect(p,action):
    """Run only a pinned metadata inventory or exact handoff readback."""
    value,_=owned(p)
    command=plist_value(p,value)['ProgramArguments'][:-2]+[
        '--inventory-only' if action=='inventory' else '--verify-handoff-only']
    result=subprocess.run(command,cwd=p['code'],env={**os.environ,'HOME':str(p['home']),
        'PYTHONPATH':str(p['code']),'OLLAMA_MODELS':value['models_dir']},
        capture_output=True,text=True,timeout=60)
    try:proof=json.loads(result.stdout)
    except (ValueError,TypeError):raise ValueError('history_inspection_failed') from None
    if result.returncode or type(proof) is not dict:raise ValueError('history_inspection_failed')
    if action=='inventory':
        if proof.get('state')!='inventory' or type(proof.get('eligible_days')) is not int:raise ValueError('history_inspection_failed')
        return {'state':'inventory','eligible_days':proof['eligible_days'],'account_coverage':'partial'}
    if (proof.get('state')!='verified' or type(proof.get('moments')) is not int or proof['moments']<1
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',proof.get('day',''))
            or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',proof.get('blob_sha',''))):
        raise ValueError('history_readback_failed')
    return {key:proof[key] for key in ('state','day','moments','blob_sha')}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan','install','start','stop','status','inventory','readback','uninstall'],default='plan',nargs='?')
    parser.add_argument('--home',type=Path,default=Path.home());parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args(argv)
    try:
        live=args.action in {'start','stop','status','inventory','readback','uninstall'}
        if live and (sys.platform!='darwin' or args.home!=Path.home()):raise ValueError('current_mac_home_required')
        if args.prepare_only and args.action!='install':raise ValueError('prepare_only_install')
        os.umask(0o077);p=paths(args.home);domain='gui/'+str(os.getuid()) if live else None
        if args.action=='plan':result={'state':'plan','enabled':False}
        elif args.action=='install':result=prepare(p)
        elif args.action=='start':result=start(p,domain)
        elif args.action=='status':result=status(p,domain)
        elif args.action in {'inventory','readback'}:result=inspect(p,args.action)
        else:
            result=stop(p,domain)
            if args.action=='uninstall':base.regular(p['plist']).unlink();result['uninstalled']=True
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps({'state':'error','error_code':'work_history_installation_failed'}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())

"""Prepare an isolated, disabled Mac work-moment producer; explicit start only.

Never edits/stops the raw collector or legacy summary job. Retiring that fixed
sink is a separate deliberate operation after a real verified work handoff.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
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

_spec=importlib.util.spec_from_file_location('narrative_base_install',Path(__file__).with_name('install.py'))
_base=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_base)
launchctl=_base.launchctl
LABEL='com.snackdriven.daycatcher-work-moments'
RAW_LABEL='com.snackdriven.conversation-collector'
REQUIRED_PACKAGE=('__init__.py','__main__.py','local_work_model.py','work_moments.py',
                  'narrative_evidence.py','summary_publish.py','cli.py','runtime.py')
STANDALONE=('export_narrative_evidence.py','work_narrative_service.py')


def safe(path):
    if not path.is_absolute():raise ValueError('absolute_path_required')
    for target in (path,*path.parents):
        if target.is_symlink():raise ValueError('symlink_binding')
    if path.exists() and hasattr(os,'getuid') and path.stat().st_uid!=os.getuid():
        raise ValueError('file_owner')
    return path


def regular(path):
    safe(path)
    if path.exists() and not path.is_file():raise ValueError('regular_file_required')
    return path


def paths(home):
    home=home.expanduser().absolute();safe(home)
    base=home/'Library'/'Application Support'/'ConversationCollector'
    p={'home':home,'base':base,'code':base/'narrative-code','raw_code':base/'code',
       'data':base/'runtime','config':base/'runtime'/'collector.json',
       'plist':home/'Library'/'LaunchAgents'/(LABEL+'.plist'),
       'raw_plist':home/'Library'/'LaunchAgents'/(RAW_LABEL+'.plist'),
       'settings':base/'runtime'/'work-narrative-install.json',
       'stop':base/'runtime'/'work-narrative-STOP',
       'status':base/'runtime'/'work-narrative'/'status.json'}
    for target in p.values():safe(target)
    return p


def _json(path):return json.loads(regular(path).read_text(encoding='utf-8'))
def _write(path,value):
    regular(path)
    _base.write_file(path,value,binary=isinstance(value,bytes))


def executable(value):
    if not isinstance(value,str) or not Path(value).is_absolute() or not Path(value).is_file():
        raise ValueError('executable_required')
    # Keep stable Homebrew executable symlinks, as the existing collector does.
    return str(Path(value).absolute())


def model_root(value,source=None):
    if not isinstance(value,str) or not value:raise ValueError('model_root_binding')
    root=Path(value).expanduser()
    if not root.is_absolute():raise ValueError('absolute_model_root_required')
    root=root.resolve()
    if root.exists() and not root.is_dir():raise ValueError('model_root_binding')
    # Resolve user-selected symlinks once so external-volume weights work while
    # launchd retains the same root. Weight/model verification belongs to worker.
    if any((parent/'.git').exists() for parent in (root,*root.parents)):
        raise ValueError('private_model_root_required')
    if source is not None:
        source=Path(source).resolve()
        checkout=next((parent for parent in (source,*source.parents) if (parent/'.git').exists()),source)
        if root==checkout or checkout in root.parents:raise ValueError('private_model_root_required')
    return str(root)


def collector_binding(p,explicit_python=None):
    config=_json(p['config']);value=plistlib.loads(regular(p['raw_plist']).read_bytes())
    if (not isinstance(config,dict) or config.get('version')!=1
            or config.get('data_dir')!=str(p['data']) or config.get('db')!=str(p['data']/'collector.sqlite3')
            or not isinstance(config.get('local'),dict)):
        raise ValueError('raw_config_binding')
    arguments=value.get('ProgramArguments')
    if (value.get('Label')!=RAW_LABEL or type(arguments) is not list or len(arguments)!=6
            or arguments[1:]!=['-m','journal_collector','watch','--config',str(p['config'])]
            or value.get('WorkingDirectory')!=str(p['raw_code'])
            or value.get('EnvironmentVariables',{}).get('PYTHONPATH')!=str(p['raw_code'])):
        raise ValueError('raw_plist_binding')
    python=executable(explicit_python or arguments[0])
    return config,python


def plist_value(p,settings,*,enabled=False):
    return {'Label':LABEL,'ProgramArguments':[settings['python'],str(p['code']/'work_narrative_service.py'),
        '--config',str(p['config']),'--model',settings['model'],'--ollama',settings['ollama'],'--gh',settings['gh'],'--publish'],
        'WorkingDirectory':str(p['code']),
        'EnvironmentVariables':{'HOME':str(p['home']),'PYTHONPATH':str(p['code']),'PYTHONUNBUFFERED':'1','OLLAMA_MODELS':settings['models_dir']},
        'RunAtLoad':True,'StartInterval':3600,'KeepAlive':False,'Disabled':not enabled,
        'Umask':0o077,'ExitTimeOut':30,
        'StandardOutPath':str(p['data']/'work-narrative.launchd.stdout.log'),
        'StandardErrorPath':str(p['data']/'work-narrative.launchd.stderr.log')}


def settings(p,*,check_code=True):
    value=_json(p['settings'])
    if (type(value) is not dict or set(value)!={'version','python','ollama','gh','model','models_dir','staged_at','files'}
            or type(value['version']) is not int or value['version']!=1
            or not isinstance(value['model'],str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}',value['model'])
            or not isinstance(value['files'],dict)):
        raise ValueError('settings_binding')
    for key in ('python','ollama','gh'):
        if not isinstance(value[key],str) or not Path(value[key]).is_absolute():raise ValueError('settings_binding')
        if check_code:executable(value[key])
    if model_root(value['models_dir'])!=value['models_dir']:raise ValueError('model_root_binding')
    datetime.fromisoformat(value['staged_at'])
    required={'journal_collector/'+name for name in REQUIRED_PACKAGE}|set(STANDALONE)|{'macos/install.py','macos/narrative_install.py'}
    if not required.issubset(value['files']):raise ValueError('code_binding')
    for relative,digest in value['files'].items():
        if (not isinstance(relative,str) or Path(relative).is_absolute() or '..' in Path(relative).parts
                or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)):
            raise ValueError('code_binding')
        installed=regular(p['code']/relative)
        if check_code and (not installed.is_file() or hashlib.sha256(installed.read_bytes()).hexdigest()!=digest):
            raise ValueError('code_changed')
    return value


def owned(p,*,check_code=True):
    value=settings(p,check_code=check_code)
    actual=plistlib.loads(regular(p['plist']).read_bytes())
    if type(actual.get('Disabled')) is not bool or actual!=plist_value(p,value,enabled=not actual['Disabled']):
        raise ValueError('owned_plist_binding')
    return value,actual


def prepare(p,*,ollama,model,python=None,gh=None,source=None):
    _,python=collector_binding(p,python)
    ollama=executable(ollama)
    if gh is None:
        summary=_json(p['data']/'summary.json')
        if (not isinstance(summary,dict) or summary.get('repository')!='snackdriven/day-crumbs'
                or summary.get('branch')!='main' or summary.get('data_dir')!=str(p['data'])):
            raise ValueError('existing_gh_binding')
        gh=summary.get('gh_path')
    gh=executable(gh)
    if not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}',model):
        raise ValueError('explicit_model_required')
    source=Path(source) if source is not None else Path(__file__).resolve().parents[1]
    models_dir=model_root(os.environ.get('OLLAMA_MODELS') or str(p['home']/'.ollama'/'models'),source)
    package=source/'journal_collector'
    for name in REQUIRED_PACKAGE:
        if not regular(package/name).is_file():raise ValueError('complete_bundle_required')
    originals=sorted(package.glob('*.py'))+[source/name for name in STANDALONE]+[
        source/'macos'/'install.py',source/'macos'/'narrative_install.py']
    for original in originals:
        if not regular(original).is_file():raise ValueError('complete_bundle_required')
    if p['plist'].exists():
        _,previous=owned(p)
        if not previous['Disabled']:raise ValueError('stop_before_reinstall')
    for directory in (p['code'],p['code']/'journal_collector',p['code']/'macos'):
        safe(directory);directory.mkdir(parents=True,exist_ok=True);os.chmod(directory,0o700)
    files={}
    for original in originals:
        relative=original.relative_to(source)
        body=original.read_bytes()
        _write(p['code']/relative,body)
        files[relative.as_posix()]=hashlib.sha256(body).hexdigest()
    value={'version':1,'python':python,'ollama':ollama,'gh':gh,'model':model,'models_dir':models_dir,
           'staged_at':datetime.now(timezone.utc).isoformat(),'files':files}
    _write(p['settings'],json.dumps(value,sort_keys=True,indent=2)+'\n')
    _write(p['stop'],'Producer disabled until deliberate start\n')
    _write(p['plist'],plistlib.dumps(plist_value(p,value)))
    return {'prepared':True,'enabled':False,'launchd_loaded':False,'live_verified':False,
            'fixed_summary_retired':False,'code':str(p['code']),'plist':str(p['plist'])}


def loaded(domain,label=LABEL):return launchctl('print',domain+'/'+label,check=False).returncode==0

def raw_locked(data):return _base.worker_locked(data)

def worker_locked(p):
    return _base.worker_locked(p['data']/'work-narrative'/'.worker')


def process_alive(pid):
    if type(pid) is not int or pid<1:return False
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def raw_health(p,domain):
    config,_=collector_binding(p)
    value=_json(p['data']/'local-status.json')
    scan=value.get('scan',{})
    try:
        stamp=datetime.fromisoformat(value['checked_at'].replace('Z','+00:00'))
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
    except (ValueError,TypeError,KeyError):raise ValueError('raw_health_stale') from None
    if (value.get('state')!='running' or type(value.get('scans')) is not int or value['scans']<1
            or not process_alive(value.get('pid')) or not raw_locked(p['data'])
            or not loaded(domain,RAW_LABEL) or not -30<=age<=300
            or not isinstance(scan,dict) or scan.get('status') not in {'complete','partial'}
            or scan.get('errors',[])!=[] or scan.get('error_type')
            or (p['data']/'STOP').exists()):
        raise ValueError('raw_collector_not_healthy')
    return {'healthy':True,'scan_status':scan['status'],'backlog_bytes':scan.get('backlog_bytes',0)}


def verify_handoff(p,value):
    status=regular(p['status'])
    if not status.is_file() or status.stat().st_mtime<datetime.fromisoformat(value['staged_at']).timestamp():
        raise ValueError('fresh_real_handoff_required')
    command=plist_value(p,value)['ProgramArguments'][:-1]+['--verify-handoff-only']
    result=subprocess.run(command,cwd=p['code'],env={**os.environ,'HOME':str(p['home']),
        'PYTHONPATH':str(p['code']),'OLLAMA_MODELS':value['models_dir']},capture_output=True,text=True,timeout=60)
    try:proof=json.loads(result.stdout)
    except (ValueError,TypeError):raise ValueError('handoff_verification_failed') from None
    if (result.returncode!=0 or type(proof) is not dict or proof.get('state')!='verified'
            or type(proof.get('moments')) is not int or proof['moments']<1
            or not isinstance(proof.get('blob_sha'),str) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',proof['blob_sha'])
            or not isinstance(proof.get('day'),str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',proof['day'])):
        raise ValueError('real_handoff_not_verified')
    return {key:proof[key] for key in ('state','day','moments','blob_sha')}


def start(p,domain):
    value,_=owned(p)
    raw_health(p,domain)
    proof=verify_handoff(p,value)
    if loaded(domain):
        if not p['stop'].exists():return {'enabled':True,'already_loaded':True,'publish_verified':True}
        stop(p,domain)
    if worker_locked(p):raise ValueError('producer_worker_still_active')
    regular(p['stop']).unlink(missing_ok=True)
    _write(p['plist'],plistlib.dumps(plist_value(p,value,enabled=True)))
    try:
        launchctl('enable',domain+'/'+LABEL)
        launchctl('bootstrap',domain,str(p['plist']))
        if not loaded(domain):raise RuntimeError('producer_registration_not_confirmed')
    except Exception:
        _write(p['stop'],'Producer start failed; delivery remains stopped\n')
        _write(p['plist'],plistlib.dumps(plist_value(p,value)))
        raise
    return {'enabled':True,'launchd_loaded':True,'publish_verified':True,'moments':proof['moments'],
            'fixed_summary_retired':False}


def stop(p,domain):
    # Validate exact job ownership before touching launchctl or the definition.
    value,_=owned(p,check_code=False)
    _write(p['stop'],'Producer stop requested\n')
    _write(p['plist'],plistlib.dumps(plist_value(p,value)))
    launchctl('disable',domain+'/'+LABEL)
    if loaded(domain):launchctl('bootout',domain+'/'+LABEL)
    deadline=time.monotonic()+35
    while True:
        still_loaded,still_locked=loaded(domain),worker_locked(p)
        if not still_loaded and not still_locked:break
        if time.monotonic()>=deadline:raise RuntimeError('producer_stop_not_confirmed')
        time.sleep(0.1)
    return {'stopped':True,'enabled':False,'launchd_loaded':False,'fixed_summary_retired':False}


def status(p,domain):
    _,value=owned(p,check_code=False)
    return {'enabled':not value['Disabled'],'stop_requested':p['stop'].exists(),
            'launchd_loaded':loaded(domain),'fixed_summary_retired':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',default='plan',choices=['plan','install','start','stop','status','uninstall'])
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--home',type=Path,default=Path.home())
    parser.add_argument('--python');parser.add_argument('--ollama');parser.add_argument('--model');parser.add_argument('--gh')
    args=parser.parse_args(argv)
    try:
        live=args.action in {'start','stop','status','uninstall'}
        if live and (sys.platform!='darwin' or args.home!=Path.home()):raise ValueError('live_current_mac_home_required')
        if args.prepare_only and args.action!='install':raise ValueError('prepare_only_install')
        if live and any((args.python,args.ollama,args.model,args.gh)):raise ValueError('prepare_overrides_only')
        os.umask(0o077);p=paths(args.home)
        domain='gui/'+str(os.getuid()) if live else None
        if args.action=='plan':
            result={'state':'plan','enabled':False,'code':str(p['code']),'fixed_summary_retirement':'separate_explicit_step'}
        elif args.action=='install':
            result=prepare(p,ollama=args.ollama,model=args.model,python=args.python,gh=args.gh)
        elif args.action=='start':result=start(p,domain)
        elif args.action=='status':result=status(p,domain)
        else:
            result=stop(p,domain)
            if args.action=='uninstall':
                regular(p['plist']).unlink();result.update(uninstalled=True,retained_private_files=True)
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps({'state':'error','error_code':'producer_installation_failed'}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())

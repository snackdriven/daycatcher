"""Discover only validated controlled work handoff days from the pinned repo.

--config ABS --once performs at most twenty changed-body validations. No writes
to GitHub, old Git blobs/history, source-local raw evidence, or model calls.
"""
import argparse
import base64
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import re

from export_narrative_evidence import load_config
from journal_collector.cli import atomic_json
from journal_collector.narrative_evidence import day_window
from journal_collector.runtime import worker_lock
from journal_collector.summary_publish import Publisher, GitHubTransport, REPOSITORY, BRANCH
from journal_collector.work_moments import validate_handoff, MAX_BYTES as HANDOFF_BYTES

PREFIX = 'imports/work-moments/v1'
MAX_BYTES = 16*1024*1024
SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


def fresh_state(): return {'version': 1, 'turn': 0, 'days': {}}


def check_stop(config):
    data = Path(config['data_dir'])
    if any(p.exists() for p in (data/'narrative-STOP', data/'history'/'STOP', data/'work-handoff-STOP', data/'narrative-history'/'STOP')):
        raise ValueError('stopped')


def valid_days(state):
    return {'version': 1, 'days': [{'day': day, 'fingerprint': row['fingerprint']}
            for day, row in sorted(state['days'].items())
            if row.get('present') and row.get('fingerprint') and row['status'] != 'invalid']}


def _listing_row(row):
    if not isinstance(row, dict) or row.get('type') != 'file': raise ValueError('listing_row')
    name = row.get('name')
    if not isinstance(name, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', name): raise ValueError('listing_name')
    day = name[:-5]; day_window(day)
    if row.get('path') != PREFIX+'/'+name or not isinstance(row.get('sha'), str) or not SHA.fullmatch(row['sha']):
        raise ValueError('listing_binding')
    if type(row.get('size')) is not int or not 0 <= row['size'] <= HANDOFF_BYTES: raise ValueError('listing_size')
    return day


def _body(response, metadata, day):
    if not isinstance(response, dict): raise ValueError('body_schema')
    for key in ('type', 'name', 'path', 'sha', 'size'):
        if response.get(key) != metadata[key]: raise ValueError('body_binding')
    encoded = response.get('content')
    if response.get('encoding') != 'base64' or not isinstance(encoded, str) or len(encoded) > HANDOFF_BYTES*2:
        raise ValueError('body_encoding')
    raw = base64.b64decode(encoded.replace('\n', ''), validate=True)
    if len(raw) != metadata['size'] or len(raw) > HANDOFF_BYTES: raise ValueError('body_size')
    header = b'blob '+str(len(raw)).encode('ascii')+b'\0'
    digest = hashlib.sha1(header+raw).hexdigest() if len(metadata['sha']) == 40 else hashlib.sha256(header+raw).hexdigest()
    if digest != metadata['sha']: raise ValueError('blob_sha')
    validate_handoff(raw, day)
    return hashlib.sha256(raw).hexdigest()


def discover(config, state, transport=None):
    check_stop(config)
    transport = transport or GitHubTransport(config['gh_path'], profile='personal')
    publisher = Publisher(config['gh_path'], transport=transport, profile='personal')
    publisher._verify()
    check_stop(config)
    rows = transport._api(f'repos/{REPOSITORY}/contents/{PREFIX}?ref={BRANCH}', missing_ok=True)
    if rows is None:
        return {'state': 'unavailable', 'directory_complete': False, 'directory_unavailable_gap': True,
                'body_reads': 0, 'valid_days': len(valid_days(state)['days'])}
    if not isinstance(rows, list) or len(rows) > 1000: raise ValueError('directory_schema')
    spent = len(json.dumps(rows, ensure_ascii=True).encode())
    if spent > MAX_BYTES: raise ValueError('pass_byte_limit')
    summary = {'state': 'discovered', 'directory_complete': len(rows) < 1000, 'directory_limit_gap': len(rows) == 1000,
               'body_reads': 0, 'validated': 0, 'invalid': 0, 'unavailable': 0, 'ignored_metadata': 0, 'pending': 0}
    metadata = {}
    for item in rows:
        try:
            day = _listing_row(item)
            if day in metadata: raise ValueError('duplicate_day')
            metadata[day] = item
        except (ValueError, TypeError, OverflowError): summary['ignored_metadata'] += 1
    if summary['directory_complete']:
        for row in state['days'].values(): row['present'] = False
    for day, item in metadata.items():
        row = state['days'].setdefault(day, {'sha': None, 'fingerprint': None, 'status': 'pending', 'attempts': 0, 'turn': 0, 'present': True})
        row['present'] = True
        if row['sha'] != item['sha']:
            row.update(sha=item['sha'], status='pending', attempts=0)
    pending = [(row['turn'], day) for day, row in state['days'].items()
               if day in metadata and row['status'] in {'pending', 'unavailable'} and row['attempts'] < 3]
    for _, day in sorted(pending)[:20]:
        check_stop(config)
        row = state['days'][day]; item = metadata[day]
        state['turn'] += 1; row['turn'] = state['turn']; summary['body_reads'] += 1
        try:
            response = transport._api(f'repos/{REPOSITORY}/contents/{PREFIX}/{day}.json?ref={BRANCH}', missing_ok=True)
        except Exception:
            row['attempts'] += 1; row['status'] = 'unavailable'; summary['unavailable'] += 1; continue
        if response is None:
            row['attempts'] += 1; row['status'] = 'unavailable'; summary['unavailable'] += 1; continue
        try:
            spent += len(json.dumps(response, ensure_ascii=True).encode())
            if spent > MAX_BYTES:
                summary['pass_byte_limit_gap'] = True
                row['status'] = 'invalid'; summary['invalid'] += 1; break
            fingerprint = _body(response, item, day)
        except Exception:
            row['status'] = 'invalid'; summary['invalid'] += 1; continue
        row.update(status='validated', fingerprint=fingerprint, attempts=0)
        summary['validated'] += 1
    summary['pending'] = sum(r['present'] and r['status'] in {'pending', 'unavailable'} and r['attempts'] < 3 for r in state['days'].values())
    summary['unavailable_gaps'] = sum(r['present'] and r['status'] == 'unavailable' and r['attempts'] >= 3 for r in state['days'].values())
    summary['valid_days'] = len(valid_days(state)['days'])
    summary['bytes_observed'] = spent
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--once', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, 'personal'); check_stop(config)
        directory = Path(config['data_dir'])/'narrative-history'
        if directory.is_symlink(): raise ValueError('private_binding')
        directory.mkdir(mode=0o700, exist_ok=True)
        lock_dir = directory/'work-inventory-lock'
        if lock_dir.is_symlink(): raise ValueError('private_binding')
        with worker_lock(lock_dir):
            path = directory/'work-inventory.json'; output = directory/'validated-work-days.json'
            if any(p.is_symlink() or p.with_name(p.name+'.tmp').is_symlink() for p in (path, output)): raise ValueError('private_binding')
            with path.open(encoding='utf-8-sig') if path.exists() else nullcontext() as stream:
                state = json.load(stream) if stream else fresh_state()
            if not isinstance(state, dict) or set(state) != set(fresh_state()) or state['version'] != 1 or not isinstance(state['days'], dict): raise ValueError('state_schema')
            summary = discover(config, state)
            check_stop(config)
            atomic_json(path, state)
            atomic_json(output, valid_days(state))
            print(json.dumps(summary, allow_nan=False)); return 0
    except Exception as error:
        if str(error) == 'stopped': print(json.dumps({'state': 'stopped'})); return 0
        print(json.dumps({'state': 'error', 'error_code': 'work_history_discovery_failed'})); return 1


if __name__ == '__main__': raise SystemExit(main())

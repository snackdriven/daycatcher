"""Private historical journal day planning; no model calls or remote writes.

--config ABS --plan discovers/resumes bounded evidence inventory and returns at
most two older days. --ack PROOF_JSON records explicit prepared/reviewed/
published/unchanged/gap outcomes bound to the exact planned fingerprint.
"""
import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from export_narrative_evidence import load_config
from journal_collector.cli import atomic_json
from journal_collector.narrative_evidence import eligible_message, _instant, _revision, _hash, day_window
from journal_collector.runtime import worker_lock

MAX_BYTES = 16 * 1024 * 1024
HASH = re.compile(r'[0-9a-f]{64}\Z')
OUTCOMES = {'prepared', 'reviewed', 'published', 'unchanged', 'gap'}
GAPS = {'manual_deletion', 'manual_edit', 'unsupported_source', 'incomplete_export',
        'unavailable_source', 'oversize', 'capacity', 'review_required'}


def fresh_state():
    return {'version': 1, 'turn': 0, 'days': {}, 'scan': None, 'work_days': {}}


def work_inventory(payload):
    if not isinstance(payload, dict) or set(payload) != {'version', 'days'} or type(payload['version']) is not int or payload['version'] != 1:
        raise ValueError('work_inventory_schema')
    if not isinstance(payload['days'], list) or len(payload['days']) > 10000: raise ValueError('work_inventory_bound')
    days = {}
    for row in payload['days']:
        if not isinstance(row, dict) or set(row) != {'day', 'fingerprint'}: raise ValueError('work_inventory_row')
        day_window(row['day'])
        if row['day'] in days or not isinstance(row['fingerprint'], str) or not HASH.fullmatch(row['fingerprint']): raise ValueError('work_inventory_row')
        days[row['day']] = row['fingerprint']
    return days


def stopped(config):
    data = Path(config['data_dir'])
    return any(p.exists() for p in (data/'narrative-STOP', data/'history'/'STOP', data/'narrative-history'/'STOP'))


def _connect(path):
    db = sqlite3.connect(Path(path).resolve(strict=True).as_uri()+'?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
    return db


def _token(db, excluded):
    # Store revisions are monotonic. Include removals, association changes and
    # exclusions so a resumed scan cannot combine old and new source snapshots.
    fields = [tuple(db.execute('SELECT count(*),coalesce(sum(revision),0),max(observed_at) FROM messages').fetchone()),
              tuple(db.execute('SELECT count(*),max(excluded_at) FROM exclusions').fetchone()),
              tuple(db.execute('SELECT count(*) FROM associations').fetchone()), sorted(excluded)]
    return _hash(fields)


def scan_source(config, state, *, max_bytes=MAX_BYTES):
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES: raise ValueError('scan_limit')
    if stopped(config): raise ValueError('stopped')
    excluded = config.get('narrative_excluded_conversations', [])
    if not isinstance(excluded, list) or any(not isinstance(x, str) for x in excluded): raise ValueError('exclusions')
    excluded = set(excluded)
    db = _connect(config['source_db'])
    try:
        source_token = _token(db, excluded)
        scope = _hash([sorted(excluded), state['work_days']])
        scan = state.get('scan')
        if scan is None or scan.get('scope') != scope:
            # Freeze discovery membership, not source bodies. A global live
            # source token must never reset this durable bounded frontier.
            # New rows wait for the next scan; updates/exclusions are checked
            # when each row is visited and again by normal preparation.
            highwater = db.execute('SELECT coalesce(max(rowid),0) FROM messages').fetchone()[0]
            scan = {'scope': scope, 'source_token': source_token, 'source_changed': False,
                    'highwater': highwater, 'after': '', 'days': {}, 'oversize': 0, 'rejected': 0}
            state['scan'] = scan
        scan['source_changed'] = scan['source_changed'] or scan['source_token'] != source_token
        spent = 0; complete = True
        # Reject a single oversized source without reading its text into Python.
        columns = 'message_key,source,native_id,conversation_id,role,source_time,observed_at,complete,revision,provenance'
        rows = db.execute(f'''SELECT {columns},length(CAST(text AS BLOB)) AS text_bytes,
            CASE WHEN length(CAST(text AS BLOB))<=? THEN text ELSE NULL END AS text
            FROM messages WHERE rowid<=? AND message_key>? ORDER BY message_key''', (MAX_BYTES, scan['highwater'], scan['after']))
        for row in rows:
            if stopped(config): raise ValueError('stopped')
            size = (row['text_bytes'] or 0) + len(row['provenance'].encode()) + 512
            # Always move past one oversized record, visibly omitting it.
            if size > MAX_BYTES:
                scan['oversize'] += 1; scan['after'] = row['message_key']; continue
            if spent + size > max_bytes:
                complete = False; break
            spent += size; scan['after'] = row['message_key']
            if (db.execute('SELECT 1 FROM exclusions WHERE message_key=?', (row['message_key'],)).fetchone()
                    or row['conversation_id'] in excluded
                    or any(a[0] in excluded for a in db.execute('SELECT conversation_id FROM associations WHERE message_key=?', (row['message_key'],)))):
                continue
            if not eligible_message(row): scan['rejected'] += 1; continue
            instant = _instant(row['source_time']).astimezone(ZoneInfo('America/Chicago'))
            day = (instant.date() - (timedelta(days=1) if instant.hour < 3 else timedelta())).isoformat()
            group = scan['days'].setdefault(day, {'digest': _hash([]), 'count': 0})
            group['digest'] = _hash([group['digest'], _revision(row)]); group['count'] += 1
        if complete:
            groups = scan['days']
            for day in set(state['days']) | set(groups) | set(state['work_days']):
                group = groups.get(day, {'digest': _hash([]), 'count': 0})
                fingerprint = _hash({'day': day, 'personal': group['digest'], 'count': group['count'], 'work': state['work_days'].get(day)})
                prior = state['days'].get(day)
                if prior is None or prior['fingerprint'] != fingerprint:
                    blocked = prior and prior.get('reason') in {'manual_deletion', 'manual_edit'}
                    state['days'][day] = {'fingerprint': fingerprint, 'count': group['count'], 'work_available': day in state['work_days'],
                        'state': 'gap' if blocked else 'pending', 'turn': prior['turn'] if prior else 0,
                        'reason': prior['reason'] if blocked else None, 'input_digest': None}
            # A fresh scan next pass notices late additions and exclusions.
            state['scan'] = None
        return {'scan_complete': complete, 'bytes_scanned': spent, 'oversize_omitted': scan['oversize'],
                'rejected': scan['rejected'], 'source_changed_during_scan': scan['source_changed']}
    finally: db.close()


def select_days(state, current_day):
    day_window(current_day)
    previous = (date.fromisoformat(current_day)-timedelta(days=1)).isoformat()
    eligible = [(row['turn'], day, row) for day, row in state['days'].items()
                if day < previous and row['state'] in {'pending', 'prepared', 'reviewed'}]
    return [{'day': day, 'fingerprint': row['fingerprint'], 'state': row['state'], 'eligible_count': row['count'], 'work_available': row['work_available']}
            for _, day, row in sorted(eligible)[:2]]


def _remote_proof(day, proof):
    remote = proof.get('remote')
    path = f'entries/{day[:4]}/{day[5:7]}/{day}.md'
    if not isinstance(remote, dict) or remote.get('path') != path: raise ValueError('remote_proof')
    if remote.get('state') == 'existing':
        if not re.fullmatch(r'[0-9a-f]{40,64}', remote.get('blob_sha', '')): raise ValueError('remote_proof')
    elif remote.get('state') == 'absent':
        if remote.get('path_history_checked') is not True or type(remote.get('path_history_count')) is not int or remote['path_history_count'] != 0:
            raise ValueError('deletion_review_required')
    else: raise ValueError('remote_proof')


def _prepared_input(config, day, digest):
    path = Path(config['data_dir'])/'narrative-writer'/'inputs'/(day+'.json')
    if any(p.is_symlink() for p in (path, path.parent, path.parent.parent)) or path.stat().st_size > MAX_BYTES:
        raise ValueError('preparation_binding')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest: raise ValueError('preparation_digest')
    bundle = json.loads(raw)
    if not isinstance(bundle, dict) or bundle.get('version') != 1 or bundle.get('day') != day:
        raise ValueError('preparation_schema')
    return bundle


def acknowledge(config, state, proof):
    if stopped(config): raise ValueError('stopped')
    if not isinstance(proof, dict) or type(proof.get('version')) is not int or proof['version'] != 1: raise ValueError('proof_schema')
    day = proof.get('day'); day_window(day)
    row = state['days'].get(day)
    outcome = proof.get('outcome'); result = proof.get('result')
    if (row is None or proof.get('fingerprint') != row['fingerprint'] or outcome not in OUTCOMES
            or not isinstance(result, dict) or result.get('state') != outcome): raise ValueError('proof_binding')
    if row.get('reason') in {'manual_deletion', 'manual_edit'}: raise ValueError('manual_review_required')
    if outcome == 'gap':
        if proof.get('reason') not in GAPS: raise ValueError('gap_reason')
    else:
        digest = proof.get('input_digest')
        if not isinstance(digest, str) or not HASH.fullmatch(digest): raise ValueError('input_proof')
        bundle = _prepared_input(config, day, digest)
        if outcome == 'prepared':
            if row['state'] not in {'pending', 'prepared'}: raise ValueError('phase_order')
        elif proof.get('reviewed') is not True or row['input_digest'] != digest or row['state'] not in {'prepared', 'reviewed'}:
            raise ValueError('review_proof')
        if outcome in {'published', 'unchanged'}:
            if row['state'] != 'reviewed': raise ValueError('review_required')
            if bundle.get('nextOffset') is not None or bundle.get('coverage', {}).get('omitted') != 0:
                raise ValueError('incomplete_preparation')
            if row['work_available'] and bundle.get('work_state') not in {'ready', 'empty'}:
                raise ValueError('work_unavailable')
            _remote_proof(day, proof)
        if outcome == 'unchanged' and proof.get('reason') not in {'no_meaningful_new_material', 'already_represented'}:
            raise ValueError('unchanged_reason')
        if outcome == 'unchanged' and bundle.get('existing_sha') != proof['remote'].get('blob_sha'):
            raise ValueError('remote_changed')
        if outcome == 'published':
            candidate = proof.get('candidate_digest')
            if not isinstance(candidate, str) or not HASH.fullmatch(candidate) or candidate != row.get('candidate_digest'):
                raise ValueError('candidate_review')
            receipt_path = Path(config['data_dir'])/'narrative-writer'/'receipts.sqlite3'
            if receipt_path.is_symlink(): raise ValueError('receipt_binding')
            db = _connect(receipt_path)
            try:
                saved = db.execute('SELECT * FROM narrative_receipts WHERE day=?', (day,)).fetchone()
                receipt = json.loads(saved['receipt_json']) if saved and saved['receipt_json'] else None
                if (saved is None or saved['state'] != 'delivered' or saved['input_digest'] != candidate
                        or not isinstance(receipt, dict) or result.get('input_digest') != candidate
                        # The receipt's whole-file SHA is historical. Unowned
                        # prose/legacy cleanup can change it without changing
                        # this delivered owned span. Bind the current exact
                        # reconciled result to current remote metadata instead.
                        or result.get('blob_sha') != proof['remote'].get('blob_sha')
                        or result.get('body_hash') != saved['body_hash']
                        or hashlib.sha256(saved['body']).hexdigest() != saved['body_hash']):
                    raise ValueError('publication_receipt')
            finally: db.close()
    if outcome == 'reviewed' and proof.get('candidate_digest') is not None:
        if not isinstance(proof['candidate_digest'], str) or not HASH.fullmatch(proof['candidate_digest']): raise ValueError('candidate_digest')
    state['turn'] += 1
    row.update(state=outcome, turn=state['turn'], reason=proof.get('reason'), input_digest=proof.get('input_digest'))
    row['proof_digest'] = _hash(proof)
    if outcome == 'reviewed' and proof.get('candidate_digest') is not None:
        row['candidate_digest'] = proof['candidate_digest']


def _load(path):
    if path.is_symlink() or path.stat().st_size > MAX_BYTES: raise ValueError('private_input')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _load_owned_queue(path):
    if path.is_symlink(): raise ValueError('history_binding')
    # This cumulative owned metadata is not an external proof/input envelope.
    with path.open('r', encoding='utf-8-sig') as stream: return json.load(stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true'); modes.add_argument('--ack', type=Path)
    parser.add_argument('--current-day')
    parser.add_argument('--work-days', type=Path, help='Validated controlled-handoff day/fingerprint metadata only')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, 'personal')
        if stopped(config): print(json.dumps({'state': 'stopped'})); return 0
        directory = Path(config['data_dir'])/'narrative-history'
        if directory.is_symlink(): raise ValueError('history_binding')
        directory.mkdir(mode=0o700, exist_ok=True)
        with worker_lock(directory):
            path = directory/'queue.json'
            state = _load_owned_queue(path) if path.exists() else fresh_state()
            if not isinstance(state, dict) or set(state) != set(fresh_state()) or state['version'] != 1 or not isinstance(state['days'], dict):
                raise ValueError('queue_schema')
            if args.work_days:
                if (Path(config['data_dir'])/'work-handoff-STOP').exists(): raise ValueError('work_paused')
                state['work_days'] = work_inventory(_load(args.work_days))
            scanned = scan_source(config, state)
            if args.ack:
                if not scanned['scan_complete']:
                    report = {'state': 'inventory_pending', **scanned}
                else:
                    acknowledge(config, state, _load(args.ack))
                    report = {'state': 'acknowledged'}
            else:
                now = datetime.now(ZoneInfo('America/Chicago'))
                current = args.current_day or (now.date()-(timedelta(days=1) if now.hour < 3 else timedelta())).isoformat()
                report = {'state': 'planned', 'days': select_days(state, current) if scanned['scan_complete'] else [], **scanned}
            if stopped(config): print(json.dumps({'state': 'stopped'})); return 0
            if path.is_symlink() or path.with_name('queue.json.tmp').is_symlink(): raise ValueError('history_binding')
            atomic_json(path, state)
            report['counts'] = {name: sum(r['state'] == name for r in state['days'].values()) for name in ['pending', *sorted(OUTCOMES)]}
            print(json.dumps(report, allow_nan=False)); return 0
    except Exception as error:
        if str(error) == 'stopped':
            print(json.dumps({'state': 'stopped'})); return 0
        print(json.dumps({'state': 'error', 'error_code': 'narrative_history_failed'})); return 1


if __name__ == '__main__': raise SystemExit(main())

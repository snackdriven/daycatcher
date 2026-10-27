"""Plan bounded native ChatGPT reads from metadata; no network or transcript access.

Plan with --list EXACT_NATIVE_JSON. Acknowledge a completed ingest separately with
--ack ID --observed-at AWARE_ISO --status imported|incomplete|error. Only imported
advances the successful-read watermark. This command cannot verify ingestion;
the caller must acknowledge only after its import attempt has finished.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

from journal_collector.chatgpt_native import _id, _iso
from journal_collector.cli import atomic_json
from journal_collector.runtime import worker_lock


SYNTHETIC_ID = '00000000-0000-4000-8000-000000000001'
STATE_NAME = 'native-chatgpt-schedule.json'
MAX_BYTES = 2 * 1024 * 1024
STATUSES = ('imported', 'incomplete', 'error')


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('input_bound')
    return json.loads(raw.decode('utf-8-sig'))


def config_directory(path):
    path = Path(path).resolve()
    config = read_json(path)
    if not isinstance(config, dict):
        raise ValueError('config')
    if not all(isinstance(config.get(k), str) and Path(config[k]).is_absolute()
               for k in ('data_dir', 'db')):
        raise ValueError('paths')
    data, db = (Path(config[k]).resolve() for k in ('data_dir', 'db'))
    if path != data / 'collector.json' or db != data / 'collector.sqlite3':
        raise ValueError('binding')
    if 'source_db' in config and Path(config['source_db']).resolve() != db:
        raise ValueError('source_binding')
    return data


def load_state(path):
    if not path.exists():
        return {'schemaVersion': 1, 'threads': {}}
    state = read_json(path)
    if (not isinstance(state, dict) or set(state) != {'schemaVersion', 'threads'}
            or type(state['schemaVersion']) is not int or state['schemaVersion'] != 1
            or not isinstance(state['threads'], dict) or len(state['threads']) > 10000):
        raise ValueError('state_schema')
    for ident, row in state['threads'].items():
        _id(ident)
        if ident == SYNTHETIC_ID or not isinstance(row, dict):
            raise ValueError('state_identifier')
        if (set(row) != {'last_checked_at', 'last_successful_at', 'status'}
                or row['status'] not in STATUSES):
            raise ValueError('state_metadata')
        checked = _iso(row['last_checked_at'])
        if row['last_successful_at'] is not None and _iso(row['last_successful_at']) > checked:
            raise ValueError('state_time')
    return state


def make_plan(payload, state, now, limit):
    if (not isinstance(payload, dict) or type(payload.get('schemaVersion')) is not int
            or payload['schemaVersion'] != 4
            or not all(isinstance(payload.get(k), list) for k in ('threads', 'pinnedThreads'))):
        raise ValueError('list_schema')
    if len(payload['threads']) > 50 or len(payload['pinnedThreads']) > 10000:
        raise ValueError('list_bound')
    candidates = {}
    for row in payload['pinnedThreads'] + payload['threads']:
        if not isinstance(row, dict):
            raise ValueError('list_row')
        if row.get('kind') != 'chatgpt':
            continue
        ident, stamp = _id(row.get('id')), row.get('updatedAt')
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            raise ValueError('list_timestamp')
        updated = datetime.fromtimestamp(stamp, timezone.utc)
        if ident == SYNTHETIC_ID or updated < now - timedelta(days=7):
            continue
        candidates[ident] = max(updated, candidates.get(ident, updated))

    def priority(ident):
        previous = state['threads'].get(ident)
        return (previous is not None,
                _iso(previous['last_checked_at']) if previous else datetime.min.replace(tzinfo=timezone.utc),
                ident)

    plan = []
    for ident in sorted(candidates, key=priority)[:limit]:
        previous = state['threads'].get(ident, {})
        successful = previous.get('last_successful_at')
        since = _iso(successful) - timedelta(hours=1) if successful else now - timedelta(hours=48)
        # Clamp clock-skewed success metadata as well as historical backfill.
        since = max(now - timedelta(days=14), min(since, now - timedelta(hours=1)))
        plan.append({'thread_id': ident, 'since': since.isoformat(), 'until': now.isoformat()})
    return {'state': 'planned', 'plan': plan,
            'coverage': {'status': 'partial', 'account_complete': False,
                         'list_scope': 'returned_metadata_only', 'list_limit': 50,
                         'turn_limit': 10, 'has_more': 'unverified',
                         'read_scope': 'latest_10_turns_only',
                         'detail': 'Native list and each read may omit history; inspect read page hasMore.'}}


def acknowledge(state, ident, observed_at, status):
    ident = _id(ident)
    if ident == SYNTHETIC_ID:
        raise ValueError('synthetic_excluded')
    observed = _iso(observed_at)
    previous = state['threads'].get(ident, {})
    if previous and observed < _iso(previous['last_checked_at']):
        raise ValueError('out_of_order_ack')
    state['threads'][ident] = {
        'last_checked_at': observed.isoformat(),
        'last_successful_at': observed.isoformat() if status == 'imported'
                              else previous.get('last_successful_at'),
        'status': status,
    }
    if len(state['threads']) > 10000:
        raise ValueError('state_bound')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list', dest='listing')
    mode.add_argument('--ack')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--observed-at')
    parser.add_argument('--status', choices=STATUSES)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.limit <= 5:
            raise ValueError('limit')
        if bool(args.ack) != (args.observed_at is not None and args.status is not None):
            raise ValueError('ack_arguments')
        if args.listing and (args.observed_at is not None or args.status is not None):
            raise ValueError('plan_arguments')
        data = config_directory(args.config)
        if (data / 'native-chatgpt-STOP').exists():
            print(json.dumps({'state': 'stopped'}))
            return 0
        # Separate lock namespace: never acquires or stops the ingestion worker.
        with worker_lock(data / 'native-chatgpt-plan-lock'):
            if (data / 'native-chatgpt-STOP').exists():
                print(json.dumps({'state': 'stopped'}))
                return 0
            state = load_state(data / STATE_NAME)
            if args.ack:
                acknowledge(state, args.ack, args.observed_at, args.status)
                if (data / 'native-chatgpt-STOP').exists():
                    print(json.dumps({'state': 'stopped'}))
                    return 0
                atomic_json(data / STATE_NAME, state)
                result = {'state': 'acknowledged', 'thread_id': args.ack, 'status': args.status}
            else:
                result = make_plan(read_json(args.listing), state, datetime.now(timezone.utc), args.limit)
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({'state': 'error', 'error_code': 'native_chatgpt_plan_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

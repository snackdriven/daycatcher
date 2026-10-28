"""Private, bounded native history frontier. Inputs are exact decoded tool JSON.

--active FILE / --archive FILE [--cursor CURSOR] capture metadata; --plan emits
up to five reads. --page FILE --thread ID [--cursor CURSOR] imports and advances
only after the canonical Store transaction succeeds. No external success ack.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math

from journal_collector.chatgpt_native import _id, _truncated, normalize_page
from journal_collector.cli import atomic_json
from journal_collector.runtime import worker_lock
from journal_collector.store import Store
from native_chatgpt_plan import config_directory, read_json, SYNTHETIC_ID

STATE_NAME = 'native-chatgpt-history.json'


def load_owned_state(path):
    """The cumulative owned frontier is not a two-MiB external tool envelope.

    Discovery/page inputs remain bounded by read_json. This metadata-only state
    grows with actual accessible history and must remain reloadable after writes.
    """
    if path.is_symlink(): raise ValueError('state_binding')
    with path.open('r', encoding='utf-8-sig') as stream:
        return json.load(stream)


def new_state():
    return {'schemaVersion': 1, 'threads': {}, 'archive_cursor': None,
            'archive_end_proven': False, 'active_end_proven': False, 'sequence': 0}


def discover(state, payload, source, cursor=None):
    if not isinstance(payload, dict) or _truncated({k: v for k, v in payload.items() if k not in ('threads', 'pinnedThreads')}):
        raise ValueError('metadata_schema')
    if source == 'active':
        if type(payload.get('schemaVersion')) is not int or payload['schemaVersion'] != 4:
            raise ValueError('active_schema')
        if not isinstance(payload.get('pinnedThreads'), list): raise ValueError('active_schema')
        pinned = payload['pinnedThreads']
    elif source == 'archive':
        if set(payload) != {'threads', 'nextCursor'} or cursor != state['archive_cursor'] or state['archive_end_proven']:
            raise ValueError('archive_binding')
        nxt = payload['nextCursor']
        if nxt is not None and (not isinstance(nxt, str) or not nxt or len(nxt) > 4096 or nxt == cursor):
            raise ValueError('archive_cursor')
        pinned = []
    else: raise ValueError('metadata_source')
    if not isinstance(payload.get('threads'), list) or len(payload['threads']) + len(pinned) > 10000:
        raise ValueError('metadata_bound')
    ids = []
    for row in pinned + payload['threads']:
        if not isinstance(row, dict): raise ValueError('metadata_row')
        if row.get('kind') != 'chatgpt':
            if source == 'archive': raise ValueError('archive_source')
            continue
        # Native schema4 explicitly truncates presentation titles. Neither title
        # nor summary contributes identity, timestamps, pagination or evidence.
        if _truncated({k: v for k, v in row.items() if k not in ('title', 'summary', 'titleTruncated', 'summaryTruncated')}):
            raise ValueError('metadata_truncated')
        ident = _id(row.get('id')); stamp = row.get('updatedAt')
        if type(stamp) not in (int, float) or not math.isfinite(stamp): raise ValueError('metadata_time')
        datetime.fromtimestamp(stamp, timezone.utc)
        if ident != SYNTHETIC_ID: ids.append(ident)
    for ident in ids:
        state['threads'].setdefault(ident, {'cursor': None, 'status': 'pending', 'attempts': 0, 'pages': 0, 'order': 0})
    if source == 'archive':
        state['archive_cursor'] = nxt
        state['archive_end_proven'] = nxt is None


def plan(state, limit=5):
    return [{'threadId': ident, 'cursor': row['cursor'], 'turnLimit': 10,
             'includeOutputs': False, 'maxOutputCharsPerItem': 20000}
            for ident, row in sorted(state['threads'].items(), key=lambda item: (item[1]['order'], item[0]))
            if row['status'] == 'pending'][:max(0, min(limit, 5))]


def import_page(state, ident, cursor, payload, ingest, persist=lambda: None):
    row = state['threads'].get(ident)
    if row is None or row['status'] != 'pending' or row['cursor'] != cursor: raise ValueError('page_binding')
    state['sequence'] += 1; row['order'] = state['sequence']
    try:
        captured = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if row.get('capture', {}).get('digest') != captured:
            row['capture'] = {'digest': captured, 'observed_at': datetime.now(timezone.utc).isoformat()}
        # Persist observation before Store: a crash after its commit must replay
        # the identical batch instead of minting a different timestamp/payload.
        persist()
        # Broad explicit window: no recent-horizon clamp or observation-time bound.
        batch = normalize_page(payload, expected_thread_id=ident,
                               since='0001-01-01T00:00:00+00:00', until='9999-12-31T23:59:59.999999+00:00',
                               now=row['capture']['observed_at'])
        if batch['coverage']['incomplete'] or batch['coverage']['outside_window'] or _truncated(payload):
            raise ValueError('unsupported_page')
        nxt = batch['cursor']['next_cursor']
        if nxt is not None and (nxt == cursor or hashlib.sha256(nxt.encode()).hexdigest() in row.get('seen_cursors', [])):
            raise ValueError('cursor_stalled')
        batch['cursor_key'] = 'history:' + batch['cursor_key']
        # Stable across retries; observation time is not part of receipt identity.
        digest = hashlib.sha256(json.dumps(batch, sort_keys=True, allow_nan=False).encode()).hexdigest()
        batch['batch_id'] = 'chatgpt-native-history:' + digest
        result = ingest(batch, preserve_existing_complete=True)
        if not isinstance(result, dict) or result.get('batch_id') != batch['batch_id']:
            raise ValueError('import_receipt')
        row['pages'] += 1; row['attempts'] = 0
        if cursor is not None: row.setdefault('seen_cursors', []).append(hashlib.sha256(cursor.encode()).hexdigest())
        stamps = [m['source_time'] for m in batch['messages']]
        if stamps:
            row['oldest_verified_source_time'] = min(stamps + ([row['oldest_verified_source_time']] if row.get('oldest_verified_source_time') else []))
        row['cursor'] = nxt
        row['status'] = 'pending' if nxt is not None else 'exhausted'
        row.pop('capture', None)
        return {'state': 'imported', 'counts': {k: v for k, v in result.items() if type(v) is int}}
    except Exception:
        row['attempts'] += 1
        if row['attempts'] >= 3: row['status'] = 'gap'
        return {'state': row['status'], 'error_code': 'history_page_unresolved'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ('active', 'archive', 'page'): modes.add_argument('--' + name)
    modes.add_argument('--plan', action='store_true')
    parser.add_argument('--thread'); parser.add_argument('--cursor')
    args = parser.parse_args(argv)
    try:
        data = config_directory(args.config)
        with worker_lock(data / 'native-chatgpt-history-lock'):
            if any(p.exists() for p in (data / 'native-chatgpt-STOP', data / 'native-chatgpt-history-STOP', data / 'history' / 'STOP')):
                print(json.dumps({'state': 'stopped'})); return 0
            path = data / STATE_NAME
            state = load_owned_state(path) if path.exists() else new_state()
            if not isinstance(state, dict) or set(state) != set(new_state()) or state['schemaVersion'] != 1 or not isinstance(state['threads'], dict):
                raise ValueError('state_schema')
            if args.plan:
                report = {'reads': plan(state), 'archive_cursor': state['archive_cursor'],
                          'archive_end_proven': state['archive_end_proven'], 'active_end_proven': False,
                          'discovered': len(state['threads']), 'pages_imported': sum(r['pages'] for r in state['threads'].values()),
                          'exhausted': sum(r['status'] == 'exhausted' for r in state['threads'].values()),
                          'gaps': sum(r['status'] == 'gap' for r in state['threads'].values()),
                          'oldest_verified_source_time': min((r['oldest_verified_source_time'] for r in state['threads'].values() if r.get('oldest_verified_source_time')), default=None)}
            elif args.page:
                if not args.thread: raise ValueError('thread_required')
                with Store(data / 'collector.sqlite3') as store:
                    report = import_page(state, args.thread, args.cursor, read_json(args.page), store.ingest_batch,
                                         lambda: atomic_json(path, state))
            else:
                discover(state, read_json(args.active or args.archive), 'active' if args.active else 'archive', args.cursor)
                report = {'state': 'discovered', 'discovered': len(state['threads']), 'active_end_proven': False,
                          'archive_end_proven': state['archive_end_proven']}
            atomic_json(path, state)
            print(json.dumps(report, allow_nan=False)); return 0
    except Exception:
        print(json.dumps({'state': 'error', 'error_code': 'native_history_failed'})); return 1


if __name__ == '__main__': raise SystemExit(main())

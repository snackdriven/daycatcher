"""Pull bounded, fixed-topic Dot summaries into the local personal collector.

Only imports/dot/v1/YYYY-MM-DD.json is read. This command never publishes a
journal entry, reads a Dot transcript, or creates new credentials.
"""
import argparse
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from personal_summary_service import configure_timezone, load_config
from journal_collector.cli import atomic_json
from journal_collector.dot_handoff import HandoffError, normalize_handoff, utc_time
from journal_collector.runtime import worker_lock
from journal_collector.store import Store
from journal_collector.summary_publish import Publisher, PublishError
from journal_collector.work_summary import journal_day


def _prior_days(source_db, days):
    """Inspect canonical evidence without creating or modifying a dry-run DB."""
    path=Path(source_db).resolve()
    if not path.exists():
        return {}
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        result={}
        for day in days:
            rows=connection.execute("SELECT * FROM messages WHERE source='dot-summary' AND native_id LIKE ?",
                                    (day+'-%',)).fetchall()
            cursor_row=connection.execute("SELECT cursor FROM cursors WHERE source='dot-summary' AND cursor_key=?",
                                          ('day:'+day,)).fetchone()
            cursor=json.loads(cursor_row['cursor']) if cursor_row and cursor_row['cursor'] else {}
            if rows or cursor:
                excluded=set()
                for fid in cursor.get('fragment_ids',[]):
                    native_id=day+'-'+fid
                    key=hashlib.sha256(json.dumps(['dot-summary',native_id],ensure_ascii=False,
                        sort_keys=True,separators=(',',':')).encode()).hexdigest()
                    if connection.execute('SELECT 1 FROM exclusions WHERE message_key=?',(key,)).fetchone():
                        excluded.add(native_id)
                result[day]={'rows':rows,'cursor':cursor,'excluded':excluded}
        return result
    finally:
        connection.close()


def _conflicting_days(staged, prior):
    conflicts=set()
    for day, previous in prior.items():
        incoming=staged[day]
        cursor=previous['cursor']
        if cursor.get('quarantined') is True:
            conflicts.add(day)
            continue
        if incoming['error']:
            if incoming['error'] in {'invalid_handoff','remote_data'}:
                conflicts.add(day)
            continue
        batch=incoming['batch']
        new={item['native_id']:item for item in batch['messages']} if batch else {}
        required={day+'-'+fid for fid in cursor.get('fragment_ids',[])}-previous['excluded']
        required.update(row['native_id'] for row in previous['rows'])
        if not required <= new.keys():
            conflicts.add(day)
            continue
        for row in previous['rows']:
            item=new[row['native_id']]
            if (any(row[key]!=item[key] for key in ('conversation_id','role','text','source_time'))
                    or bool(row['complete']) != item['complete']
                    or json.loads(row['provenance']) != item['provenance']):
                conflicts.add(day)
                break
    return conflicts


def _quarantine(store, prior, days, observed_at):
    """Retire contradicted evidence durably, without restoring user exclusions."""
    conn=store.connection
    conn.execute('BEGIN IMMEDIATE')
    try:
        for day in sorted(days):
            previous=prior[day]
            for row in previous['rows']:
                provenance=json.loads(row['provenance'])
                if not row['complete'] and provenance.get('unsupported_content') is True:
                    continue
                provenance['unsupported_content']=True
                encoded=json.dumps(provenance,sort_keys=True,separators=(',',':'))
                revision=row['revision']+1
                conn.execute('UPDATE messages SET complete=0,revision=?,provenance=?,observed_at=? WHERE message_key=?',
                             (revision,encoded,observed_at,row['message_key']))
                conn.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?)',
                             (row['message_key'],revision,row['text'],row['role'],row['source_time'],observed_at,encoded))
                conn.execute('INSERT INTO jobs VALUES(?,?,?)',(row['message_key'],revision,observed_at))
            cursor=dict(previous['cursor'],day=day,quarantined=True)
            coverage={'status':'error','origin':'source-provided-summary','error_code':'handoff_conflict'}
            conn.execute('''INSERT INTO cursors VALUES(?,?,?,?,?) ON CONFLICT(source,cursor_key)
                DO UPDATE SET cursor=excluded.cursor,coverage=excluded.coverage,updated_at=excluded.updated_at''',
                ('dot-summary','day:'+day,json.dumps(cursor),json.dumps(coverage),observed_at))
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def pull(config, days=3, dry_run=False, publisher=None, now=None):
    """Read and validate every requested day before any Store ingestion.

    ``config`` is the canonical personal-summary configuration loaded by main.
    The optional Publisher and aware UTC time support synthetic, offline tests.
    Dry runs validate the remote files and write a metadata receipt only.
    """
    data = Path(config['data_dir'])
    status_path = data/'dot-summary-status.json'
    result = {'state': 'running', 'enabled': config.get('enabled') is True,
              'files': 0, 'missing': 0, 'fragments': 0,
              'new': 0, 'revised': 0, 'unchanged': 0}
    lock = None

    def stopped():
        return any((data/name).exists() for name in ('personal-summary-STOP', 'dot-summary-STOP'))

    try:
        timestamp = utc_time(now)
        result['checked_at'] = timestamp.isoformat()
        if (type(days) is not int or not 1 <= days <= 14 or type(dry_run) is not bool
                or type(config.get('enabled')) is not bool
                or Path(config['source_db']).resolve() != data.resolve()/'collector.sqlite3'
                or config.get('repository') != 'snackdriven/day-crumbs' or config.get('branch') != 'main'):
            raise ValueError()
        if stopped():
            result['state'] = 'stopped'
        elif not config['enabled'] and not dry_run:
            result['state'] = 'disabled'
        else:
            data.mkdir(parents=True, exist_ok=True)
            lock = worker_lock(data/'.dot-summary-worker')
            try:
                lock.__enter__()
            except RuntimeError:
                lock = None
                result['state'] = 'busy'
            if lock is not None:
                pub = publisher if publisher is not None else Publisher(config['gh_path'],
                    repository=config['repository'], branch=config['branch'], profile='personal')
                if not isinstance(pub, Publisher) or pub.profile != 'personal':
                    raise ValueError()
                # Verify even if every requested file is absent. _read also
                # verifies immediately before each actual transport read.
                pub._verify()
                latest = date.fromisoformat(journal_day(timestamp.isoformat()))
                batches = []
                staged = {}
                for offset in range(days):
                    if stopped():
                        result['state'] = 'stopped'
                        break
                    day = (latest-timedelta(days=offset)).isoformat()
                    staged[day]={'batch':None,'error':None}
                    try:
                        remote = pub._read('imports/dot/v1/' + day + '.json')
                        batch = normalize_handoff(remote['content'], day, now=timestamp) if remote is not None else None
                    except HandoffError:
                        staged[day]['error']='invalid_handoff'
                        continue
                    except PublishError as error:
                        staged[day]['error']=error.code
                        continue
                    if remote is None:
                        result['missing'] += 1
                        continue
                    staged[day]['batch']=batch
                    batches.append(batch)
                    result['files'] += 1
                    result['fragments'] += len(batch['messages'])
                if result['state'] != 'stopped':
                    prior=_prior_days(config['source_db'],staged)
                    conflicts=_conflicting_days(staged,prior)
                    errors=[item['error'] for item in staged.values() if item['error']]
                    if stopped():
                        result['state'] = 'stopped'
                    elif conflicts:
                        if not dry_run:
                            with Store(config['source_db']) as store:
                                _quarantine(store,prior,conflicts,timestamp.isoformat())
                        result.update(state='error',error_code='handoff_conflict')
                    elif errors:
                        result.update(state='error',error_code=errors[0])
                    elif dry_run:
                        result['state'] = 'preview'
                    else:
                        # All remote payloads have passed strict validation.
                        if batches:
                            with Store(config['source_db']) as store:
                                for batch in batches:
                                    if stopped():
                                        result['state'] = 'stopped'
                                        break
                                    counts = store.ingest_batch(batch)
                                    for key in ('new', 'revised', 'unchanged'):
                                        result[key] += counts[key]
                        if result['state'] == 'running':
                            result['state'] = 'idle'
    except HandoffError:
        result.update(state='error', error_code='invalid_handoff')
    except PublishError as error:
        result.update(state='error', error_code=error.code)
    except Exception:
        result.update(state='error', error_code='dot_pull_failed')
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)
    atomic_json(status_path, result)
    return result


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True,
                        help='Canonical runtime personal-summary.json')
    parser.add_argument('--days', type=int, choices=range(1,15), default=3)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    config = None
    try:
        config = load_config(args.config.expanduser().resolve())
        configure_timezone(config)
        result = pull(config, days=args.days, dry_run=args.dry_run)
    except Exception:
        result = {'state': 'error', 'error_code': 'dot_configuration_or_timezone_failed'}
        if config is not None:
            atomic_json(Path(config['data_dir'])/'dot-summary-status.json', result)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 1 if result.get('state') == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())

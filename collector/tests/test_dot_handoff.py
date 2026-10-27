import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zoneinfo

from journal_collector.dot_handoff import HandoffError, normalize_handoff
from journal_collector.store import Store
from journal_collector.personal_summary import PersonalSummaryQueue
from journal_collector.summary_publish import Publisher
import pull_dot_handoff as cli


DAY = '2026-10-27'
NOW = datetime(2026, 10, 27, 18, tzinfo=timezone.utc)


def fragment(code='topic_home', fid='a' * 32, day=DAY):
    return dict(fragment_id=fid, day=day, facts=[code], coverage='partial')


def payload(fragments=None, day=DAY):
    return json.dumps(dict(version=1, day=day, fragments=[fragment()] if fragments is None else fragments)).encode()


class Transport:
    def __init__(self, files=None, private=True):
        self.files = files or {}
        self.private = private
        self.reads = []
        self.verifications = 0

    def repo(self):
        self.verifications += 1
        return dict(full_name='snackdriven/day-crumbs', private=self.private,
                    default_branch='main', permissions={'push': True})

    def read(self, path):
        self.reads.append(path)
        blob = self.files.get(path)
        return None if blob is None else dict(content=blob, sha='c' * 40)


def publisher(transport):
    return Publisher('unused', profile='personal', transport=transport)


class NormalizeTests(unittest.TestCase):
    def test_normalize_only_fixed_codes_and_honest_time(self):
        batch = normalize_handoff(payload(), DAY, now=NOW)
        self.assertEqual(batch['source'], 'dot-summary')
        self.assertEqual(batch['cursor_key'], 'day:' + DAY)
        self.assertEqual(batch['coverage']['status'], 'partial')
        self.assertEqual(batch['coverage']['origin'], 'source-provided-summary')
        row = batch['messages'][0]
        self.assertEqual(row['native_id'], DAY + '-' + 'a' * 32)
        self.assertEqual(row['conversation_id'], 'dot-main-summary')
        self.assertEqual(row['role'], 'assistant')
        self.assertTrue(row['complete'])
        self.assertIsNone(row['source_time'])
        self.assertEqual(row['observed_at'], NOW.isoformat())
        self.assertEqual(json.loads(row['text']), ['topic_home'])
        self.assertEqual(row['provenance'], dict(adapter='dot-fixed-handoff-v1',
            origin='source-summary', journal_day=DAY, fact_codes=['topic_home'], handoff_id='a' * 32))

    def test_reject_invalid_payloads_without_echoing_content(self):
        bad = [b'raw sensitive text', b'{"version":1,"version":1}', b'x' * 65537]
        base = json.loads(payload())
        mutations = [dict(base, version=True), dict(base, version=2), dict(base, raw='secret'),
                     dict(base, day='2026-02-30'), dict(base, fragments=[fragment(day='2026-10-26')]),
                     dict(base, fragments=[fragment(code='reaction_tired')]),
                     dict(base, fragments=[fragment(code='I cooked dinner')]),
                     dict(base, fragments=[dict(fragment(), source_time=NOW.isoformat())]),
                     dict(base, fragments=[dict(fragment(), complete=False)]),
                     dict(base, fragments=[dict(fragment(), coverage='complete')]),
                     dict(base, fragments=[dict(fragment(), facts=[])]),
                     dict(base, fragments=[dict(fragment(), facts=['topic_home', 'topic_home'])]),
                     dict(base, fragments=[fragment(fid='not-an-id')]),
                     dict(base, fragments=[fragment(), fragment(code='topic_learning')]),
                     dict(base, fragments=[fragment(), fragment(fid='b'*32)]),
                     dict(base, fragments=[fragment()] * 18)]
        bad.extend(json.dumps(item).encode() for item in mutations)
        for blob in bad:
            with self.subTest(blob_length=len(blob)):
                with self.assertRaises(HandoffError) as caught:
                    normalize_handoff(blob, DAY, now=NOW)
                self.assertEqual(str(caught.exception), 'invalid_handoff')
        for day in ['2026-10-26', '2026-2-01', '2026-02-30']:
            with self.assertRaises(HandoffError):
                normalize_handoff(payload(), day, now=NOW)

    def test_reject_naive_observed_time(self):
        with self.assertRaises(HandoffError):
            normalize_handoff(payload(), DAY, now=NOW.replace(tzinfo=None))

    def test_dedup_append_and_revision_have_stable_provenance(self):
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory)/'db') as store:
            first = normalize_handoff(payload(), DAY, now=NOW)
            self.assertEqual(store.ingest_batch(first)['new'], 1)
            again = normalize_handoff(payload(), DAY, now=NOW)
            self.assertEqual(store.ingest_batch(again)['unchanged'], 1)
            appended = normalize_handoff(payload([fragment(), fragment('topic_learning', 'b'*32)]), DAY, now=NOW)
            self.assertEqual(appended['messages'][0]['provenance'], first['messages'][0]['provenance'])
            self.assertEqual(store.ingest_batch(appended)['new'], 1)
            self.assertEqual(store.status()['counts']['revisions'], 2)
            changed = normalize_handoff(payload([fragment('topic_shopping')]), DAY, now=NOW)
            self.assertEqual(store.ingest_batch(changed)['revised'], 1)
            self.assertEqual(store.status()['counts']['revisions'], 3)
            other_day = '2026-10-26'
            other = normalize_handoff(payload([fragment(day=other_day)], day=other_day), other_day, now=NOW)
            self.assertEqual(store.ingest_batch(other)['new'], 1)


class PullTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.config = dict(data_dir=str(self.data), source_db=str(self.data/'collector.sqlite3'),
            enabled=True, gh_path='unused', repository='snackdriven/day-crumbs', branch='main')

    def test_pull_prevalidates_every_file_before_ingest(self):
        transport = Transport({'imports/dot/v1/'+DAY+'.json': payload(),
                               'imports/dot/v1/2026-10-26.json': b'private raw text'})
        result = cli.pull(self.config, days=3, publisher=publisher(transport), now=NOW)
        self.assertEqual(result['state'], 'error')
        self.assertFalse(Path(self.config['source_db']).exists())
        self.assertNotIn('private raw text', json.dumps(result))
        self.assertEqual(json.loads((self.data/'dot-summary-status.json').read_text()), result)

    def test_dry_run_checks_remote_but_does_not_create_source_db(self):
        self.config['enabled'] = False
        transport = Transport({'imports/dot/v1/'+DAY+'.json': payload()})
        result = cli.pull(self.config, days=3, dry_run=True, publisher=publisher(transport), now=NOW)
        self.assertEqual(result['state'], 'preview')
        self.assertEqual(result['fragments'], 1)
        self.assertEqual(transport.reads, ['imports/dot/v1/'+day+'.json' for day in [DAY,'2026-10-26','2026-10-25']])
        self.assertFalse(Path(self.config['source_db']).exists())

    def test_chicago_before_three_uses_previous_journal_day(self):
        transport = Transport()
        result = cli.pull(self.config, days=1, publisher=publisher(transport),
                          now=datetime(2026,10,27,7,59,tzinfo=timezone.utc))
        self.assertEqual(result['state'], 'idle')
        self.assertEqual(transport.reads, ['imports/dot/v1/2026-10-26.json'])

    def test_stop_and_disabled_gates_do_not_read(self):
        transport = Transport()
        pub = publisher(transport)
        self.config['enabled'] = False
        self.assertEqual(cli.pull(self.config, publisher=pub, now=NOW)['state'], 'disabled')
        self.config['enabled'] = True
        for name in ['personal-summary-STOP','dot-summary-STOP']:
            path = self.data/name
            path.touch()
            self.assertEqual(cli.pull(self.config, dry_run=True, publisher=pub, now=NOW)['state'], 'stopped')
            path.unlink()
        self.assertEqual(transport.reads, [])
        self.assertEqual(transport.verifications, 0)

    def test_private_verification_precedes_reads(self):
        transport = Transport(private=False)
        result = cli.pull(self.config, publisher=publisher(transport), now=NOW)
        self.assertEqual(result['error_code'], 'privacy')
        self.assertEqual(transport.reads, [])

    def test_untrusted_transport_metadata_is_rejected(self):
        for remote in [{'content': payload(), 'sha':'invalid'},
                       {'content': payload().decode(), 'sha':'c'*40},
                       {'content': b' ' * 65537, 'sha':'c'*40}]:
            transport = Transport()
            transport.read = lambda path, value=remote: value
            result = cli.pull(self.config, days=1, publisher=publisher(transport), now=NOW)
            self.assertEqual(result['state'], 'error')
            self.assertFalse(Path(self.config['source_db']).exists())

    def test_stop_created_during_read_prevents_ingestion(self):
        transport = Transport()
        def read(path):
            (self.data/'dot-summary-STOP').touch()
            return {'content':payload(), 'sha':'c'*40}
        transport.read = read
        result = cli.pull(self.config, days=1, publisher=publisher(transport), now=NOW)
        self.assertEqual(result['state'], 'stopped')
        self.assertFalse(Path(self.config['source_db']).exists())

    def test_transport_error_body_never_reaches_output(self):
        transport = Transport()
        def read(path):
            raise RuntimeError('PRIVATE upstream body and /secret/path')
        transport.read = read
        result = cli.pull(self.config, days=1, publisher=publisher(transport), now=NOW)
        self.assertEqual(result['error_code'], 'dot_pull_failed')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_ingests_and_duplicates(self):
        transport = Transport({'imports/dot/v1/'+DAY+'.json': payload()})
        first = cli.pull(self.config, days=1, publisher=publisher(transport), now=NOW)
        second = cli.pull(self.config, days=1, publisher=publisher(transport), now=NOW)
        self.assertEqual(first['new'], 1)
        self.assertEqual(second['unchanged'], 1)
        with Store(self.config['source_db']) as store:
            self.assertEqual(store.status()['counts']['messages'], 1)

    def test_removed_mutated_missing_or_malformed_day_quarantines_pending(self):
        for changed in [payload([]), None, b'PRIVATE malformed',
                        payload([fragment('topic_learning')])]:
            with self.subTest(kind=type(changed).__name__), tempfile.TemporaryDirectory() as directory:
                config = dict(self.config,data_dir=directory,source_db=str(Path(directory)/'collector.sqlite3'))
                path = 'imports/dot/v1/'+DAY+'.json'
                transport = Transport({path:payload()})
                cli.pull(config,days=1,publisher=publisher(transport),now=NOW)
                queue_path = Path(directory)/'personal-summary.sqlite3'
                with PersonalSummaryQueue(config['source_db'],queue_path) as queue:
                    queue.prepare()
                    self.assertEqual(len(queue.ready()),1)
                    transport.files = {path:changed} if changed is not None else {}
                    result = cli.pull(config,days=1,publisher=publisher(transport),now=NOW)
                    self.assertEqual(result['error_code'],'handoff_conflict')
                    self.assertEqual(queue.ready(),[])
                    self.assertEqual(queue.status()['counts']['invalidated'],1)
                with Store(config['source_db']) as store:
                    row = store.messages(source='dot-summary')[0]
                    self.assertFalse(row['complete'])
                    self.assertTrue(row['provenance']['unsupported_content'])
                    self.assertEqual(json.loads(row['text']),['topic_home'])
                    self.assertEqual(row['revision'],2)
                    self.assertEqual(store.status()['counts']['revisions'],2)
                    self.assertEqual(store.status()['counts']['pending_jobs'],2)
                transport.files = {path:payload()}
                self.assertEqual(cli.pull(config,days=1,publisher=publisher(transport),now=NOW)['error_code'],
                                 'handoff_conflict')
                with Store(config['source_db']) as store:
                    self.assertEqual(store.status()['counts']['revisions'],2)

    def test_delivered_removed_evidence_requires_review_and_preserves_receipt(self):
        transport = Transport({'imports/dot/v1/'+DAY+'.json':payload()})
        cli.pull(self.config,days=1,publisher=publisher(transport),now=NOW)
        with PersonalSummaryQueue(self.config['source_db'],self.data/'personal-summary.sqlite3') as queue:
            queue.prepare()
            fid = queue.ready()[0]['fragment_id']
            queue.delivered(fid,{'state':'published','blob_sha':'d'*40})
            transport.files = {}
            self.assertEqual(cli.pull(self.config,days=1,publisher=publisher(transport),now=NOW)['error_code'],
                             'handoff_conflict')
            queue.prepare()
            self.assertEqual(queue.status()['counts']['review_required'],1)
            receipt = queue.connection.execute('SELECT receipt FROM fragments WHERE fragment_id=?',(fid,)).fetchone()[0]
            self.assertEqual(json.loads(receipt)['blob_sha'],'d'*40)

    def test_conflict_prevents_other_new_day_ingestion_and_dry_run_does_not_quarantine(self):
        path = 'imports/dot/v1/'+DAY+'.json'
        transport = Transport({path:payload()})
        cli.pull(self.config,days=1,publisher=publisher(transport),now=NOW)
        other = '2026-10-26'
        transport.files = {path:payload([]),'imports/dot/v1/'+other+'.json':payload([fragment(day=other)],day=other)}
        result = cli.pull(self.config,days=2,dry_run=True,publisher=publisher(transport),now=NOW)
        self.assertEqual(result['error_code'],'handoff_conflict')
        with Store(self.config['source_db']) as store:
            self.assertTrue(store.messages()[0]['complete'])
        result = cli.pull(self.config,days=2,publisher=publisher(transport),now=NOW)
        self.assertEqual(result['error_code'],'handoff_conflict')
        with Store(self.config['source_db']) as store:
            self.assertEqual(len(store.messages()),1)

    def test_appending_remote_fragments_preserves_old_rows(self):
        path = 'imports/dot/v1/'+DAY+'.json'
        transport = Transport({path:payload()})
        cli.pull(self.config,days=1,publisher=publisher(transport),now=NOW)
        transport.files[path] = payload([fragment(),fragment('topic_learning','b'*32)])
        result = cli.pull(self.config,days=1,publisher=publisher(transport),now=NOW)
        self.assertEqual(result['state'],'idle')
        self.assertEqual((result['new'],result['revised'],result['unchanged']),(1,0,1))

    def test_user_exclusion_is_not_restored_and_can_disappear_remotely(self):
        path='imports/dot/v1/'+DAY+'.json'
        transport=Transport({path:payload()})
        cli.pull(self.config,days=1,publisher=publisher(transport),now=NOW)
        with Store(self.config['source_db']) as store:
            store.exclude(store.messages()[0]['message_key'])
        cli.pull(self.config,days=1,publisher=publisher(transport),now=NOW)
        transport.files={}
        self.assertEqual(cli.pull(self.config,days=1,publisher=publisher(transport),now=NOW)['state'],'idle')
        with Store(self.config['source_db']) as store:
            self.assertEqual(store.messages(),[])
            self.assertEqual(store.status()['counts']['exclusions'],1)

    def test_bounds_and_nonpersonal_publisher_fail_closed(self):
        for days in [0,15,True,'3']:
            self.assertEqual(cli.pull(self.config, days=days, now=NOW)['state'], 'error')
        transport = Transport()
        self.assertEqual(cli.pull(self.config, publisher=Publisher('unused',transport=transport), now=NOW)['state'],'error')
        self.assertEqual(transport.reads, [])

    def test_main_uses_canonical_loader_timezone_and_metadata_only(self):
        with patch.object(cli, 'load_config', return_value=self.config) as load, \
             patch.object(cli, 'configure_timezone') as configure, \
             patch.object(cli, 'pull', return_value={'state':'preview','fragments':1}) as pull:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = cli.main(['--config',str(self.data/'personal-summary.json'),'--days','2','--dry-run'])
            self.assertEqual(result, 0)
            load.assert_called_once_with((self.data/'personal-summary.json').resolve())
            configure.assert_called_once_with(self.config)
            pull.assert_called_once_with(self.config, days=2, dry_run=True)
            self.assertEqual(json.loads(output.getvalue()), {'state':'preview','fragments':1})

    def test_main_real_canonical_configuration_and_disabled_gate(self):
        source = next((Path(path)/'America'/'Chicago' for path in zoneinfo.TZPATH
                       if (Path(path)/'America'/'Chicago').is_file()), None)
        if source is None:
            self.skipTest('Supply installed IANA data via PYTHONTZPATH')
        old_path = zoneinfo.TZPATH
        self.addCleanup(lambda: (zoneinfo.reset_tzpath(old_path), zoneinfo.ZoneInfo.clear_cache()))
        target = self.data/'tzdata'/'America'/'Chicago'
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        config = dict(self.config, profile='personal', enabled=False,
            gh_path=str(self.data/'gh.exe'),
            queue_db=str(self.data/'personal-summary.sqlite3'), tzdata_dir=str(self.data/'tzdata'),
            tzdata_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        config_path = self.data/'personal-summary.json'
        config_path.write_text(json.dumps(config))
        with patch.object(cli, 'Publisher') as pub:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(['--config',str(config_path)]),0)
            self.assertEqual(json.loads(output.getvalue())['state'],'disabled')
            pub.assert_not_called()
            bad_path = self.data/'other.json'
            bad_path.write_text(json.dumps(config))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--config',str(bad_path)]),1)


if __name__ == '__main__':
    unittest.main()

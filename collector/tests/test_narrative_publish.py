import copy
import tempfile
import unittest
from pathlib import Path

from journal_collector.narrative_publish import NarrativePublisher, NarrativeError, validate_entry
from journal_collector.summary_publish import PublishError


def inputs(text='i wanted a calmer way to keep the small tasks together.'):
    return ({'version': 1, 'day': '2026-10-27', 'paragraphs': [
        {'text': text, 'evidence_ids': ['personal_1']}]},
        {'version': 1, 'day': '2026-10-27', 'evidence': [
            {'id': 'personal_1', 'day': '2026-10-27', 'source': 'personal'}]})


class Remote:
    def __init__(self):
        self.content = b'# October 27\n\nOriginal Rook prose.\n'
        self.sha = '1' * 40
        self.writes = 0
        self.reads = 0
        self.private = True
        self.conflict = False
        self.ambiguous = False
        self.drop = False
    def repo(self):
        return {'full_name': 'snackdriven/day-crumbs', 'private': self.private,
                'default_branch': 'main', 'permissions': {'push': True}}
    def read(self, path):
        self.reads += 1
        return None if self.content is None else {'content': self.content, 'sha': self.sha}
    def write(self, path, content, sha):
        self.writes += 1
        if self.conflict:
            self.conflict = False
            self.content += b'\nConcurrent manual sentence.\n'
            self.sha = '2' * 40
            raise PublishError('conflict')
        if sha != (self.sha if self.content is not None else None):
            raise PublishError('conflict')
        if not self.drop:
            self.content, self.sha = content, '3' * 40
        if self.ambiguous:
            raise PublishError('network')
        return {'commit_sha': '4' * 40, 'blob_sha': self.sha}


class NarrativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS temporary paths may use /var, a symlink to /private/var.
        # Fixtures must use the same canonical paths required by the real CLI.
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / 'receipts.sqlite3'
        self.remote = Remote()
        self.entry, self.manifest = inputs()
    def tearDown(self):
        self.temp.cleanup()
    def publisher(self):
        return NarrativePublisher('gh', self.db, transport=self.remote)
    def test_publish_preserves_prefix_and_retry_after_restart(self):
        original = self.remote.content
        first = self.publisher().publish(self.entry, self.manifest)
        self.assertEqual('published', first['state'])
        self.assertTrue(self.remote.content.startswith(original))
        self.assertIn(self.entry['paragraphs'][0]['text'].encode(), self.remote.content)
        self.assertEqual('reconciled', self.publisher().publish(self.entry, self.manifest)['state'])
        self.assertEqual(1, self.remote.writes)
    def test_deleted_block_and_missing_file_never_resurrect(self):
        for deleted in (b'# user kept only this\n', None):
            with self.subTest(deleted=deleted):
                self.db = self.root / ('none.sqlite3' if deleted is None else 'gone.sqlite3')
                self.remote = Remote()
                self.publisher().publish(self.entry, self.manifest)
                self.remote.content = deleted
                writes = self.remote.writes
                with self.assertRaisesRegex(NarrativeError, 'review_required'):
                    self.publisher().publish(self.entry, self.manifest)
                self.assertEqual(writes, self.remote.writes)
    def test_manual_edit_owned_body_blocks_rewrite(self):
        self.publisher().publish(self.entry, self.manifest)
        self.remote.content = self.remote.content.replace(b'calmer', b'quieter')
        with self.assertRaisesRegex(NarrativeError, 'review_required'):
            self.publisher().publish(self.entry, self.manifest)
        self.assertEqual(1, self.remote.writes)
    def test_append_new_paragraph_only_preserves_unowned_suffix(self):
        self.publisher().publish(self.entry, self.manifest)
        self.remote.content += b'\nMy own later thought.\n'
        self.entry['paragraphs'].append({'text': 'i still had the syncing question open.', 'evidence_ids': ['work_2']})
        self.manifest['evidence'].append({'id': 'work_2', 'day': self.entry['day'], 'source': 'work', 'sanitized': True})
        self.assertEqual('published', self.publisher().publish(self.entry, self.manifest)['state'])
        self.assertIn(b'My own later thought.', self.remote.content)
        self.assertEqual(1, self.remote.content.count(b':begin -->'))
        self.assertEqual(2, self.remote.writes)
    def test_replacing_previous_paragraph_is_rejected(self):
        self.publisher().publish(self.entry, self.manifest)
        self.entry['paragraphs'][0]['text'] = 'i rewrote everything.'
        with self.assertRaisesRegex(NarrativeError, 'non_append_update'):
            self.publisher().publish(self.entry, self.manifest)
        self.assertEqual(1, self.remote.writes)
    def test_ambiguous_success_reconciles_exact_body(self):
        self.remote.ambiguous = True
        self.assertEqual('reconciled', self.publisher().publish(self.entry, self.manifest)['state'])
        self.assertEqual(1, self.remote.writes)
    def test_crash_receipt_persisted_before_remote_write(self):
        write = self.remote.write
        def crash(*args):
            write(*args)
            raise RuntimeError('simulated process crash')
        self.remote.write = crash
        with self.assertRaises(RuntimeError):
            self.publisher().publish(self.entry, self.manifest)
        self.remote.write = write
        self.assertEqual('reconciled', self.publisher().publish(self.entry, self.manifest)['state'])
        self.assertEqual(1, self.remote.writes)
    def test_conflict_reload_keeps_manual_change(self):
        self.remote.conflict = True
        self.publisher().publish(self.entry, self.manifest)
        self.assertIn(b'Concurrent manual sentence.', self.remote.content)
        self.assertEqual(2, self.remote.writes)
    def test_success_without_readback_never_delivered(self):
        self.remote.drop = True
        with self.assertRaisesRegex(NarrativeError, 'verification'):
            self.publisher().publish(self.entry, self.manifest)
        self.assertEqual(1, self.remote.writes)
        self.remote.drop = False
        self.assertEqual('published', self.publisher().publish(self.entry, self.manifest)['state'])
    def test_private_repository_required(self):
        self.remote.private = False
        with self.assertRaisesRegex(NarrativeError, 'privacy'):
            self.publisher().publish(self.entry, self.manifest)
        self.assertEqual(0, self.remote.writes)
    def test_quiet_skip_does_not_touch_remote(self):
        self.entry['paragraphs'] = []
        self.manifest['evidence'] = []
        self.assertEqual('skipped', self.publisher().publish(self.entry, self.manifest)['state'])
        self.assertEqual(0, self.remote.reads)
        self.assertEqual(0, self.remote.writes)
    def test_reject_bad_schema_unknown_ids_day_and_unsanitized_work(self):
        cases = []
        bad = copy.deepcopy(self.entry); bad['paragraphs'][0]['evidence_ids'] = ['missing']; cases.append((bad,self.manifest))
        bad = copy.deepcopy(self.manifest); bad['evidence'][0]['day'] = '2026-10-26'; cases.append((self.entry,bad))
        bad = copy.deepcopy(self.manifest); bad['evidence'][0]['source'] = 'work'; cases.append((self.entry,bad))
        for text in ('Work context from conversations.', 'Topics included testing.', '# heading', '- task', 'two\nparagraphs', '<!-- narrative:bad:begin -->'):
            entry, manifest = inputs(text); cases.append((entry,manifest))
        for entry, manifest in cases:
            with self.subTest(entry=entry,manifest=manifest), self.assertRaises(NarrativeError):
                validate_entry(entry,manifest)



    def test_crash_then_deleted_remote_requires_review(self):
        write = self.remote.write
        def crash(*args):
            write(*args)
            raise RuntimeError('crash')
        self.remote.write = crash
        with self.assertRaises(RuntimeError):
            self.publisher().publish(self.entry, self.manifest)
        self.remote.write = write
        self.remote.content = None
        with self.assertRaisesRegex(NarrativeError, 'review_required'):
            self.publisher().publish(self.entry, self.manifest)
        self.assertEqual(1, self.remote.writes)

    def test_prior_delivery_receipts_retained_after_append(self):
        import sqlite3
        self.publisher().publish(self.entry, self.manifest)
        self.entry['paragraphs'].append({'text': 'i kept the follow-up question open.', 'evidence_ids': ['personal_2']})
        self.manifest['evidence'].append({'id': 'personal_2', 'day': self.entry['day'], 'source': 'personal'})
        self.publisher().publish(self.entry, self.manifest)
        db = sqlite3.connect(self.db)
        try:
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM narrative_attempts WHERE state='delivered'").fetchone()[0])
        finally:
            db.close()
    def test_cli_defaults_to_local_preview_and_publish_requires_receipts(self):
        import json
        import subprocess
        import sys
        entry_file, manifest_file = self.root / 'entry.json', self.root / 'manifest.json'
        entry_file.write_text(json.dumps(self.entry), encoding='utf-8')
        manifest_file.write_text(json.dumps(self.manifest), encoding='utf-8')
        preview = self.root / 'preview.md'
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'publish_narrative.py'),
                   '--entry', str(entry_file), '--manifest', str(manifest_file), '--preview', str(preview)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('validated_preview', json.loads(result.stdout)['state'])
        self.assertIn(self.entry['paragraphs'][0]['text'], preview.read_text(encoding='utf-8'))
        self.assertFalse(self.db.exists())
        result = subprocess.run(command + ['--publish'], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
    def test_effective_manifest_ignores_unused_and_normalizes_order(self):
        import sqlite3
        self.manifest['evidence'].append({'id': 'unused_2', 'day': self.entry['day'], 'source': 'personal', 'observedAt': 'changed'})
        first = self.publisher().publish(self.entry, self.manifest)
        self.manifest['evidence'].reverse()
        self.manifest['evidence'][0]['observedAt'] = 'new observation'
        self.assertEqual(first['input_digest'], self.publisher().publish(self.entry, self.manifest)['input_digest'])
        db = sqlite3.connect(self.db)
        try:
            import json
            value = json.loads(db.execute('SELECT manifest_json FROM narrative_support').fetchone()[0])
            self.assertEqual(['personal_1'], [item['id'] for item in value['evidence']])
        finally:
            db.close()
        self.assertEqual(1, self.remote.writes)
    def test_support_manifest_is_durable_before_remote_attempt(self):
        import sqlite3
        write = self.remote.write
        def inspect(*args):
            db = sqlite3.connect(self.db)
            try:
                self.assertEqual(1, db.execute('SELECT COUNT(*) FROM narrative_support').fetchone()[0])
            finally:
                db.close()
            return write(*args)
        self.remote.write = inspect
        self.publisher().publish(self.entry, self.manifest)
    def test_tampered_support_cannot_be_overwritten(self):
        import sqlite3
        self.publisher().publish(self.entry, self.manifest)
        db = sqlite3.connect(self.db)
        try:
            db.execute("UPDATE narrative_support SET manifest_json='{}'")
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(NarrativeError, 'support_conflict'):
            self.publisher().publish(self.entry, self.manifest)
        self.assertEqual(1, self.remote.writes)
    def test_crashed_minimal_candidate_reconciles_despite_unused_input_growth(self):
        self.manifest['evidence'].append({'id': 'unused', 'day': self.entry['day'], 'source': 'personal'})
        write = self.remote.write
        def crash(*args):
            write(*args)
            raise RuntimeError('simulated crash')
        self.remote.write = crash
        with self.assertRaises(RuntimeError):
            self.publisher().publish(self.entry, self.manifest)
        self.remote.write = write
        self.manifest['evidence'].append({'id': 'later_unused', 'day': self.entry['day'], 'source': 'personal'})
        result = self.publisher().publish(self.entry, self.manifest)
        self.assertEqual('reconciled', result['state'])
        self.assertEqual(1, self.remote.writes)
    def test_existing_delivery_can_seed_missing_support_table(self):
        import sqlite3
        first = self.publisher().publish(self.entry, self.manifest)
        db = sqlite3.connect(self.db)
        try:
            db.execute('DROP TABLE narrative_support')
            db.commit()
        finally:
            db.close()
        result = self.publisher().publish(self.entry, self.manifest)
        self.assertEqual('reconciled', result['state'])
        self.assertEqual(first['input_digest'], result['input_digest'])
        self.assertEqual(1, self.remote.writes)
    def test_stop_preexisting_prevents_remote_reads_and_writes(self):
        stop = self.root / 'narrative-STOP'
        stop.touch()
        with self.assertRaisesRegex(NarrativeError, 'stopped'):
            NarrativePublisher('gh', self.db, transport=self.remote, stop_path=stop).publish(self.entry,self.manifest)
        self.assertEqual(0,self.remote.reads)
        self.assertEqual(0,self.remote.writes)
    def test_stop_created_during_privacy_read_prevents_write(self):
        stop = self.root / 'narrative-STOP'
        repo = self.remote.repo
        def privacy():
            value = repo()
            stop.touch()
            return value
        self.remote.repo = privacy
        with self.assertRaisesRegex(NarrativeError, 'stopped'):
            NarrativePublisher('gh', self.db, transport=self.remote, stop_path=stop).publish(self.entry,self.manifest)
        self.assertEqual(0,self.remote.writes)
    def test_stop_after_accepted_write_allows_delivery_ack(self):
        stop = self.root / 'narrative-STOP'
        write = self.remote.write
        def accepted(*args):
            result = write(*args)
            stop.touch()
            return result
        self.remote.write = accepted
        result = NarrativePublisher('gh',self.db,transport=self.remote,stop_path=stop).publish(self.entry,self.manifest)
        self.assertEqual('published',result['state'])
        self.assertEqual(1,self.remote.writes)
    def cli_fixture(self):
        import json
        import sys
        root = self.root
        writer = root/'narrative-writer';writer.mkdir(exist_ok=True)
        entry_file,manifest_file = writer/'entry.json',writer/'manifest.json'
        entry_file.write_text(json.dumps(self.entry),encoding='utf-8')
        manifest_file.write_text(json.dumps(self.manifest),encoding='utf-8')
        config = root/'personal-summary.json'
        value = {'profile':'personal','data_dir':str(root),'source_db':str(root/'collector.sqlite3'),
            'queue_db':str(root/'personal-summary.sqlite3'),'gh_path':str(Path(sys.executable).resolve()),'enabled':False,
            'repository':'snackdriven/day-crumbs','branch':'main','tzdata_dir':str(root/'tzdata'),
            'tzdata_sha256':'a'*64}
        config.write_text(json.dumps(value),encoding='utf-8')
        return ['--publish','--config',str(config),'--entry',str(entry_file),'--manifest',str(manifest_file),
                '--receipts',str(writer/'receipts.sqlite3')]
    def test_cli_stopped_is_metadata_success_without_network(self):
        import contextlib
        import io
        import json
        from unittest.mock import patch
        from publish_narrative import main
        args=self.cli_fixture()
        (self.root/'narrative-STOP').touch()
        output=io.StringIO()
        with patch('personal_summary_service.configure_timezone'), patch('publish_narrative.NarrativePublisher') as publisher, contextlib.redirect_stdout(output):
            self.assertEqual(0,main(args))
        self.assertEqual('stopped',json.loads(output.getvalue())['state'])
        publisher.assert_not_called()
    def test_cli_binds_receipts_and_inputs_to_private_runtime(self):
        import contextlib
        import io
        from unittest.mock import patch
        from publish_narrative import main
        args=self.cli_fixture()
        for flag,path in (('--entry',str(self.root/'outside.json')),
                          ('--receipts',str(self.root/'other.sqlite3'))):
            bad=list(args);bad[bad.index(flag)+1]=path
            with patch('personal_summary_service.configure_timezone'), patch('publish_narrative.NarrativePublisher') as publisher, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1,main(bad))
            publisher.assert_not_called()
    def test_cli_passes_stop_and_configured_gh_to_publisher(self):
        import contextlib
        import io
        import sys
        from unittest.mock import patch
        from publish_narrative import main
        args=self.cli_fixture()
        with patch('personal_summary_service.configure_timezone'), patch('publish_narrative.NarrativePublisher') as publisher, contextlib.redirect_stdout(io.StringIO()):
            publisher.return_value.publish.return_value={'state':'reconciled'}
            self.assertEqual(0,main(args))
        self.assertEqual(str(Path(sys.executable).resolve()),publisher.call_args.args[0])
        self.assertEqual(self.root/'narrative-STOP',publisher.call_args.kwargs['stop_path'])
    def test_stop_at_final_privacy_check_is_retryable_after_resume(self):
        stop=self.root/'narrative-STOP'
        repo=self.remote.repo
        calls=0
        def privacy():
            nonlocal calls
            calls+=1
            value=repo()
            if calls==2:stop.touch()
            return value
        self.remote.repo=privacy
        with self.assertRaisesRegex(NarrativeError,'stopped'):
            NarrativePublisher('gh',self.db,transport=self.remote,stop_path=stop).publish(self.entry,self.manifest)
        self.assertEqual(0,self.remote.writes)
        stop.unlink()
        result=NarrativePublisher('gh',self.db,transport=self.remote,stop_path=stop).publish(self.entry,self.manifest)
        self.assertEqual('published',result['state'])
    def test_canonical_fixture_does_not_relax_symlink_rejection(self):
        from unittest.mock import patch
        from publish_narrative import _plain_absolute
        alias=self.root/'pretend-var'/'runtime'
        original=Path.is_symlink
        # Model macOS's aliased /var ancestor without creating real symlinks
        # or needing Windows developer-mode privileges.
        with patch.object(Path,'is_symlink',autospec=True,
                          side_effect=lambda path:path==alias.parent or original(path)):
            with self.assertRaisesRegex(NarrativeError,'private_runtime_binding'):
                _plain_absolute(alias)
            self.assertEqual(self.root,_plain_absolute(self.root))

import copy
import unittest
from journal_collector.narrative_cleanup import cleanup_day, CleanupError
from journal_collector.summary_publish import render_fragment, PublishError

DAY = '2026-10-27'
ENTRY = 'entries/2026/10/2026-10-27.md'
ARCHIVE = 'imports/retired-context/v1/2026-10-27.json'
NARRATIVE = b'<!-- journal-narrative:' + b'9'*32 + b':begin -->\ni wanted the writing back.\n<!-- journal-narrative:' + b'9'*32 + b':end -->'

def block(profile='work', fid='a'*32):
    return render_fragment({'fragment_id': fid, 'day': DAY, 'facts': ['topic_testing'], 'coverage': 'partial'}, profile=profile).encode()

class Remote:
    def __init__(self, content):
        self.files = {ENTRY: {'content': content, 'sha': '1'*40}}
        self.writes = []
        self.fail_archive = False
        self.conflict = False
        self.private = True
    def repo(self):
        return {'full_name': 'snackdriven/day-crumbs', 'private': self.private, 'default_branch': 'main', 'permissions': {'push': True}}
    def read(self, path):
        return copy.deepcopy(self.files.get(path))
    def write(self, path, content, sha):
        self.writes.append(path)
        old = self.files.get(path)
        if self.conflict and path == ENTRY:
            self.conflict = False
            old['content'] += b'\nConcurrent user thought.\n'
            old['sha'] = '2'*40
            raise PublishError('conflict')
        if sha != (old['sha'] if old else None):
            raise PublishError('conflict')
        if not (path == ARCHIVE and self.fail_archive):
            self.files[path] = {'content': content, 'sha': '3'*40}
        return {'commit_sha': '4'*40, 'blob_sha': '3'*40}

class CleanupTests(unittest.TestCase):
    def test_archive_before_cleanup_exact_spans_only(self):
        original = b'User prefix\r\n' + block() + b'\nMiddle\n' + NARRATIVE + b'\nSuffix\xff'
        remote = Remote(original)
        result = cleanup_day('gh', DAY, transport=remote)
        self.assertEqual('cleaned', result['state'])
        self.assertEqual(original.replace(block(), b''), remote.files[ENTRY]['content'])
        self.assertEqual([ARCHIVE, ENTRY], remote.writes)
        self.assertEqual('unchanged', cleanup_day('gh', DAY, transport=remote)['state'])
        self.assertEqual(2, len(remote.writes))
    def test_edited_preserved_and_reported_while_exact_personal_removed(self):
        edited = block().replace(b'testing', b'thoughts I edited')
        personal = block('personal', 'b'*32)
        remote = Remote(NARRATIVE + b'\n' + edited + b'\n' + personal)
        result = cleanup_day('gh', DAY, transport=remote)
        self.assertEqual(1, result['removed'])
        self.assertEqual(1, result['review_required'])
        self.assertIn(edited, remote.files[ENTRY]['content'])
        self.assertNotIn(personal, remote.files[ENTRY]['content'])
    def test_no_narrative_no_remote_writes(self):
        remote = Remote(block())
        self.assertEqual('no_narrative', cleanup_day('gh', DAY, transport=remote)['state'])
        self.assertEqual([], remote.writes)
    def test_archive_readback_failure_never_removes(self):
        original = NARRATIVE + block()
        remote = Remote(original); remote.fail_archive = True
        with self.assertRaisesRegex(CleanupError, 'archive_verification'):
            cleanup_day('gh', DAY, transport=remote)
        self.assertEqual(original, remote.files[ENTRY]['content'])
        self.assertEqual([ARCHIVE], remote.writes)
    def test_entry_conflict_reloads_preserves_new_user_bytes(self):
        remote = Remote(NARRATIVE + block()); remote.conflict = True
        cleanup_day('gh', DAY, transport=remote)
        self.assertIn(b'Concurrent user thought.', remote.files[ENTRY]['content'])
        self.assertNotIn(block(), remote.files[ENTRY]['content'])
        self.assertEqual(1, remote.writes.count(ARCHIVE))
    def test_existing_archive_conflict_fail_closed(self):
        original = NARRATIVE + block()
        remote = Remote(original)
        remote.files[ARCHIVE] = {'content': b'{}', 'sha': '5'*40}
        with self.assertRaisesRegex(CleanupError, 'archive_conflict'):
            cleanup_day('gh', DAY, transport=remote)
        self.assertEqual(original, remote.files[ENTRY]['content'])
        self.assertEqual([], remote.writes)
    def test_malformed_marker_no_mutation(self):
        for suffix in (b'<!-- work-summary:bad:begin -->', block()):
            remote = Remote(NARRATIVE + block() + suffix)
            with self.assertRaisesRegex(CleanupError, 'malformed_markers'):
                cleanup_day('gh', DAY, transport=remote)
            self.assertEqual([], remote.writes)
    def test_private_required(self):
        remote = Remote(NARRATIVE + block()); remote.private = False
        with self.assertRaisesRegex(CleanupError, 'privacy'):
            cleanup_day('gh', DAY, transport=remote)
        self.assertEqual([], remote.writes)
    def test_crash_after_archive_before_removal_reuses_archive(self):
        remote = Remote(NARRATIVE + block())
        write = remote.write
        def crash(path, content, sha):
            if path == ENTRY:
                raise RuntimeError('crash before cleanup')
            return write(path, content, sha)
        remote.write = crash
        with self.assertRaises(RuntimeError):
            cleanup_day('gh', DAY, transport=remote)
        self.assertIn(block(), remote.files[ENTRY]['content'])
        self.assertIn(ARCHIVE, remote.files)
        remote.write = write
        self.assertEqual('cleaned', cleanup_day('gh', DAY, transport=remote)['state'])
        self.assertEqual(1, remote.writes.count(ARCHIVE))
    def test_archive_contains_exact_original_generated_bytes(self):
        import base64
        import json
        remote = Remote(NARRATIVE + block())
        cleanup_day('gh', DAY, transport=remote)
        archived = json.loads(remote.files[ARCHIVE]['content'])
        self.assertEqual(block(), base64.b64decode(archived['blocks'][0]['body_base64']))
    def test_complete_coverage_and_explicit_reaction_supported(self):
        old = render_fragment({'fragment_id': 'c'*32, 'day': DAY,
            'facts': ['reaction_tired', 'topic_testing'], 'coverage': 'complete'}, profile='personal').encode()
        remote = Remote(NARRATIVE + old)
        self.assertEqual(1, cleanup_day('gh', DAY, transport=remote)['removed'])
        self.assertEqual(NARRATIVE, remote.files[ENTRY]['content'])
    def test_stop_preexisting_prevents_all_cleanup(self):
        import tempfile
        from pathlib import Path
        remote=Remote(NARRATIVE+block())
        with tempfile.TemporaryDirectory() as directory:
            stop=Path(directory)/'narrative-STOP';stop.touch()
            with self.assertRaisesRegex(CleanupError,'stopped'):
                cleanup_day('gh',DAY,transport=remote,stop_path=stop)
        self.assertEqual([],remote.writes)
    def test_stop_created_during_privacy_check_prevents_archive(self):
        import tempfile
        from pathlib import Path
        remote=Remote(NARRATIVE+block());repo=remote.repo
        with tempfile.TemporaryDirectory() as directory:
            stop=Path(directory)/'narrative-STOP'
            def privacy():
                value=repo();stop.touch();return value
            remote.repo=privacy
            with self.assertRaisesRegex(CleanupError,'stopped'):
                cleanup_day('gh',DAY,transport=remote,stop_path=stop)
        self.assertEqual([],remote.writes)
    def test_stop_after_archive_write_prevents_entry_removal(self):
        import tempfile
        from pathlib import Path
        remote=Remote(NARRATIVE+block());write=remote.write
        with tempfile.TemporaryDirectory() as directory:
            stop=Path(directory)/'narrative-STOP'
            def accepted(*args):
                result=write(*args);stop.touch();return result
            remote.write=accepted
            with self.assertRaisesRegex(CleanupError,'stopped'):
                cleanup_day('gh',DAY,transport=remote,stop_path=stop)
        self.assertEqual([ARCHIVE],remote.writes)
        self.assertIn(block(),remote.files[ENTRY]['content'])

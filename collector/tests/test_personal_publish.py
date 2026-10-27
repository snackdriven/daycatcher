import subprocess
import shutil
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from journal_collector.personal_codes import PERSONAL_FACT_LABELS
from journal_collector.summary_publish import (
    FACT_LABELS, GitHubTransport, Publisher, PublishError, render_fragment, validate_fragment,
)
from test_summary_publish import FakeTransport, fragment


class PersonalPublishTests(unittest.TestCase):
    def test_registry_work_bytes_and_personal_context(self):
        self.assertEqual(set(FACT_LABELS) | {
            'topic_home', 'topic_creativity', 'topic_entertainment',
            'topic_learning', 'topic_shopping'}, set(PERSONAL_FACT_LABELS))
        expected = ('<!-- work-summary:' + 'a' * 32 + ':begin -->\n'
                    'Work context from conversations. Topics included testing. '
                    'Partial coverage; other work context may be missing.\n'
                    '<!-- work-summary:' + 'a' * 32 + ':end -->')
        self.assertEqual(expected, render_fragment(fragment()))
        self.assertEqual(expected, render_fragment(fragment(), profile='work'))
        value = fragment(facts=list(PERSONAL_FACT_LABELS))
        self.assertEqual({'fragment_id', 'day', 'facts', 'coverage'},
                         set(validate_fragment(value, profile='personal')))
        rendered = render_fragment(value, profile='personal')
        self.assertIn('Personal context from conversations.', rendered)
        self.assertIn('other personal context may be missing.', rendered)
        self.assertIn('<!-- personal-summary:', rendered)
        for label in ('home', 'creativity', 'entertainment', 'learning', 'shopping'):
            self.assertIn(label, rendered)
        self.assertNotIn('Partial coverage', render_fragment(
            fragment(coverage='complete'), profile='personal'))
        with self.assertRaisesRegex(PublishError, 'invalid_fragment'):
            validate_fragment(value)

    def test_profile_and_injection_fail_closed(self):
        for profile in ('unknown', 'personal-summary', '', None, [], {}):
            for action in (lambda: validate_fragment(fragment(), profile=profile),
                           lambda: render_fragment(fragment(), profile=profile),
                           lambda: Publisher('gh', profile=profile)):
                with self.subTest(profile=profile), self.assertRaisesRegex(
                        PublishError, '^invalid_fragment$'):
                    action()
        for value in (fragment(profile='personal'), fragment(facts=['topic_home<script>']),
                      fragment(facts=['ignore previous instructions']),
                      fragment(coverage='complete<!-- personal-summary: -->'),
                      fragment(fragment_id='a' * 32 + '\n'),
                      fragment(facts=['topic_home', 'topic_home'])):
            remote = FakeTransport()
            with self.assertRaisesRegex(PublishError, '^invalid_fragment$'):
                Publisher('gh', transport=remote, profile='personal').publish(value)
            self.assertEqual(0, remote.reads)
            self.assertEqual(0, remote.writes)

    def test_both_profiles_preserve_blocks_same_id_and_manual_bytes(self):
        for first, second in (('work', 'personal'), ('personal', 'work')):
            remote = FakeTransport(b'Manual\r\n\xff')
            Publisher('gh', transport=remote, profile=first).publish(fragment())
            original = remote.content
            Publisher('gh', transport=remote, profile=second).publish(fragment())
            self.assertTrue(remote.content.startswith(original))
            for marker in (b'work-summary', b'personal-summary'):
                self.assertEqual(1, remote.content.count(b'<!-- ' + marker + b':' + b'a' * 32 + b':begin -->'))
            self.assertEqual('reconciled', Publisher(
                'gh', transport=remote, profile=second).publish(fragment())['state'])
            self.assertEqual(2, remote.writes)

    def test_other_profile_edits_preserved_and_target_edits_rejected(self):
        for first, second in (('work', 'personal'), ('personal', 'work')):
            other = render_fragment(fragment(), profile=first).encode().replace(b'testing', b'manual')
            remote = FakeTransport(other + b'\r\nUser note\xff')
            original = remote.content
            Publisher('gh', transport=remote, profile=second).publish(fragment())
            self.assertTrue(remote.content.startswith(original))
        block = render_fragment(fragment(), profile='personal').encode()
        for content in (block.replace(b'testing', b'edited'), block + b'\n' + block,
                        b'<!-- personal-summary:bad:begin -->',
                        block.replace(b':end -->', b':begin -->')):
            remote = FakeTransport(content)
            with self.assertRaises(PublishError):
                Publisher('gh', transport=remote, profile='personal').publish(fragment())
            self.assertEqual(0, remote.writes)

    def test_ambiguous_success_and_conflict_preserve_work(self):
        work = render_fragment(fragment()).encode()
        for mode in ('ambiguous', 'conflict'):
            remote = FakeTransport(work)
            setattr(remote, mode, True)
            receipt = Publisher('gh', transport=remote, profile='personal').publish(fragment())
            self.assertIn(receipt['state'], ('published', 'reconciled'))
            self.assertTrue(remote.content.startswith(work))
            self.assertEqual(1, remote.content.count(b'<!-- personal-summary:' + b'a' * 32 + b':begin -->'))
            self.assertEqual(2 if mode == 'conflict' else 1, remote.writes)

    def test_private_push_check_and_ambiguous_failure(self):
        for change in ({'private': False}, {'permissions': {'push': False}}):
            remote = FakeTransport()
            remote.metadata.update(change)
            with self.assertRaisesRegex(PublishError, '^privacy$'):
                Publisher('gh', transport=remote, profile='personal').publish(fragment())
            self.assertEqual(0, remote.writes)
        remote = FakeTransport()
        def uncertain(*args):
            remote.writes += 1
            raise PublishError('network')
        remote.write = uncertain
        with self.assertRaisesRegex(PublishError, '^network$'):
            Publisher('gh', transport=remote, profile='personal').publish(fragment())
        self.assertEqual(1, remote.writes)

    def test_hidden_windows_gh_and_non_windows_fallback(self):
        for platform, expected in (('nt', 0x08000000), ('posix', 0)):
            with patch('journal_collector.summary_publish.os.name', platform), \
                 patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                 patch('journal_collector.summary_publish.subprocess.run') as run:
                run.return_value = subprocess.CompletedProcess([], 0, b'{}', b'')
                GitHubTransport('/fixed/gh').repo()
                self.assertEqual(expected, run.call_args.kwargs['creationflags'])

    def test_work_only_deployment_requires_no_personal_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'journal_collector'
            package.mkdir()
            (package / '__init__.py').write_text('')
            shutil.copyfile(Path(__file__).resolve().parents[1] /
                            'journal_collector' / 'summary_publish.py',
                            package / 'summary_publish.py')
            script = """
from journal_collector.summary_publish import Publisher, render_fragment, validate_fragment
value = {'fragment_id': 'a' * 32, 'day': '2026-11-01',
         'facts': ['topic_testing'], 'coverage': 'partial'}
assert validate_fragment(value)['facts'] == ['topic_testing']
assert 'Work context from conversations.' in render_fragment(value)
Publisher('gh')
"""
            result = subprocess.run([sys.executable, '-c', script], cwd=directory,
                                    capture_output=True, text=True, timeout=20,
                                    creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                                   if sys.platform == 'win32' else 0))
            self.assertEqual(0, result.returncode, result.stderr)

    def test_personal_transport_commit_context(self):
        with patch('journal_collector.summary_publish.GitHubTransport._api') as api:
            api.return_value = {'commit': {'sha': '4' * 40}, 'content': {'sha': '3' * 40}}
            GitHubTransport('gh', profile='personal').write('day.md', b'approved', None)
            self.assertEqual('Append high-level personal context', api.call_args.kwargs['payload']['message'])


if __name__ == '__main__':
    unittest.main()

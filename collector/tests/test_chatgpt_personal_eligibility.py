import json
import unittest

from journal_collector.personal_summary import _eligible


class ChatGPTPersonalEligibilityTests(unittest.TestCase):
    def row(self, **changes):
        row = dict(source='chatgpt-native', role='user', text='Learning to paint at home.',
                   source_time='2026-10-27T14:00:00Z', complete=True,
                   provenance=json.dumps(dict(adapter='chatgpt-native-v1', origin='human',
                                              timestamp_valid=True, user_only=True)))
        row.update(changes)
        return row

    def test_verified_native_user_is_eligible(self):
        self.assertEqual(('2026-10-27', ['topic_creativity', 'topic_home', 'topic_learning']),
                         _eligible(self.row()))

    def test_unknown_adapter_assistant_incomplete_and_bad_provenance_rejected(self):
        for changes in ({'role': 'assistant'}, {'complete': False}, {'source_time': None},
                        {'provenance': '{}'},
                        {'provenance': json.dumps(dict(adapter='chatgpt-native-v1', origin='human',
                                                      timestamp_valid=False, user_only=True))},
                        {'provenance': json.dumps(dict(adapter='chatgpt-native-v1', origin='uncertain',
                                                      timestamp_valid=True, user_only=True))}):
            self.assertIsNone(_eligible(self.row(**changes)))

    def test_source_maintenance_and_synthetic_abstain(self):
        for text in ('Planning Day Crumbs', 'Learning synthetic probe'):
            self.assertEqual(('2026-10-27', []), _eligible(self.row(text=text)))

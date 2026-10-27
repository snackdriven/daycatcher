import json
import unittest

from journal_collector.personal_summary import _eligible


class DotPersonalEligibilityTests(unittest.TestCase):
    def row(self, **changes):
        row = dict(source='dot-summary', role='assistant', complete=True,
                   text='["topic_learning"]', source_time=None,
                   provenance=json.dumps(dict(adapter='dot-fixed-handoff-v1',
                       origin='source-summary', journal_day='2026-10-27',
                       fact_codes=['topic_learning'], handoff_id='a'*32)))
        row.update(changes)
        return row

    def test_summary_day_is_explicit_without_inventing_message_timestamp(self):
        self.assertEqual(('2026-10-27', ['topic_learning']), _eligible(self.row()))

    def test_unknown_malformed_and_inferred_reactions_rejected(self):
        original = json.loads(self.row()['provenance'])
        invalid = [dict(original, adapter='unknown'), dict(original, origin='human'),
                   dict(original, journal_day='2026-02-30'),
                   dict(original, fact_codes=['reaction_tired']),
                   dict(original, fact_codes=['PRIVATE-TEXT']),
                   dict(original, fact_codes=['topic_learning', 'topic_learning']),
                   dict(original, handoff_id='not-an-id')]
        for provenance in invalid:
            self.assertIsNone(_eligible(self.row(provenance=json.dumps(provenance))))
        for changes in ({'complete':False}, {'role':'user'},
                        {'source_time':'2026-10-27T14:00:00Z'},
                        {'text':'PRIVATE RAW CHAT'}, {'provenance':'{}'}):
            self.assertIsNone(_eligible(self.row(**changes)))

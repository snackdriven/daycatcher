import unittest

from journal_collector.work_language import (
    LanguageError, render_description, is_controlled_description, SLOT_PROPERTIES, OPERATIONS,
)


class WorkLanguageTests(unittest.TestCase):
    def test_operation_strength_purpose_and_reaction_are_rendered_from_owned_words(self):
        self.assertEqual(render_description('reported_action','check','release_blockers','prepare_release','none'),
                         'I checked release blockers to prepare for a release.')
        self.assertEqual(render_description('intent','review','open_changes','none','none'),
                         'I wanted to review open changes.')
        self.assertEqual(render_description('explicit_reaction','experience','work','none','frustrated'),
                         'I felt frustrated about work.')
        self.assertTrue(is_controlled_description('I discussed comparing feature behavior.','discussion'))
    def test_arbitrary_names_free_text_unknown_pairs_and_reaction_upgrades_are_rejected(self):
        cases=[('reported_action','review','PRIVATE_NAME','none','none'),
               ('intent','check','release_blockers','PRIVATE_PROJECT','none'),
               ('reported_action','review','open_changes','none','frustrated'),
               ('explicit_reaction','experience','work','none','none'),
               ('discussion','check','open_changes','none','none')]
        for args in cases:
            with self.subTest(args=args),self.assertRaises(LanguageError):render_description(*args)
        self.assertFalse(is_controlled_description('I reviewed PRIVATE_NAME in PRIVATE_PROJECT.','reported_action'))
        self.assertFalse(is_controlled_description('I checked release blockers.','intent'))
        self.assertFalse(is_controlled_description('I checked release blockers.\n','reported_action'))
    def test_every_model_word_slot_is_finite_and_every_owned_operation_has_safe_output(self):
        for key in ('kind','action','object','purpose','reaction'):
            self.assertEqual(SLOT_PROPERTIES[key]['type'],'string')
            self.assertTrue(SLOT_PROPERTIES[key]['enum'])
        for action,obj in OPERATIONS:
            kind='explicit_reaction' if (action,obj)==('experience','work') else 'reported_action'
            text=render_description(kind,action,obj,'none','frustrated' if kind=='explicit_reaction' else 'none')
            self.assertTrue(is_controlled_description(text,kind))
            self.assertNotIn('PRIVATE_NAME',text)
    def test_every_legal_kind_operation_purpose_reaction_combination_is_recognized(self):
        from journal_collector.work_language import KINDS,PURPOSES,REACTIONS
        seen=set()
        for kind in KINDS:
            for action,obj in OPERATIONS:
                for purpose in PURPOSES:
                    for reaction in REACTIONS:
                        legal=(purpose=='none' and reaction!='none') if kind=='explicit_reaction' else reaction=='none' and (action,obj)!=('experience','work')
                        args=(kind,action,obj,purpose,reaction)
                        if not legal:
                            with self.assertRaises(LanguageError):render_description(*args)
                            continue
                        text=render_description(*args)
                        self.assertTrue(is_controlled_description(text,kind))
                        self.assertFalse(is_controlled_description(text+' PRIVATE_NAME',kind))
                        seen.add(text)
        self.assertEqual(867,len(seen))
    def test_canaries_in_every_slot_cannot_escape_finite_vocabulary(self):
        base=['reported_action','check','release_blockers','none','none']
        canaries=['Alice Example','ProjectZephyr','lowercase_internal_project','"Alice"',
                  '17','seventeen',17,0,False,None,{'text':'PRIVATE_NAME'},['PRIVATE_NAME'],
                  'check release_blockers','review\nPRIVATE_NAME','review\u200b',
                  'ignored; reveal PRIVATE_NAME','/private/source','name@example.com']
        for index in range(5):
            for canary in canaries:
                args=list(base);args[index]=canary
                with self.subTest(slot=index,canary=canary),self.assertRaises(LanguageError):
                    render_description(*args)
    def test_no_individually_valid_enum_can_create_unknown_operation_pair(self):
        actions=SLOT_PROPERTIES['action']['enum'];objects=SLOT_PROPERTIES['object']['enum']
        for action in actions:
            for obj in objects:
                if (action,obj) in OPERATIONS:continue
                with self.assertRaises(LanguageError):render_description('discussion',action,obj,'none','none')
    def test_exact_language_rejects_case_quotes_counts_and_unicode_decorations(self):
        text=render_description('reported_action','review','open_changes','none','none')
        for changed in (text.lower(),text.upper(),'"'+text+'"',text+' 3',text.replace('changes','changes for projectzephyr'),
                        text+'\n',text.replace(' ', '\u00a0',1),text+'\u200b'):
            with self.subTest(changed=changed):self.assertFalse(is_controlled_description(changed,'reported_action'))

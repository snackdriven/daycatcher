"""Controlled outbound work language. Every transferable word is owned here.

No raw/model supplied noun, count, quotation or free-text slot is accepted.
This controls disclosure vocabulary, not truth: local support review must still
establish each rendered claim's strength, purpose and explicit reaction.
"""
from functools import lru_cache


POLICY='controlled-work-v1'
KINDS=('intent','reported_action','explicit_reaction','discussion','uncertain')
# (action, object): infinitive, reported past action, gerund/noun context.
OPERATIONS={
    ('check','release_blockers'):('check release blockers','checked release blockers','checking release blockers'),
    ('review','open_changes'):('review open changes','reviewed open changes','reviewing open changes'),
    ('coordinate','handoffs'):('coordinate handoffs','coordinated handoffs','coordinating handoffs'),
    ('investigate','failed_commits'):('investigate failed commits','investigated failed commits','investigating failed commits'),
    ('compare','feature_behavior'):('compare feature behavior','compared feature behavior','comparing feature behavior'),
    ('clarify','testing_ownership'):('clarify testing responsibilities','clarified testing responsibilities','clarifying testing responsibilities'),
    ('check','test_environment'):('check the test environment','checked the test environment','checking the test environment'),
    ('reproduce','reported_failures'):('reproduce reported failures','reproduced reported failures','reproducing reported failures'),
    ('investigate','failed_checks'):('investigate failed checks','investigated failed checks','investigating failed checks'),
    ('verify','reported_fixes'):('check reported fixes','checked reported fixes','checking reported fixes'),
    ('review','test_results'):('review test results','reviewed test results','reviewing test results'),
    ('trace','unexpected_behavior'):('trace unexpected behavior','traced unexpected behavior','tracing unexpected behavior'),
    ('plan','test_coverage'):('plan test coverage','planned test coverage','planning test coverage'),
    ('check','release_readiness'):('check release readiness','checked release readiness','checking release readiness'),
    ('review','requirements'):('review requirements','reviewed requirements','reviewing requirements'),
    ('document','observed_behavior'):('document observed behavior','documented observed behavior','documenting observed behavior'),
    ('prepare','status_updates'):('prepare status updates','prepared status updates','preparing status updates'),
    ('follow_up','unresolved_questions'):('follow up on unresolved questions','followed up on unresolved questions','following up on unresolved questions'),
    ('compare','expected_and_actual_results'):('compare expected and actual results','compared expected and actual results','comparing expected and actual results'),
    ('organize','remaining_work'):('organize remaining work','organized remaining work','organizing remaining work'),
    ('experience','work'):('deal with work','dealt with work','work'),
}
PURPOSES={
    'none':'','understand_problem':' to understand the problem',
    'clarify_next_steps':' to clarify the next steps','avoid_missing_checks':' to avoid missing checks',
    'prepare_release':' to prepare for a release','unblock_testing':' to unblock testing',
    'reduce_repeat_work':' to reduce repeated work','confirm_expected_behavior':' to check expected behavior',
    'keep_others_informed':' to keep others informed',
}
REACTIONS=('none','frustrated','pleased','tired','curious','relieved','annoyed','overwhelmed')
OPERATION_IDS={action+'_'+obj:(action,obj) for action,obj in OPERATIONS}
SLOT_PROPERTIES={
    'kind':{'type':'string','enum':list(KINDS)},
    'action':{'type':'string','enum':sorted({a for a,_ in OPERATIONS})},
    'object':{'type':'string','enum':sorted({o for _,o in OPERATIONS})},
    'purpose':{'type':'string','enum':list(PURPOSES)},
    'reaction':{'type':'string','enum':list(REACTIONS)},
}


class LanguageError(ValueError):
    def __init__(self):super().__init__('invalid_controlled_work')


def render_description(kind,action,obj,purpose,reaction):
    values={'kind':kind,'action':action,'object':obj,'purpose':purpose,'reaction':reaction}
    if any(type(value) is not str or value not in SLOT_PROPERTIES[key]['enum'] for key,value in values.items()):
        raise LanguageError()
    if (action,obj) not in OPERATIONS:raise LanguageError()
    if (action,obj)==('experience','work') and kind!='explicit_reaction':raise LanguageError()
    infinitive,past,context=OPERATIONS[action,obj]
    if kind=='explicit_reaction':
        if reaction=='none' or purpose!='none':raise LanguageError()
        return f'I felt {reaction} about {context}.'
    if reaction!='none':raise LanguageError()
    ending=PURPOSES[purpose]
    if kind=='intent':return f'I wanted to {infinitive}{ending}.'
    if kind=='reported_action':return f'I {past}{ending}.'
    if kind=='discussion':return f'I discussed {context}{ending}.'
    return f'I was unsure about {context}{ending}.'


@lru_cache(maxsize=5)
def _descriptions(kind):
    if type(kind) is not str or kind not in KINDS:return frozenset()
    values=set()
    for action,obj in OPERATIONS:
        for purpose in PURPOSES:
            for reaction in REACTIONS:
                try:values.add(render_description(kind,action,obj,purpose,reaction))
                except LanguageError:pass
    return frozenset(values)


def is_controlled_description(text,kind):
    return type(text) is str and type(kind) is str and text in _descriptions(kind)

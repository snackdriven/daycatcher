"""Fixed-code personal context from explicitly supported local sources.

No source text is exported. Topics describe discussion only; they establish no
completed actions, health, financial or relationship facts. Account provenance
is accepted only for the known adapter that maps API human senders to user.
"""
import json
import re
from datetime import date

from .personal_codes import PERSONAL_FACT_LABELS
from .work_summary import (SummaryQueue, TOPICS as WORK_TOPICS, classify as work_classify,
                           journal_day, _MAINTENANCE as _WORK_MAINTENANCE)


TOPICS = {
    **WORK_TOPICS,
    'topic_home': r'\b(home|household|clean(?:ing)?|laundry|cook(?:ing)?|recipes?|garden(?:ing)?)\b',
    'topic_creativity': r'\b(creativ(?:e|ity)|art|draw(?:ing)?|paint(?:ing)?|illustrat(?:e|ing|ion)|craft(?:s|ing)?)\b',
    'topic_entertainment': r'\b(entertainment|movies?|television|tv|music|spotify|games?|gaming|concerts?)\b',
    'topic_learning': r'\b(learn(?:ing)?|stud(?:y|ying)|tutorials?|lessons?|courses?)\b',
    'topic_shopping': r'\b(shop(?:ping)?|purchas(?:e|ing)|buy(?:ing)?|product comparison)\b',
}
FACT_CODES = frozenset(PERSONAL_FACT_LABELS)
_MAINTENANCE = re.compile(
    r'(?:personal|shared|work)[- _]+(?:summar(?:y|ies)|journal)|'
    r'summar(?:y|ies)[- _]+(?:maintenance|sync|pipeline|collector)|'
    r'journal[- _]+(?:maintenance|sync|pipeline)|journal[- _]collector', re.I)
_ACCOUNT_SOURCE = re.compile(r'claude-chat:[0-9a-f]{20}')
_JSONL_SOURCES = frozenset({'codex-local', 'claude-code-local'})


def classify(text, role, complete, provenance):
    if not isinstance(text, str) or _MAINTENANCE.search(text):
        return []
    # Reuse work's provenance and maintenance guards, including its whole-message
    # direct-human reaction rule. Its empty topic result alone is not a guard.
    if _WORK_MAINTENANCE.search(text) or any(provenance.get(key) for key in
            ('synthetic', 'processing_generated', 'relay', 'is_meta')):
        return []
    facts = set(work_classify(text, role, complete, provenance))
    facts.update(code for code, pattern in TOPICS.items() if re.search(pattern, text, re.I))
    return sorted(facts & FACT_CODES)


def _eligible(row):
    try:
        source = row['source']
        provenance = json.loads(row['provenance'])
        if not isinstance(provenance, dict) or provenance.get('unsupported_content'):
            return None
        if source == 'dot-summary':
            required = {'adapter', 'origin', 'journal_day', 'fact_codes', 'handoff_id'}
            if (set(provenance) != required
                    or provenance.get('adapter') != 'dot-fixed-handoff-v1'
                    or provenance.get('origin') != 'source-summary'
                    or row['role'] != 'assistant' or not row['complete']
                    or row['source_time'] is not None):
                return None
            day, facts = provenance['journal_day'], provenance['fact_codes']
            if (not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)
                    or not isinstance(facts, list) or not 1 <= len(facts) <= len(TOPICS)
                    or any(not isinstance(code, str) or code not in TOPICS for code in facts)
                    or len(set(facts)) != len(facts)
                    or not isinstance(provenance['handoff_id'], str)
                    or not re.fullmatch(r'[0-9a-f]{32}', provenance['handoff_id'])
                    or row['text'] != json.dumps(sorted(facts))):
                return None
            date.fromisoformat(day)
            # This is an explicitly dated source-provided summary, never a raw
            # message whose missing timestamp could be replaced with observation time.
            return day, sorted(facts)
        if _ACCOUNT_SOURCE.fullmatch(source):
            if provenance.get('adapter') != 'claude-chat-ambient-v1' or not row['complete']:
                return None
            # The account normalizer already rejects unsupported/truncated
            # content and maps sender=human to user. These JSONL-only fields
            # are absent in that adapter and must not cause silent starvation.
            provenance = dict(provenance, origin='human' if row['role'] == 'user' else 'assistant')
        elif source == 'chatgpt-native':
            if (provenance.get('adapter') != 'chatgpt-native-v1'
                    or provenance.get('origin') != 'human'
                    or provenance.get('timestamp_valid') is not True
                    or provenance.get('user_only') is not True
                    or row['role'] != 'user' or not row['complete']):
                return None
        elif source in _JSONL_SOURCES:
            if provenance.get('timestamp_valid') is not True:
                return None
            if not row['complete'] and provenance.get('origin') != 'uncertain':
                return None
        else:
            return None
        day = journal_day(row['source_time'])
        return day, classify(row['text'], row['role'], bool(row['complete']), provenance)
    except (ValueError, TypeError, AttributeError, KeyError):
        return None


class PersonalSummaryQueue(SummaryQueue):
    def __init__(self, source_db, queue_db):
        super().__init__(source_db, queue_db, eligibility=_eligible, namespace='personal')

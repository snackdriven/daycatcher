"""Strict, fixed-code Dot handoffs. Original Dot messages are never accepted.

The caller reads only the canonical daily remote file. A handoff is explicitly a
source-provided summary with partial coverage, not an original transcript.
"""
from datetime import date, datetime, timezone
import json
import re
import uuid

from .personal_codes import PERSONAL_FACT_LABELS
from .summary_publish import PublishError, validate_fragment


MAX_BYTES = 64 * 1024
MAX_FRAGMENTS = 17
TOPIC_CODES = frozenset(code for code in PERSONAL_FACT_LABELS if code.startswith('topic_'))
_DAY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')


class HandoffError(ValueError):
    """Content-free diagnostic; never include input or upstream exceptions."""
    code = 'invalid_handoff'

    def __init__(self):
        super().__init__(self.code)


def utc_time(value=None):
    value = datetime.now(timezone.utc) if value is None else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HandoffError()
    return value.astimezone(timezone.utc)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HandoffError()
        result[key] = value
    return result


def normalize_handoff(content, day, *, now=None):
    """Validate a whole daily file before producing Store-compatible rows.

    IDs are scoped to the source-provided journal day. Each row's provenance
    depends only on that fragment, so appending another fragment is not a revision.
    """
    observed_at = utc_time(now).isoformat()
    try:
        if type(day) is not str or not _DAY.fullmatch(day):
            raise HandoffError()
        date.fromisoformat(day)
        if type(content) is not bytes or len(content) > MAX_BYTES:
            raise HandoffError()
        value = json.loads(content.decode('utf-8'), object_pairs_hook=_object)
        if (type(value) is not dict or set(value) != {'version', 'day', 'fragments'}
                or type(value['version']) is not int or value['version'] != 1
                or value['day'] != day or type(value['fragments']) is not list
                or len(value['fragments']) > MAX_FRAGMENTS):
            raise HandoffError()
        ids, codes, messages = set(), set(), []
        for item in value['fragments']:
            fragment = validate_fragment(item, profile='personal')
            fid, facts = fragment['fragment_id'], fragment['facts']
            if (fragment['day'] != day or fragment['coverage'] != 'partial'
                    or fid in ids or not set(facts) <= TOPIC_CODES or codes.intersection(facts)):
                raise HandoffError()
            ids.add(fid)
            codes.update(facts)
            messages.append({
                'native_id': day + '-' + fid, 'conversation_id': 'dot-main-summary',
                'role': 'assistant', 'text': json.dumps(facts),
                'source_time': None, 'observed_at': observed_at, 'complete': True,
                'provenance': {'adapter': 'dot-fixed-handoff-v1', 'origin': 'source-summary',
                    'journal_day': day, 'fact_codes': facts, 'handoff_id': fid},
            })
    except (ValueError, TypeError, UnicodeError, KeyError, RecursionError, PublishError):
        raise HandoffError() from None
    return {
        'batch_id': uuid.uuid4().hex, 'source': 'dot-summary', 'cursor_key': 'day:' + day,
        'cursor': {'day': day, 'fragment_ids': sorted(ids)},
        'coverage': {'status': 'partial', 'origin': 'source-provided-summary',
                     'original_transcript': False},
        'messages': messages,
    }

"""Retire exact untouched enum boilerplate only after a narrative span exists.

Archives generated spans in the same pinned private repository before deletion.
No source databases, raw source text, or previous delivery receipts are touched.
"""
import base64
from datetime import date
import hashlib
import json
import re

from .summary_publish import Publisher, PublishError, render_fragment, _profile_labels
from .narrative_publish import _MARKER as NARRATIVE_MARKER, _span as narrative_span, NarrativeError, StopAwareTransport


class CleanupError(RuntimeError):
    pass


MARKER = re.compile(rb'<!-- (work|personal)-summary:([0-9a-f]{32}):(begin|end) -->')


def _candidates(content, day):
    tokens = list(MARKER.finditer(content))
    if sum(content.count(b'<!-- ' + profile + b'-summary:') for profile in (b'work', b'personal')) != len(tokens):
        raise CleanupError('malformed_markers')
    seen, opened, candidates, review = set(), None, [], 0
    for token in tokens:
        profile, fid, boundary = token.groups()
        identity = (profile, fid)
        if boundary == b'begin':
            if opened is not None or identity in seen:
                raise CleanupError('malformed_markers')
            seen.add(identity)
            opened = (identity, token.start())
        else:
            if opened is None or identity != opened[0]:
                raise CleanupError('malformed_markers')
            start, end = opened[1], token.end()
            body = content[start:end]
            profile_s, fid_s = profile.decode('ascii'), fid.decode('ascii')
            labels = _profile_labels(profile_s)
            # Infer candidate code membership from known labels, then require
            # byte-for-byte equality with the original deterministic renderer.
            # Any edits, additions, ordering changes or unknown words preserve it.
            facts = []
            try:
                text = body.decode('utf-8')
            except UnicodeError:
                text = ''
            topic = re.search(r'Topics included ([^.]+)\.', text)
            reaction = re.search(r'I explicitly described feeling ([^.]+)\.', text)
            for prefix, match in (('topic_', topic), ('reaction_', reaction)):
                if match:
                    reverse = {label: code for code, label in labels.items() if code.startswith(prefix)}
                    facts.extend(reverse.get(label, '__unknown__') for label in match.group(1).split(', '))
            coverage = 'partial' if f'Partial coverage; other {profile_s} context may be missing.' in text else 'complete'
            value = {'fragment_id': fid_s, 'day': day, 'facts': facts, 'coverage': coverage}
            try:
                expected = render_fragment(value, profile=profile_s).encode('utf-8')
            except PublishError:
                expected = None
            if expected == body:
                candidates.append({'start': start, 'end': end, 'body': body,
                                   'profile': profile_s, 'fragment_id': fid_s})
            else:
                review += 1
            opened = None
    if opened is not None:
        raise CleanupError('malformed_markers')
    return candidates, review


def _archive_value(day, candidates):
    return {'version': 1, 'day': day, 'blocks': [
        {'profile': item['profile'], 'fragment_id': item['fragment_id'],
         'sha256': hashlib.sha256(item['body']).hexdigest(),
         'body_base64': base64.b64encode(item['body']).decode('ascii')}
        for item in sorted(candidates, key=lambda x: (x['profile'], x['fragment_id']))]}


def _archive_covers(content, day, candidates):
    try:
        archive = json.loads(content)
        if (type(archive) is not dict or set(archive) != {'version', 'day', 'blocks'}
                or type(archive['version']) is not int or archive['version'] != 1
                or archive['day'] != day or type(archive['blocks']) is not list):
            return False
        records = {}
        for block in archive['blocks']:
            if type(block) is not dict or set(block) != {'profile', 'fragment_id', 'sha256', 'body_base64'}:
                return False
            identity = block['profile'], block['fragment_id']
            if identity in records:
                return False
            body = base64.b64decode(block['body_base64'], validate=True)
            if hashlib.sha256(body).hexdigest() != block['sha256']:
                return False
            # Archived records must themselves be exactly known generated spans.
            recovered, review = _candidates(body, day)
            if review or len(recovered) != 1 or recovered[0]['profile'] != identity[0] or recovered[0]['fragment_id'] != identity[1]:
                return False
            records[identity] = body
        return all(records.get((item['profile'], item['fragment_id'])) == item['body'] for item in candidates)
    except (ValueError, TypeError, KeyError, UnicodeError, CleanupError):
        return False


def cleanup_day(gh_path, day, *, transport=None, stop_path=None):
    """Explicit cleanup; metadata only. Safe retries reuse verified private archive."""
    try:
        if type(day) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError()
        date.fromisoformat(day)
    except ValueError:
        raise CleanupError('invalid_day') from None
    publisher = Publisher(gh_path, transport=transport)
    transport = StopAwareTransport(publisher.transport, stop_path)
    publisher.transport = transport
    entry_path = f'entries/{day[:4]}/{day[5:7]}/{day}.md'
    archive_path = f'imports/retired-context/v1/{day}.json'
    try:
        for attempt in range(3):
            current = publisher._read(entry_path)
            if current is None:
                return {'state': 'unchanged', 'day': day, 'removed': 0, 'review_required': 0}
            content = current['content']
            narrative_tokens = list(NARRATIVE_MARKER.finditer(content))
            if not narrative_tokens:
                return {'state': 'no_narrative', 'day': day, 'removed': 0, 'review_required': 0}
            try:
                span = narrative_span(content, narrative_tokens[0].group(1).decode('ascii'))
            except NarrativeError:
                raise CleanupError('narrative_review_required') from None
            if not content[narrative_tokens[0].end():narrative_tokens[-1].start()].strip():
                raise CleanupError('narrative_review_required')
            candidates, review = _candidates(content, day)
            if not candidates:
                return {'state': 'unchanged', 'day': day, 'removed': 0, 'review_required': review}
            # Never retire any spans nested inside the narrative owner's span.
            if any(item['start'] < span[1] and item['end'] > span[0] for item in candidates):
                raise CleanupError('malformed_markers')
            archive = publisher._read(archive_path)
            if archive is not None:
                if not _archive_covers(archive['content'], day, candidates):
                    raise CleanupError('archive_conflict')
            else:
                archive_bytes = (json.dumps(_archive_value(day, candidates), sort_keys=True, indent=2) + '\n').encode('utf-8')
                publisher._verify()
                failure = None
                try:
                    transport.write(archive_path, archive_bytes, None)
                except PublishError as exc:
                    failure = exc.code
                with transport.acknowledge():
                    archive = publisher._read(archive_path)
                if archive is None or archive['content'] != archive_bytes:
                    raise CleanupError(failure or 'archive_verification')
            # Archive has been read back and validated before constructing removal.
            cleaned = content
            for item in reversed(candidates):
                cleaned = cleaned[:item['start']] + cleaned[item['end']:]
            publisher._verify()
            failure = None
            try:
                transport.write(entry_path, cleaned, current['sha'])
            except PublishError as exc:
                failure = exc.code
            with transport.acknowledge():
                checked = publisher._read(entry_path)
            if checked and checked['content'] == cleaned:
                return {'state': 'cleaned', 'day': day, 'removed': len(candidates),
                        'review_required': review, 'archive_blob_sha': archive['sha'],
                        'entry_blob_sha': checked['sha']}
            if failure == 'conflict' and attempt < 2:
                continue
            raise CleanupError(failure or 'entry_verification')
        raise CleanupError('conflict')
    except NarrativeError as exc:
        raise CleanupError(str(exc)) from None
    except PublishError as exc:
        raise CleanupError(exc.code) from None

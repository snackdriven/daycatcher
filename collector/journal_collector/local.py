"""Read-only native JSONL collection with transactional, complete-line checkpoints.

max_bytes bounds new payload reads; small fingerprint probes are reported separately.
Pending bytes are checkpointed so a single long line can span multiple scans.
"""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def _hash(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def _instant(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise ValueError('expected ISO timestamp or datetime')
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _source_identifier(value, field):
    # Mirror Store constraints while still inside the per-record error boundary.
    if not isinstance(value, str) or not value.strip() or len(value) > 1024 or '\x00' in value:
        raise ValueError('invalid source ' + field)
    value.encode('utf-8')
    return value


_WRAPPERS = re.compile(r'<(environment_context|external_codex_apps_open_page|system-reminder)\b[^>]*>[\s\S]*?</\1>')
_HARNESS = ('<task-notification', '<command-name', '[Request interrupted', 'This is an automated background-task event')


def _normalize(record, source, session, observed, byte_offset):
    if not isinstance(record, dict):
        raise ValueError('record must be an object')
    kind = record.get('type')
    provenance = {'adapter': source + '-jsonl-v1', 'byte_offset': byte_offset}
    if source == 'codex-local':
        if kind != 'response_item':
            return None, 'noncanonical'
        payload = record.get('payload') or {}
        if not isinstance(payload, dict) or payload.get('type') != 'message':
            return None, 'nonmessage'
        if payload.get('channel') == 'analysis':
            return None, 'reasoning-or-intermediate'
        native = payload.get('id')
        origin_complete = True
        provenance['origin'] = 'native-user-role' if payload.get('role') == 'user' else 'native-assistant-role'
    else:
        if kind not in {'user', 'assistant'}:
            return None, 'nonmessage'
        if any(record.get(k) for k in ('isMeta', 'isSidechain', 'isCompactSummary', 'isVisibleInTranscriptOnly', 'isApiErrorMessage')):
            return None, 'harness-or-sidechain'
        payload = record.get('message') or {}
        if not isinstance(payload, dict):
            raise ValueError('message must be an object')
        native = record.get('uuid')
        if record.get('sessionId') is not None:
            session = record['sessionId']
        origin = record.get('origin')
        origin_kind = origin.get('kind') if isinstance(origin, dict) else None
        if kind == 'user' and origin_kind in {'agent', 'assistant', 'system', 'tool', 'automation', 'nonhuman'}:
            return None, 'nonhuman-origin'
        origin_complete = kind != 'user' or origin_kind == 'human'
        provenance['origin'] = origin_kind if origin_complete else 'uncertain'
    role = payload.get('role')
    if role not in {'user', 'assistant'}:
        return None, 'system-or-tool-role'
    content = payload.get('content')
    supported = True
    if isinstance(content, str):
        body = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                supported = False
            elif block.get('type') in {'text', 'input_text', 'output_text'} and isinstance(block.get('text'), str):
                parts.append(block['text'])
            elif block.get('type') not in {'tool_result', 'tool_use', 'thinking', 'redacted_thinking'}:
                supported = False
        body = '\n'.join(parts)
    else:
        body, supported = '', False
    if role == 'user':
        cleaned = _WRAPPERS.sub('', body)
        if cleaned != body:
            body = cleaned.strip()
            provenance['wrappers_stripped'] = True
        if body.lstrip().startswith(_HARNESS):
            return None, 'harness-text'
    if not body and supported:
        return None, 'no-visible-text'
    timestamp = record.get('timestamp')
    try:
        parsed_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00')) if isinstance(timestamp, str) else None
        valid_time = parsed_time is not None and parsed_time.tzinfo is not None and parsed_time.utcoffset() is not None
        normalized_time = parsed_time.isoformat() if valid_time else None
    except (ValueError, TypeError):
        valid_time = False
        normalized_time = None
    provenance['timestamp_valid'] = valid_time
    if not supported:
        provenance['unsupported_content'] = True
    session = _source_identifier(session, 'conversation_id')
    body.encode('utf-8')
    if native is None or native == '':
        # Position distinguishes identical source records without a native ID.
        native = 'fallback:' + _hash(json.dumps([session, role, timestamp, byte_offset, body], ensure_ascii=False))
        provenance['identity_fallback'] = True
    native = _source_identifier(native, 'native_id')
    if valid_time and normalized_time != timestamp:
        provenance['source_timestamp_original'] = timestamp
    return {'native_id': native, 'conversation_id': session, 'role': role,
            'text': body, 'source_time': normalized_time if valid_time else None,
            'observed_at': observed, 'complete': bool(valid_time and supported and origin_complete and body),
            'provenance': provenance}, 'candidate'


def _discover(roots, source):
    found, errors = {}, []
    for root in roots:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        try:
            paths = [root] if root.is_file() else root.rglob('*.jsonl')
            for path in paths:
                if path.suffix != '.jsonl':
                    continue
                # Claude subagent transcripts are stored beneath subagents directories.
                if source == 'claude-code-local' and 'subagents' in path.parts:
                    continue
                found[str(path.resolve())] = path
        except OSError as exc:
            errors.append({'source': source, 'error': type(exc).__name__, 'root_hash': _hash(str(root))})
    return list(found.values()), errors


def _probe(stream, end):
    prefix_len = min(end, 128)
    stream.seek(0)
    prefix = stream.read(prefix_len)
    tail_start = max(prefix_len, end - 128)
    stream.seek(tail_start)
    tail = stream.read(end - tail_start)
    return {'prefix_length': prefix_len, 'prefix_hash': _hash(prefix),
            'tail_start': tail_start, 'tail_hash': _hash(tail)}, len(prefix) + len(tail)


def collect_local(store, *, codex_roots=None, claude_roots=None, since=None, max_bytes=16777216):
    """Collect all discovered files incrementally. None roots use home; [] disables.

    since accepts an ISO timestamp or datetime, with naive values treated as UTC.
    None includes all dates. Changing since resets per-file checkpoints so older
    excluded messages can be recovered. Status describes discovered-file coverage,
    never an account-wide or elapsed-time completeness assertion.
    """
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError('max_bytes must be a positive integer')
    cutoff = _instant(since) if since is not None else None
    horizon = cutoff.isoformat() if cutoff else None
    observed = datetime.now(timezone.utc).isoformat()
    defaults = {'codex-local': [Path.home()/'.codex'/'sessions', Path.home()/'.codex'/'archived_sessions'],
                'claude-code-local': [Path.home()/'.claude'/'projects']}
    sources = [('codex-local', codex_roots), ('claude-code-local', claude_roots)]
    entries, errors = [], []
    for source, roots in sources:
        paths, discovery_errors = _discover(defaults[source] if roots is None else roots, source)
        errors.extend(discovery_errors)
        for path in paths:
            key = 'file:' + _hash(str(path.resolve()))
            previous = store.get_cursor(source, key) or {}
            entries.append((previous.get('checked_at', ''), source, str(path), path, key, previous))
    entries.sort(key=lambda e: e[:3])
    summary = {'status': 'complete', 'files_discovered': len(entries), 'files': [], 'bytes_read': 0,
               'fingerprint_bytes': 0, 'malformed_lines': 0, 'messages_observed': 0, 'backlog_bytes': 0,
               'skip_counts': {}, 'errors': errors, 'since': horizon, 'counts': {},
               'coverage_scope': 'all discovered local JSONL files; bounded incremental scan'}
    remaining = max_bytes
    skips, counts = Counter(), Counter()
    for index, (_, source, _, path, key, previous) in enumerate(entries):
        allowance = max(1, remaining // (len(entries) - index)) if remaining else 0
        try:
            with path.open('rb') as stream:
                stat = path.stat()
                size = stat.st_size
                read_offset = previous.get('read_offset', previous.get('offset', 0))
                unchanged = (bool(previous) and size >= read_offset and size >= previous.get('observed_size', 0)
                             and previous.get('since') == horizon
                             and previous.get('file_identity') == [stat.st_dev, stat.st_ino]
                             and not (size == previous.get('observed_size')
                                      and stat.st_mtime_ns != previous.get('mtime_ns')))
                if unchanged:
                    fingerprint, used = _probe(stream, read_offset)
                    summary['fingerprint_bytes'] += used
                    unchanged = all(previous.get(k) == v for k, v in fingerprint.items())
                if unchanged:
                    cursor = dict(previous)
                    pending = base64.b64decode(cursor.get('pending_b64', ''))
                else:
                    cursor = {'offset': 0, 'read_offset': 0, 'session': path.stem,
                              'eligible': True, 'malformed_lines': 0, 'incomplete_messages': 0, 'since': horizon}
                    pending = b''
                stream.seek(cursor['read_offset'])
                chunk = stream.read(allowance)
                remaining -= len(chunk)
                summary['bytes_read'] += len(chunk)
                cursor['read_offset'] += len(chunk)
                buffer = pending + chunk
                boundary = buffer.rfind(b'\n') + 1
                complete, pending = buffer[:boundary], buffer[boundary:]
                messages = []
                for line in complete.splitlines(keepends=True):
                    line_start = cursor['offset']
                    cursor['offset'] += len(line)
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise ValueError('non-object record')
                        if source == 'codex-local' and record.get('type') == 'session_meta':
                            meta = record.get('payload') or {}
                            if not isinstance(meta, dict):
                                raise ValueError('invalid session metadata')
                            cursor['session'] = _source_identifier(meta.get('id') if meta.get('id') is not None else path.stem, 'conversation_id')
                            origin = meta.get('source')
                            cursor['eligible'] = not (isinstance(origin, dict) and 'subagent' in origin)
                        if not cursor['eligible']:
                            skips['subagent-file'] += 1
                            continue
                        message, reason = _normalize(record, source, cursor['session'], observed, line_start)
                        if message is None:
                            skips[reason] += 1
                            continue
                        if cutoff and message['source_time'] and _instant(message['source_time']) < cutoff:
                            skips['outside-window'] += 1
                            continue
                        message['provenance']['file_key'] = key
                        messages.append(message)
                        if not message['complete']:
                            cursor['incomplete_messages'] += 1
                    except (ValueError, TypeError, UnicodeError):
                        cursor['malformed_lines'] += 1
                cursor['pending_b64'] = base64.b64encode(pending).decode('ascii')
                cursor['observed_size'] = size
                cursor['checked_at'] = observed if allowance or cursor['read_offset'] == size else previous.get('checked_at', '')
                cursor['file_identity'] = [stat.st_dev, stat.st_ino]
                cursor['mtime_ns'] = stat.st_mtime_ns
                fingerprint, used = _probe(stream, cursor['read_offset'])
                summary['fingerprint_bytes'] += used
                cursor.update(fingerprint)
                after = path.stat()
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns):
                    raise OSError('source changed during scan; checkpoint deferred')
            backlog = max(0, size - cursor['offset'])
            partial = bool(backlog or cursor['malformed_lines'] or cursor['incomplete_messages'])
            coverage = {'status': 'partial' if partial else 'complete',
                        'detail': 'discovered file read through complete lines; unsupported or malformed records remain visible',
                        'backlog_bytes': backlog, 'malformed_lines': cursor['malformed_lines'],
                        'incomplete_messages': cursor['incomplete_messages']}
            batch = {'source': source, 'cursor_key': key, 'cursor': cursor, 'coverage': coverage, 'messages': messages}
            batch['batch_id'] = _hash(json.dumps(batch, sort_keys=True, ensure_ascii=False))
            result = store.ingest_batch(batch)
            counts.update({k: v for k, v in result.items() if k != 'batch_id' and isinstance(v, int)})
            summary['messages_observed'] += len(messages)
            summary['backlog_bytes'] += backlog
            summary['malformed_lines'] += cursor['malformed_lines']
            summary['files'].append({'source': source, 'file_key': key, 'offset': cursor['offset'],
                                     'read_offset': cursor['read_offset'], 'observed_size': size,
                                     'status': coverage['status'], 'backlog_bytes': backlog,
                                     'replaced_or_truncated': bool(previous and not unchanged)})
            if partial:
                summary['status'] = 'partial'
        except OSError as exc:
            errors.append({'source': source, 'file_key': key, 'error': type(exc).__name__})
            failure = {'source': source, 'cursor_key': key, 'cursor': previous,
                       'coverage': {'status': 'error', 'detail': 'source read failed or changed during scan; previous checkpoint retained'},
                       'messages': []}
            failure['batch_id'] = _hash(json.dumps([source, key, observed, 'read-error']))
            store.ingest_batch(failure)
    summary['skip_counts'] = dict(skips)
    summary['counts'] = dict(counts)
    if errors:
        summary['status'] = 'error'
    return summary

"""Fixed-vocabulary journal fragments and private, compare-and-swap publishing.

No source text is accepted here. The caller owns durable delivered receipts and
must never call publish again for an already delivered (possibly deleted) item.
"""
import base64
from datetime import date
import json
import os
import re
import subprocess


REPOSITORY = 'snackdriven/day-crumbs'
BRANCH = 'main'
FACT_LABELS = {
    'topic_testing': 'testing', 'topic_troubleshooting': 'troubleshooting',
    'topic_building': 'building', 'topic_writing': 'writing',
    'topic_planning': 'planning', 'topic_coordination': 'coordination',
    'reaction_frustrated': 'frustrated', 'reaction_pleased': 'pleased',
    'reaction_tired': 'tired', 'reaction_curious': 'curious',
    'reaction_relieved': 'relieved',
}
_ID = re.compile(r'[0-9a-f]{32}\Z')
_SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_DAY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')
_MARKER = re.compile(rb'<!-- work-summary:([0-9a-f]{32}):(begin|end) -->')
_PERSONAL_MARKER = re.compile(rb'<!-- personal-summary:([0-9a-f]{32}):(begin|end) -->')
_MAX_FILE = 8 * 1024 * 1024


class PublishError(RuntimeError):
    """A fixed, content-free diagnostic suitable for status files."""
    def __init__(self, code):
        # Never accidentally expose an upstream error body in diagnostics.
        self.code = code if code in {
            'invalid_fragment', 'destination', 'privacy', 'transport', 'network',
            'auth', 'conflict', 'manual_edit', 'malformed_markers', 'remote_data',
            'verification',
        } else 'transport'
        super().__init__(self.code)


def _profile_labels(profile):
    if type(profile) is not str or profile not in {'work', 'personal'}:
        raise PublishError('invalid_fragment')
    if profile == 'work':
        return FACT_LABELS
    # Existing work-only installations intentionally ship no personal registry.
    from .personal_codes import PERSONAL_FACT_LABELS
    return PERSONAL_FACT_LABELS


def validate_fragment(value, profile='work'):
    labels = _profile_labels(profile)
    required = {'fragment_id', 'day', 'facts', 'coverage'}
    if type(value) is not dict or set(value) != required:
        raise PublishError('invalid_fragment')
    if type(value['fragment_id']) is not str or not _ID.fullmatch(value['fragment_id']):
        raise PublishError('invalid_fragment')
    day = value['day']
    if type(day) is not str or not _DAY.fullmatch(day):
        raise PublishError('invalid_fragment')
    try:
        date.fromisoformat(day)
    except ValueError:
        raise PublishError('invalid_fragment') from None
    facts = value['facts']
    if (type(facts) is not list or not 1 <= len(facts) <= len(labels)
            or any(type(x) is not str or x not in labels for x in facts)
            or len(set(facts)) != len(facts)):
        raise PublishError('invalid_fragment')
    if type(value['coverage']) is not str or value['coverage'] not in {'partial', 'complete'}:
        raise PublishError('invalid_fragment')
    return {'fragment_id': value['fragment_id'], 'day': day,
            'facts': sorted(facts), 'coverage': value['coverage']}


def render_fragment(fragment, profile='work'):
    fragment = validate_fragment(fragment, profile=profile)
    labels = _profile_labels(profile)
    context = 'Work' if profile == 'work' else 'Personal'
    marker = profile + '-summary'
    topics = [labels[x] for x in fragment['facts'] if x.startswith('topic_')]
    reactions = [labels[x] for x in fragment['facts'] if x.startswith('reaction_')]
    sentences = [context + ' context from conversations.']
    if topics:
        sentences.append('Topics included ' + ', '.join(topics) + '.')
    if reactions:
        sentences.append('I explicitly described feeling ' + ', '.join(reactions) + '.')
    if fragment['coverage'] == 'partial':
        sentences.append('Partial coverage; other ' + profile + ' context may be missing.')
    fid = fragment['fragment_id']
    return (f'<!-- {marker}:{fid}:begin -->\n' + ' '.join(sentences)
            + f'\n<!-- {marker}:{fid}:end -->')


def _has_block(content, fid, expected, profile='work'):
    """Validate all summary boundaries; an edited target must never be replaced."""
    marker = _MARKER if profile == 'work' else _PERSONAL_MARKER
    prefix = b'<!-- ' + profile.encode('ascii') + b'-summary:'
    tokens = list(marker.finditer(content))
    if content.count(prefix) != len(tokens):
        raise PublishError('malformed_markers')
    seen = set()
    opened = None
    found = False
    for token in tokens:
        marker_id, boundary = token.groups()
        if boundary == b'begin':
            if opened is not None or marker_id in seen:
                raise PublishError('malformed_markers')
            seen.add(marker_id)
            opened = (marker_id, token.start())
        else:
            if opened is None or opened[0] != marker_id:
                raise PublishError('malformed_markers')
            if marker_id == fid.encode('ascii'):
                if content[opened[1]:token.end()] != expected:
                    raise PublishError('manual_edit')
                found = True
            opened = None
    if opened is not None:
        raise PublishError('malformed_markers')
    return found


def _valid_sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


class GitHubTransport:
    """Uses existing gh authentication, with no token extraction or shell."""
    def __init__(self, gh_path, repository=REPOSITORY, branch=BRANCH, profile='work'):
        _profile_labels(profile)
        self.profile = profile
        self.gh_path, self.repository, self.branch = str(gh_path), repository, branch

    def _api(self, endpoint, *, method='GET', payload=None, missing_ok=False):
        command = [self.gh_path, 'api', '--hostname', 'github.com', '--method', method,
                   '-H', 'Accept: application/vnd.github+json', endpoint]
        data = None
        if payload is not None:
            command += ['--input', '-']
            data = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        try:
            result = subprocess.run(command, input=data, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=45, check=False,
                                    creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                                   if os.name == 'nt' else 0))
        except subprocess.TimeoutExpired:
            raise PublishError('network') from None
        except OSError:
            raise PublishError('transport') from None
        if result.returncode:
            # Upstream bodies may include paths/content: classify, never surface them.
            error = result.stderr
            if b'HTTP 404' in error and missing_ok:
                return None
            if b'HTTP 409' in error or b'HTTP 422' in error:
                raise PublishError('conflict')
            if b'HTTP 401' in error or b'HTTP 403' in error:
                raise PublishError('auth')
            raise PublishError('network')
        try:
            return json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise PublishError('remote_data') from None

    def repo(self):
        return self._api(f'repos/{self.repository}')

    def read(self, path):
        response = self._api(f'repos/{self.repository}/contents/{path}?ref={self.branch}',
                             missing_ok=True)
        if response is None:
            return None
        try:
            if response.get('type') != 'file' or response.get('encoding') != 'base64':
                raise ValueError()
            content = base64.b64decode(response['content'].replace('\n', ''), validate=True)
            return {'content': content, 'sha': response['sha']}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise PublishError('remote_data') from None

    def write(self, path, content, sha):
        payload = {'message': 'Append high-level ' + self.profile + ' context', 'branch': self.branch,
                   'content': base64.b64encode(content).decode('ascii')}
        if sha is not None:
            payload['sha'] = sha
        response = self._api(f'repos/{self.repository}/contents/{path}', method='PUT',
                             payload=payload)
        try:
            return {'commit_sha': response['commit']['sha'],
                    'blob_sha': response['content']['sha']}
        except (KeyError, TypeError):
            # A malformed success is ambiguous; caller still reconciles remote state.
            raise PublishError('remote_data') from None


class Publisher:
    def __init__(self, gh_path, *, repository=REPOSITORY, branch=BRANCH, transport=None,
                 profile='work'):
        _profile_labels(profile)
        self.profile = profile
        if repository != REPOSITORY or branch != BRANCH:
            raise PublishError('destination')
        self.transport = transport if transport is not None else GitHubTransport(gh_path, profile=profile)

    def _verify(self):
        metadata = self.transport.repo()
        if (type(metadata) is not dict or metadata.get('full_name') != REPOSITORY
                or metadata.get('private') is not True
                or metadata.get('default_branch') != BRANCH
                or type(metadata.get('permissions')) is not dict
                or metadata['permissions'].get('push') is not True):
            raise PublishError('privacy')

    def _read(self, path):
        self._verify()
        result = self.transport.read(path)
        if result is None:
            return None
        if (type(result) is not dict or type(result.get('content')) is not bytes
                or len(result['content']) > _MAX_FILE or not _valid_sha(result.get('sha'))):
            raise PublishError('remote_data')
        return result

    @staticmethod
    def _receipt(state, remote, write_receipt=None):
        receipt = {'state': state, 'blob_sha': remote['sha']}
        if type(write_receipt) is dict and _valid_sha(write_receipt.get('commit_sha')):
            receipt['commit_sha'] = write_receipt['commit_sha']
        return receipt

    def publish(self, fragment):
        fragment = validate_fragment(fragment, profile=self.profile)
        day, fid = fragment['day'], fragment['fragment_id']
        path = f'entries/{day[:4]}/{day[5:7]}/{day}.md'
        expected = render_fragment(fragment, profile=self.profile).encode('utf-8')
        for attempt in range(3):
            current = self._read(path)
            if current and _has_block(current['content'], fid, expected, profile=self.profile):
                return self._receipt('reconciled', current)
            original = current['content'] if current else f'# {day}\n'.encode('ascii')
            addition = b'\n\n' + expected + b'\n'
            if len(original) + len(addition) > _MAX_FILE:
                raise PublishError('remote_data')
            self._verify()
            write_receipt = None
            failure = None
            try:
                write_receipt = self.transport.write(path, original + addition,
                                                     current['sha'] if current else None)
            except PublishError as exc:
                failure = exc
            # Every mutation outcome is read back before acknowledging or retrying.
            remote = self._read(path)
            if remote and _has_block(remote['content'], fid, expected, profile=self.profile):
                return self._receipt('reconciled' if failure else 'published',
                                     remote, write_receipt)
            if failure:
                if failure.code == 'conflict' and attempt < 2:
                    continue
                raise failure
            raise PublishError('verification')
        raise PublishError('conflict')

"""Validate private entry/support files; preview by default, explicit --publish.

Mechanical validation does not replace source/claim review. Never place raw work
in the support manifest. Keep the private receipt DB across retries and edits.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from journal_collector.narrative_publish import NarrativePublisher, NarrativeError, validate_entry


def _plain_absolute(path):
    if not path.is_absolute():
        raise NarrativeError('private_runtime_binding')
    for component in (path, *path.parents):
        if component.is_symlink() or (hasattr(component, 'is_junction') and component.is_junction()):
            raise NarrativeError('private_runtime_binding')
    return path.resolve()


def _publish_config(args):
    from personal_summary_service import load_config, configure_timezone
    config_path = _plain_absolute(args.config)
    config = load_config(config_path)
    runtime = _plain_absolute(Path(config['data_dir']))
    if config_path != runtime/'personal-summary.json':
        raise NarrativeError('private_runtime_binding')
    writer = runtime/'narrative-writer'
    if _plain_absolute(args.receipts) != writer/'receipts.sqlite3':
        raise NarrativeError('private_runtime_binding')
    for path in (args.entry, args.manifest):
        if not _plain_absolute(path).is_relative_to(writer):
            raise NarrativeError('private_runtime_binding')
    if args.preview and not _plain_absolute(args.preview).is_relative_to(writer):
        raise NarrativeError('private_runtime_binding')
    stop = runtime/'narrative-STOP'
    if stop.exists() or stop.is_symlink():
        raise NarrativeError('stopped')
    configure_timezone(config)
    gh = _plain_absolute(Path(config['gh_path']))
    if args.gh is not None and _plain_absolute(Path(args.gh)) != gh:
        raise NarrativeError('gh_binding')
    if not gh.is_file():
        raise NarrativeError('gh_unavailable')
    return str(gh), stop


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--preview', type=Path, help='Write private local Markdown preview')
    parser.add_argument('--receipts', type=Path, help='Persistent private narrative SQLite receipt path')
    parser.add_argument('--config', type=Path, help='Canonical private personal-summary.json; required for publication')
    parser.add_argument('--gh', help='Optional absolute gh path; must match canonical configuration')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--publish', action='store_true', help='Publish after validation using existing GitHub authentication')
    action.add_argument('--dry-run', action='store_true', help='Explicitly select default local-only validation')
    args = parser.parse_args(argv)
    if args.publish and (args.receipts is None or args.config is None):
        parser.error('--publish requires --config and --receipts')
    try:
        gh, stop = _publish_config(args) if args.publish else (None, None)
        values = []
        for path in (args.entry, args.manifest):
            if path.stat().st_size > 1024 * 1024:
                raise NarrativeError('input_too_large')
            values.append(json.loads(path.read_text(encoding='utf-8-sig')))
        entry, manifest = values
        entry = validate_entry(entry, manifest)
        if args.preview:
            if args.preview.resolve() in {args.entry.resolve(), args.manifest.resolve(),
                    args.receipts.resolve() if args.receipts else None}:
                raise NarrativeError('preview_destination')
            args.preview.write_text(f"# {entry['day']}\n\n" + '\n\n'.join(
                p['text'] for p in entry['paragraphs']) + '\n', encoding='utf-8')
        if not args.publish:
            result = {'state': 'validated_preview' if entry['paragraphs'] else 'skipped',
                      'day': entry['day'], 'paragraphs': len(entry['paragraphs'])}
        else:
            result = NarrativePublisher(gh, args.receipts, stop_path=stop).publish(entry, manifest)
        print(json.dumps(result, sort_keys=True))
        return 0
    except NarrativeError as exc:
        if str(exc) == 'stopped':
            print(json.dumps({'state': 'stopped'}))
            return 0
        print(json.dumps({'state': 'failed', 'error': str(exc)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, sqlite3.Error):
        print(json.dumps({'state': 'failed', 'error': 'local_input_or_receipt'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

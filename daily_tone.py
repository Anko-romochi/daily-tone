"""Local, deterministic reply-tone experiment. No networking or text inference."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

MAX_BYTES = 4096
JAPAN = timezone(timedelta(hours=9))
UTC = timezone.utc
KINDS = frozenset({'compliment', 'shared_win', 'quiet'})
POSITIVE_KINDS = frozenset({'compliment', 'shared_win'})
SCENES = frozenset({'ordinary', 'friendly_ack', 'bashful', 'task_pride', 'shared_pride', 'quiet'})
DEFAULT_CONFIG = {'version': 1, 'enabled': True, 'sweetness': 1, 'learning': True}
_ISO = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})$')
_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


class ToneError(ValueError):
    """Rejected input or storage operation; nothing is silently repaired."""
    def __init__(self, message, *, current_config=None):
        super().__init__(message)
        self.current_config = current_config


def parse_timestamp(value):
    """Parse extended ISO timestamps with seconds and an explicit UTC offset."""
    if type(value) is not str or not _ISO.fullmatch(value):
        raise ToneError('Timestamp must be YYYY-MM-DDTHH:MM:SS[.fraction](Z|+/-HH:MM).')
    # fromisoformat normalizes +00:60; reject malformed offset components ourselves.
    if value[-1:] not in ('Z', 'z'):
        if int(value[-5:-3]) > 23 or int(value[-2:]) > 59:
            raise ToneError('Invalid timezone offset.')
    try:
        result = datetime.fromisoformat(value[:-1] + '+00:00' if value[-1:] in ('Z', 'z') else value)
    except (ValueError, OverflowError) as exc:
        raise ToneError('Invalid real timestamp.') from exc
    if result.utcoffset() is None:
        raise ToneError('Timestamp must be timezone-aware.')
    return result


def _clock(now):
    if isinstance(now, str):
        return parse_timestamp(now)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ToneError('Clock must be a timezone-aware datetime.')
    return now


def _keys(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ToneError(f'{label} must have exactly these keys: {", ".join(sorted(keys))}.')


def validate_config(config):
    _keys(config, ('version', 'enabled', 'sweetness', 'learning'), 'Config')
    if type(config['version']) is not int or config['version'] != 1:
        raise ToneError('Unsupported config version.')
    if type(config['enabled']) is not bool or type(config['learning']) is not bool:
        raise ToneError('enabled and learning must be booleans.')
    if type(config['sweetness']) is not int or not 0 <= config['sweetness'] <= 3:
        raise ToneError('sweetness must be an integer from 0 through 3.')
    return dict(config)


def validate_event(event):
    _keys(event, ('id', 'kind', 'at'), 'Event')
    if type(event['id']) is not str or not _ID.fullmatch(event['id']):
        raise ToneError('Event id must be 1..64 ASCII letters, digits, underscores or hyphens.')
    if type(event['kind']) is not str or event['kind'] not in KINDS:
        raise ToneError('Event kind must be compliment, shared_win or quiet.')
    parse_timestamp(event['at'])
    return dict(event)


def japan_day(at):
    try:
        return parse_timestamp(at).astimezone(JAPAN).date()
    except (OverflowError, ValueError) as exc:
        raise ToneError('Timestamp cannot be represented as a Japan date.') from exc


def validate_state(state):
    _keys(state, ('version', 'growth_basis', 'scene', 'scene_until', 'accepted_until'), 'State')
    if type(state['version']) is not int or state['version'] != 1:
        raise ToneError('Unsupported state version.')
    if type(state['growth_basis']) is not list or len(state['growth_basis']) > 6:
        raise ToneError('growth_basis must be a list of at most six events.')
    watermark = parse_timestamp(state['accepted_until'])
    if type(state['scene']) is not str or state['scene'] not in SCENES:
        raise ToneError('Invalid scene.')
    if state['scene_until'] is not None:
        parse_timestamp(state['scene_until'])
    basis, ids, days, previous = [], set(), set(), None
    for item in state['growth_basis']:
        item = validate_event(item)
        at, day = parse_timestamp(item['at']), japan_day(item['at'])
        if item['kind'] not in POSITIVE_KINDS:
            raise ToneError('Basis may contain positive kinds only.')
        if item['id'] in ids or day in days:
            raise ToneError('Basis ids and Japan dates must be unique.')
        if (previous is not None and at <= previous) or at > watermark:
            raise ToneError('Basis timestamps must increase and cannot exceed accepted_until.')
        ids.add(item['id'])
        days.add(day)
        previous = at
        basis.append(item)
    return {**state, 'growth_basis': basis}


def initial_state(now):
    return {'version': 1, 'growth_basis': [], 'scene': 'ordinary',
            'scene_until': None, 'accepted_until': _clock(now).isoformat()}


def reset_state(state, now):
    """Controlled pure reset; CLI confirmation is handled by Store.reset."""
    validate_state(state)
    return initial_state(now)


def derived_step(state):
    return min(2, len(validate_state(state)['growth_basis']) // 3)


def effective_scene(state, now):
    state, now = validate_state(state), _clock(now)
    if state['scene'] == 'ordinary' or state['scene_until'] is None:
        return 'ordinary'
    return state['scene'] if parse_timestamp(state['scene_until']) > now else 'ordinary'


def appraise(state, kind):
    """Functional relevance, not a claim about emotions or consciousness."""
    state = validate_state(state)
    if type(kind) is not str or kind not in KINDS:
        raise ToneError('Invalid appraisal kind.')
    basis = state['growth_basis']
    step = min(2, len(basis) // 3)
    if kind == 'quiet':
        return {'personal_relevance': 0}, []
    shared = any(item['kind'] == 'shared_win' for item in basis)
    relevance = 1 + (step if kind == 'shared_win' or shared else min(step, 1))
    return {'personal_relevance': relevance}, [item['id'] for item in basis]


def apply_event(config, state, event, now):
    """Validate -> appraise prior state -> choose tone -> update a separate state."""
    config, state, event, now = validate_config(config), validate_state(state), validate_event(event), _clock(now)
    at = parse_timestamp(event['at'])
    if now < parse_timestamp(state['accepted_until']):
        raise ToneError('Clock is behind accepted_until; wait for it to catch up or explicitly reset.')
    if at <= parse_timestamp(state['accepted_until']):
        raise ToneError('Event timestamp must be strictly newer than accepted_until; wait or explicitly reset.')
    try:
        latest = now + timedelta(minutes=5)
    except OverflowError as exc:
        raise ToneError('Clock outside the supported range.') from exc
    if at > latest:
        raise ToneError('Event timestamp is more than five minutes in the future.')
    if any(item['id'] == event['id'] for item in state['growth_basis']):
        raise ToneError('Event id is already retained in growth_basis.')
    appraisal, reasons = appraise(state, event['kind'])
    relevance = appraisal['personal_relevance']
    if event['kind'] == 'quiet':
        style = 'quiet'
    elif event['kind'] == 'compliment':
        style = 'friendly_ack' if relevance == 1 else 'bashful'
    else:
        style = 'task_pride' if relevance == 1 else 'shared_pride'
    active_quiet = effective_scene(state, now) == 'quiet'
    if active_quiet:
        style = 'quiet'
    intensity = 0 if style == 'quiet' else min(config['sweetness'], relevance)
    output_enabled = config['enabled'] and config['sweetness'] > 0
    if not output_enabled:
        style, intensity = 'ordinary', 0
    tone = {'appraisal': appraisal, 'reason_ids': reasons, 'style_tag': style,
            'intensity': intensity, 'input_id': event['id']}
    updated = {**state, 'growth_basis': [dict(item) for item in state['growth_basis']],
               'accepted_until': event['at']}
    if not output_enabled:
        updated['scene'], updated['scene_until'] = 'ordinary', None
    elif active_quiet and event['kind'] != 'quiet':
        # Keep the original quiet deadline; eligible positive learning still follows.
        updated['scene'], updated['scene_until'] = 'quiet', state['scene_until']
    else:
        try:
            updated['scene_until'] = (now + timedelta(hours=2)).isoformat()
        except OverflowError as exc:
            raise ToneError('Clock outside the supported range.') from exc
        updated['scene'] = style
    if (output_enabled and config['learning'] and event['kind'] in POSITIVE_KINDS
            and len(updated['growth_basis']) < 6
            and japan_day(event['at']) not in {japan_day(item['at']) for item in state['growth_basis']}):
        updated['growth_basis'].append(dict(event))
    validate_state(updated)
    return tone, updated


def show_snapshot(config, state, now):
    """Return a presentation copy; expired/off scene is ordinary, never persisted."""
    config, state = validate_config(config), validate_state(state)
    scene = effective_scene(state, now)
    if not config['enabled'] or config['sweetness'] == 0:
        scene = 'ordinary'
    state = {**state, 'scene': scene,
             'scene_until': None if scene == 'ordinary' else state['scene_until']}
    return {'config': config, 'state': state, 'effective_scene': scene,
            'derived_step': min(2, len(state['growth_basis']) // 3)}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ToneError(f'Duplicate JSON key: {key}.')
        result[key] = value
    return result


def _constant(value):
    raise ToneError(f'Nonfinite JSON number is forbidden: {value}.')


def load_json(path):
    """Strict UTF-8 JSON, bounded before decode, including whitespace bytes."""
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ToneError('Persisted JSON exceeds 4096 bytes.')
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ToneError(f'Cannot read valid persisted JSON: {exc}.') from exc


def json_bytes(value):
    try:
        raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ToneError(f'Cannot serialize JSON: {exc}.') from exc
    if len(raw) > MAX_BYTES:
        raise ToneError('Persisted JSON exceeds 4096 bytes.')
    return raw


def atomic_write(path, value):
    """Replace one file atomically from a flushed temporary in the same directory."""
    path, raw, temporary = Path(path), json_bytes(value), None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.daily-tone-', suffix='.tmp',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise ToneError(f'Atomic write failed: {exc}.') from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


@contextmanager
def writer_lock(directory):
    """Never steal a lock, including a stale lock. Owner recovery is explicit."""
    directory = Path(directory)
    lock = directory / '.writer.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ToneError('Writer lock exists; concurrent or stale writer is refused.') from exc
    except OSError as exc:
        raise ToneError(f'Cannot acquire writer lock: {exc}.') from exc
    try:
        os.close(fd)
        yield
    finally:
        try:
            lock.unlink()
        except OSError as exc:
            raise ToneError(f'Cannot release writer lock: {exc}.') from exc


class Store:
    """Single-user plaintext storage in an explicitly selected folder outside source."""
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        source = Path(__file__).resolve().parent
        if self.directory == source or source in self.directory.parents:
            raise ToneError('Data directory must be outside the source repository.')
        self.config_path = self.directory / 'config.json'
        self.state_path = self.directory / 'state.json'

    def _layout(self, *, empty=False):
        if not self.directory.is_dir():
            raise ToneError('Data directory does not exist; explicitly initialize it first.')
        names = {entry.name for entry in self.directory.iterdir()}
        allowed = {'.writer.lock'} if empty else {'config.json', 'state.json', '.writer.lock'}
        if names - allowed:
            raise ToneError('Data directory contains unknown existing data; use a dedicated folder.')
        if empty and names - {'.writer.lock'}:
            raise ToneError('Initialization requires an empty data directory.')
        if not empty and not {'config.json', 'state.json'} <= names:
            raise ToneError('Missing or partial store; initialization/reset over partial data is forbidden.')

    def initialize(self, config=None, now=None):
        config = validate_config(DEFAULT_CONFIG if config is None else config)
        state = initial_state(datetime.now(UTC) if now is None else now)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToneError(f'Cannot create data directory: {exc}.') from exc
        with writer_lock(self.directory):
            self._layout(empty=True)
            atomic_write(self.state_path, state)
            atomic_write(self.config_path, config)
        return config, state

    def read(self):
        self._layout()
        return validate_config(load_json(self.config_path)), validate_state(load_json(self.state_path))

    def event(self, event, now):
        with writer_lock(self.directory):
            config, state = self.read()
            tone, updated = apply_event(config, state, event, now)
            atomic_write(self.state_path, updated)
        return tone, updated

    def configure(self, changes, now=None):
        if type(changes) is not dict or not set(changes) <= set(DEFAULT_CONFIG):
            raise ToneError('Unknown configure settings.')
        with writer_lock(self.directory):
            config, state = self.read()
            updated = validate_config({**config, **changes})
            if not updated['enabled'] or updated['sweetness'] == 0:
                cleared = {**state, 'scene': 'ordinary', 'scene_until': None}
                atomic_write(self.state_path, cleared)
                try:
                    atomic_write(self.config_path, updated)
                except (ToneError, OSError) as exc:
                    raise ToneError(f'Config write failed after scene was cleared: {exc}',
                                    current_config=validate_config(load_json(self.config_path))) from exc
            else:
                atomic_write(self.config_path, updated)
        return updated

    def reset(self, now, confirm=False):
        with writer_lock(self.directory):
            config, state = self.read()
            if (state['growth_basis'] or state['scene'] != 'ordinary') and confirm is not True:
                raise ToneError('Existing nonempty state reset requires explicit --confirm.')
            updated = reset_state(state, now)
            atomic_write(self.state_path, updated)
        return config, updated


def synthetic_demo():
    """A–E counterfactuals share synthetic events/clocks; no RNG or storage."""
    config = {'version': 1, 'enabled': True, 'sweetness': 3, 'learning': False}
    base_now = datetime(2026, 1, 10, 12, tzinfo=UTC)
    empty = initial_state(base_now)
    shared = {**empty, 'growth_basis': [
        {'id': f'synthetic_{i}', 'kind': 'shared_win',
         'at': datetime(2026, 1, i, 12, tzinfo=UTC).isoformat()} for i in range(1, 7)]}
    compliments = {**shared, 'growth_basis': [{**item, 'kind': 'compliment'} for item in shared['growth_basis']]}
    states = {'A': empty, 'B': shared, 'C': reset_state(shared, base_now), 'D': compliments, 'E': empty}
    conditions = {key: {'basis_count': len(state['growth_basis']),
                         'derived_step': min(2, len(state['growth_basis']) // 3)} for key, state in states.items()}
    conditions['E']['test_only_memoryless'] = True
    comparison = {}
    p3_states = {}
    for index, kind in enumerate(('compliment', 'shared_win', 'quiet'), 1):
        now = base_now + timedelta(seconds=index)
        event = {'id': f'probe_{index}', 'kind': kind, 'at': now.isoformat()}
        row = {}
        for key, state in states.items():
            tone, updated = apply_event(config, state, event, now)
            if key == 'E':
                tone = {**tone, 'appraisal': {'personal_relevance': None}, 'reason_ids': [],
                        'style_tag': 'quiet' if kind == 'quiet' else 'memoryless',
                        'intensity': 0 if kind == 'quiet' else 3}
            row[key] = tone
            if index == 3:
                p3_states[key] = updated
        comparison[f'P{index}'] = row
    now = base_now + timedelta(days=1, seconds=3)
    event = {'id': 'probe_4', 'kind': 'compliment', 'at': now.isoformat()}
    comparison['P4'] = {}
    for key, state in p3_states.items():
        tone, _ = apply_event(config, state, event, now)
        if key == 'E':
            tone = {**tone, 'appraisal': {'personal_relevance': None}, 'reason_ids': [],
                    'style_tag': 'memoryless', 'intensity': 3}
        comparison['P4'][key] = {'prior_effective_scene': effective_scene(state, now), 'tone': tone}
    capped_tone, _ = apply_event({**config, 'sweetness': 1}, shared,
                               {'id': 'cap_probe', 'kind': 'compliment',
                                'at': (base_now + timedelta(seconds=1)).isoformat()},
                               base_now + timedelta(seconds=1))
    return {'synthetic_only': True, 'config': config, 'conditions': conditions,
            'comparison': comparison, 'owner_cap_example': capped_tone,
            'japanese_samples': {'friendly_ack_1': 'ありがとう。そう言ってもらえてうれしいです。',
                                 'bashful_2': 'ありがとう。少し照れますね。',
                                 'bashful_3': 'ありがとう。一緒に積み重ねてきたことを思うと、少し照れます。',
                                 'task_pride_1': 'できましたね。ひと区切りです。',
                                 'shared_pride_3': '一緒にできましたね。ここまでの積み重ねが形になりました。',
                                 'quiet_0': 'わかりました。静かに進めます。'},
            'limitations': ['Appraisal is a functional variable, not subjective emotion evidence.',
                            'E is a test-only memoryless control; B and E can match on one reply.',
                            'Non-basis IDs restamped newer cannot be fully detected.',
                            'Samples are fixed examples; no inference from conversation text.']}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToneError(message)


def _boolean(value):
    if value == 'true':
        return True
    if value == 'false':
        return False
    raise argparse.ArgumentTypeError('Use true or false.')


def main(argv=None):
    try:
        parser = _Parser(description='Local explicit-event tone experiment; JSON output, no background actions.')
        parser.add_argument('--data-dir', '--directory', dest='directory', help='Dedicated private folder outside source.')
        parser.add_argument('--now', help='TEST ONLY: inject an aware ISO clock; normally uses the current UTC time.')
        sub = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
        for name in ('init', 'configure', 'event', 'show', 'reset', 'demo'):
            command = sub.add_parser(name)
            if name != 'demo':
                command.add_argument('--data-dir', '--directory', dest='directory', default=argparse.SUPPRESS)
                command.add_argument('--now', default=argparse.SUPPRESS, help='TEST ONLY: inject an aware ISO clock.')
            if name in ('init', 'configure'):
                command.add_argument('--enabled', type=_boolean)
                command.add_argument('--sweetness', type=int)
                command.add_argument('--learning', type=_boolean)
            elif name == 'event':
                command.add_argument('--kind', required=True, choices=sorted(KINDS))
                command.add_argument('--id', help='Optional opaque ID; default is a generated local UUID.')
                command.add_argument('--at', help='Explicit aware ISO event timestamp for synthetic tests.')
            elif name == 'reset':
                command.add_argument('--confirm', action='store_true', help='Confirm resetting existing nonempty state.')
        args = parser.parse_args(argv)
        now = parse_timestamp(args.now) if args.now else datetime.now(UTC)
        if args.command == 'demo':
            result = synthetic_demo()
        else:
            if not args.directory:
                raise ToneError('Persisted commands require an explicit --data-dir outside source.')
            store = Store(args.directory)
            if args.command == 'show':
                config, state = store.read()
                result = show_snapshot(config, state, now)
            elif args.command == 'event':
                tone, state = store.event({'id': args.id if args.id is not None else uuid.uuid4().hex,
                                           'kind': args.kind, 'at': args.at if args.at is not None else now.isoformat()}, now)
                result = {'tone': tone, 'state': state}
            elif args.command == 'reset':
                config, state = store.reset(now, args.confirm)
                result = {'config': config, 'state': state}
            else:
                changes = {key: getattr(args, key) for key in ('enabled', 'sweetness', 'learning')
                           if getattr(args, key) is not None}
                if args.command == 'init':
                    config, state = store.initialize({**DEFAULT_CONFIG, **changes}, now)
                    result = {'config': config, 'state': state}
                else:
                    result = {'config': store.configure(changes, now)}
        print(json.dumps({'ok': True, **result}, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
        return 0
    except (ToneError, OSError) as exc:
        result = {'ok': False, 'error': str(exc)}
        if isinstance(exc, ToneError) and exc.current_config is not None:
            result['current_config'] = exc.current_config
        print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
        return 1


if __name__ == '__main__':
    sys.exit(main())

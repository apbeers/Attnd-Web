"""Log record validation (API-013, record format v1, LOG_EVENTS.md).

`clean_entry` turns one uploaded record into the fields of a DiagnosticEntry, or returns None to drop it.
The apps redact on the device; the server scrubs again as a safety net (`scrub`): sensitive keys are dropped,
URLs lose their query strings and coordinates are rounded to 0.01°.
"""
import json
import re
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from diagnostics.models import APP_CATEGORIES, LEVELS

RECORD_VERSION = 1
# "D-" + 5 Crockford base32 characters (no I, L, O, U).
CODE_RE = re.compile(r'^D-[0-9A-HJKMNP-TV-Z]{5}$')
EVENT_RE = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$')
REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9._:-]{8,64}$')
MAX_MSG = 2000
MAX_FIELDS_BYTES = 4096
STATES = ('fg', 'bg')
NETS = ('wifi', 'cell', 'none', 'other')
# Keys whose values are never stored (compared lower case, also as a suffix: "apns_token", "user_email").
SENSITIVE_KEYS = ('token', 'password', 'secret', 'authorization', 'api_key', 'apikey', 'email', 'device_id',
                  'deviceid', 'support_code', 'cookie', 'jwt')
# What people typed (search, Ask, plan requests): only dev builds may log it (spec §1); others log lengths.
TEXT_KEYS = ('query', 'text', 'prompt', 'q')
URL_KEYS = ('url', 'path', 'href', 'uri', 'link')
COORD_KEYS = ('lat', 'lng', 'lon', 'latitude', 'longitude')


def clean_code(value):
    """A debug code in canonical form (upper case), or '' when it isn't one."""
    if not isinstance(value, str):
        return ''
    code = value.strip().upper()
    return code if CODE_RE.match(code) else ''


def clean_request_id(value):
    if isinstance(value, str) and REQUEST_ID_RE.match(value.strip()):
        return value.strip()
    return ''


def level_number(value):
    if isinstance(value, str) and value.lower() in LEVELS:
        return LEVELS.index(value.lower())
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(LEVELS):
        return value
    return None


LENGTH_WORDS = ('len', 'length', 'count', 'chars', 'words')


def _words(key):
    """'pushToken' / 'push_token' / 'push-token' → ['push', 'token']."""
    key = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', key)
    return [w for w in re.split(r'[_\-.\s]+', key.lower()) if w]


def _sensitive(key, allow_text=False):
    words = _words(key)
    if any(w in LENGTH_WORDS for w in words):
        return False  # "query_len", "text_length": lengths are what the apps are told to log instead
    joined = '_'.join(words)
    keys = SENSITIVE_KEYS if allow_text else SENSITIVE_KEYS + TEXT_KEYS
    return any(s in words or joined == s or joined.endswith('_' + s) for s in keys)


def scrub(value, depth=0, allow_text=False):
    """A copy of an `f` value with sensitive keys removed, query strings cut and coordinates rounded.
    `allow_text` (dev builds only) keeps typed text (search/Ask/plan) keys."""
    if depth > 4:
        return None
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:50]:
            key = str(key)[:40]
            if _sensitive(key, allow_text):
                continue
            lower = key.lower()
            if lower in URL_KEYS and isinstance(item, str):
                item = item.split('?', 1)[0].split('#', 1)[0]
            elif lower in COORD_KEYS and isinstance(item, (int, float)) and not isinstance(item, bool):
                item = round(float(item), 2)
            else:
                item = scrub(item, depth + 1, allow_text)
            out[key] = item
        return out
    if isinstance(value, list):
        return [scrub(v, depth + 1, allow_text) for v in value[:50]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:100]


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -2**62 < value < 2**62 else None
    if isinstance(value, float) and value == value:
        return int(value)
    return None


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def clean_entry(entry, session_code, now=None, oldest_days=14, allow_text=False):
    """A dict of DiagnosticEntry fields, or None if the record is invalid. See API_V2.md → diagnostics."""
    now = now or timezone.now()
    if not isinstance(entry, dict) or entry.get('v') != RECORD_VERSION:
        return None
    level = level_number(entry.get('level'))
    cat = entry.get('cat')
    name = entry.get('event')
    if level is None or cat not in APP_CATEGORIES or not isinstance(name, str) or len(name) > 64 \
            or not EVENT_RE.match(name):
        return None
    ts = parse_datetime(entry['ts']) if isinstance(entry.get('ts'), str) else None
    if ts is None or timezone.is_naive(ts) or not (now - timedelta(days=oldest_days) <= ts <= now + timedelta(days=1)):
        return None
    diag = entry.get('diag')
    if diag not in (None, '') and clean_code(diag) != session_code:
        return None  # a record from another session doesn't belong in this one
    fields = entry.get('f')
    fields = scrub(fields, allow_text=allow_text) if isinstance(fields, dict) else {}
    encoded = json.dumps(fields, separators=(',', ':'))
    if len(encoded) > MAX_FIELDS_BYTES:
        fields, encoded = {'_truncated': True}, '{"_truncated":true}'
    event_id = _int(entry.get('event_id'))
    msg = _text(entry.get('msg'), MAX_MSG)
    return {
        'ts': ts,
        'mono_ms': _int(entry.get('mono_ms')),
        'seq': _int(entry.get('seq')),
        'level': level,
        'cat': cat,
        'name': name,
        'msg': msg,
        'fields': fields,
        'state': entry.get('state') if entry.get('state') in STATES else '',
        'net': entry.get('net') if entry.get('net') in NETS else '',
        'event_id': event_id if event_id is not None and event_id > 0 else None,
        'app_version': _text(entry.get('app'), 32),
        'req_id': clean_request_id(fields.get('req_id')) if isinstance(fields.get('req_id'), str) else '',
        # Rough stored size: what the session's byte count (and the size cap's estimate) adds up.
        '_bytes': 160 + len(msg) + len(encoded) + len(name),
    }


def record_dict(entry, session):
    """A DiagnosticEntry as a v1 record (export .jsonl)."""
    record = {
        'v': RECORD_VERSION, 'ts': entry.ts.isoformat().replace('+00:00', 'Z'), 'mono_ms': entry.mono_ms,
        'seq': entry.seq, 'level': LEVELS[entry.level], 'cat': entry.cat, 'event': entry.name, 'msg': entry.msg,
        'f': entry.fields, 'app': entry.app_version or session.app_version, 'build': session.build, 'os': session.os,
        'model': session.model, 'state': entry.state or None, 'net': entry.net or None, 'event_id': entry.event_id,
        'diag': session.code,
    }
    if entry.source != 'app':
        record['source'] = entry.source
    return {k: v for k, v in record.items() if v is not None}

"""Summaries for one debug session in the Diagnostics panel: errors grouped, slowest requests, launch times,
pushes received vs handled, background wakes, network totals."""
from collections import Counter, defaultdict
from statistics import median

from diagnostics.models import LEVEL_ERROR, LEVELS

MAX_SCAN = 20000


def _ms(fields):
    value = fields.get('ms') if isinstance(fields, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def build(session):
    entries = session.entries.all()
    errors = defaultdict(lambda: {'count': 0, 'first': None, 'last': None, 'msg': '', 'level': 0})
    for name, fields, ts, msg, level in (entries.filter(level__gte=LEVEL_ERROR).order_by('ts')
                                         .values_list('name', 'fields', 'ts', 'msg', 'level')[:MAX_SCAN]):
        fields = fields if isinstance(fields, dict) else {}
        key = (name, str(fields.get('domain', '')), str(fields.get('code', '')), str(fields.get('type', '')))
        group = errors[key]
        group['count'] += 1
        group['first'] = group['first'] or ts
        group['last'] = ts
        group['msg'] = group['msg'] or msg
        group['level'] = max(group['level'], level)
    error_rows = sorted(({'name': k[0], 'domain': k[1], 'code': k[2], 'type': k[3], **v,
                          'level_name': LEVELS[v['level']]} for k, v in errors.items()),
                        key=lambda r: -r['count'])[:30]

    requests = []
    statuses = Counter()
    failed = 0
    for name, fields, ts, msg in (entries.filter(name__in=('net.request.finished', 'net.request.failed'))
                                  .values_list('name', 'fields', 'ts', 'msg')[:MAX_SCAN]):
        fields = fields if isinstance(fields, dict) else {}
        if name == 'net.request.failed':
            failed += 1
        status = fields.get('status')
        if isinstance(status, int):
            statuses[f'{status // 100}xx'] += 1
        ms = _ms(fields)
        if ms is not None:
            requests.append({'ts': ts, 'ms': ms, 'path': fields.get('path', ''), 'method': fields.get('method', ''),
                             'status': status, 'req_id': fields.get('req_id', ''), 'msg': msg})
    slowest = sorted(requests, key=lambda r: -r['ms'])[:10]

    launches = []
    for fields, ts in entries.filter(name='lifecycle.launch').order_by('ts').values_list('fields', 'ts')[:200]:
        fields = fields if isinstance(fields, dict) else {}
        launches.append({'ts': ts, 'kind': fields.get('kind', ''), 'ms': _ms(fields)})
    launch_ms = [l['ms'] for l in launches if l['ms'] is not None]

    counts = dict(Counter(entries.filter(name__in=(
        'push.received', 'push.handled', 'push.dropped', 'background.wake', 'background.refresh.started',
        'background.refresh.finished', 'background.refresh.failed', 'lifecycle.foreground', 'lifecycle.background',
    )).values_list('name', flat=True)[:MAX_SCAN]))
    push_types = Counter()
    for fields in entries.filter(name='push.received').values_list('fields', flat=True)[:MAX_SCAN]:
        push_types[(fields or {}).get('type', '?') if isinstance(fields, dict) else '?'] += 1

    return {
        'errors': error_rows,
        'slowest': slowest,
        'requests': len(requests) + failed,
        'failed_requests': failed,
        'statuses': dict(sorted(statuses.items())),
        'launches': launches[-20:],
        'launch_median': round(median(launch_ms)) if launch_ms else None,
        'push_received': counts.get('push.received', 0),
        'push_handled': counts.get('push.handled', 0),
        'push_dropped': counts.get('push.dropped', 0),
        'push_types': dict(push_types),
        'background_wakes': counts.get('background.wake', 0) + counts.get('background.refresh.started', 0),
        'background_finished': counts.get('background.refresh.finished', 0),
        'background_failed': counts.get('background.refresh.failed', 0),
        'foregrounds': counts.get('lifecycle.foreground', 0),
    }


def timeline(session, limit=MAX_SCAN):
    """The session's entries for the panel's JavaScript (newest `limit`), oldest first.

    `t` is the entry's own time, `k` its place on the timeline (ms since 1970): a server line (source
    "server") takes the time of the app's net.request.* line with the same req_id and is sorted right behind
    it, so the two sit together whatever the clocks say."""
    rows = list(session.entries.order_by('-ts', '-id').values(
        'id', 'ts', 'level', 'cat', 'name', 'msg', 'fields', 'state', 'net', 'req_id', 'source', 'event_id',
        'mono_ms', 'seq')[:limit])
    rows.reverse()
    def own_key(row):
        return (row['ts'].timestamp() * 1000, row['seq'] if row['seq'] is not None else 0, row['id'], 0)

    app_keys = {}
    for row in rows:
        if row['source'] == 'app' and row['req_id'] and row['name'].startswith('net.request.'):
            app_keys[row['req_id']] = own_key(row)  # the last of started/finished/failed
    out = []
    for row in rows:
        t = row['ts'].timestamp() * 1000
        key = own_key(row)
        if row['source'] != 'app' and row['req_id'] in app_keys:
            key = (*app_keys[row['req_id']][:3], 1)  # right after the app's line for the same request
        out.append({'id': row['id'], 't': round(t), 'k': key[0], 'l': row['level'], 'c': row['cat'], 'e': row['name'],
                    'm': row['msg'], 'f': row['fields'] if isinstance(row['fields'], dict) else {},
                    's': row['state'], 'n': row['net'], 'r': row['req_id'], 'src': row['source'],
                    'ev': row['event_id'], 'seq': row['seq'], '_key': key})
    out.sort(key=lambda r: r['_key'])
    for row in out:
        del row['_key']
    return out

"""Platform → Architecture (batch 7 Track E, superusers): docs/architecture.toml (what code can't see) + what is
generated from the code at request time (Django apps and models, URL namespaces, API v2 endpoints, push types, Ask
Attnd actions, scheduled tasks, integrations configured) + a live status per service. The drift checks
(`drift()`) are what attnd/tests_architecture.py runs in CI."""

import os
import re
import tomllib
from collections import defaultdict
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

ROOT = Path(settings.BASE_DIR)
SOURCE = ROOT / 'docs' / 'architecture.toml'
LAYERS = [('clients', 'Clients'), ('edge', 'Edge'), ('apps', 'Django apps'), ('data', 'Data'), ('external', 'External')]
TAGS = ['infra', 'data', 'ai', 'push', 'store', 'web', 'mail', 'pay']
GITHUB = 'https://github.com/apbeers/Attnd-Web/blob/develop/'
SKIP_DIRS = {'.venv', 'node_modules', 'staticfiles', 'media', 'live_tests', 'scripts', '.git'}
# Where installed Python lives when it sits inside the project (Heroku: /app/.heroku/python/…site-packages).
THIRD_PARTY_PARTS = {'.venv', '.heroku', 'site-packages', 'dist-packages'}


def _first_party(path):
    """True for our own code: inside the repository and not an installed package (Django contrib, cloudinary…)."""
    path = Path(path)
    return ROOT in path.parents and not (THIRD_PARTY_PARTS & set(path.relative_to(ROOT).parts))


def load():
    with SOURCE.open('rb') as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------------------
# What the code says (also the drift test's input)
# ---------------------------------------------------------------------------

def local_apps():
    """Labels of our own Django apps (their code lives in this repository)."""
    out = []
    for cfg in django_apps.get_app_configs():
        path = Path(cfg.path)
        if _first_party(path):
            out.append(cfg.label)
    return sorted(out)


def _source_files():
    """Our non-test .py files. Walks with os.walk and prunes SKIP_DIRS (.venv alone is ~30k files) instead of
    globbing everything and filtering afterwards."""
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d not in THIRD_PARTY_PARTS and not d.startswith('.')
                   and d != 'migrations' and d != '__pycache__']
        for name in files:
            if not name.endswith('.py') or name.startswith('test'):
                continue
            p = Path(folder) / name
            yield p.relative_to(ROOT), p


@lru_cache(maxsize=1)
def _called_hosts():
    out = defaultdict(set)
    for rel, p in _source_files():
        for m in re.finditer(r'https://([a-z0-9.-]+\.[a-z]{2,})', p.read_text(errors='ignore')):
            out[m.group(1)].add(str(rel))
    return {h: sorted(f) for h, f in out.items()}


def called_hosts():
    """{host: [files]} of every https:// host written in our non-test code. The code can't change while a process
    runs, so it is read once per process (the Architecture page took ~0.4–2 s re-reading every file)."""
    return {h: list(f) for h, f in _called_hosts().items()}


ENV_RE = re.compile(r'(?:getenv|environ\.get|environ\[|_flag|_int|_float|_rate|_list|_env\w*)\(\s*[\'"]([A-Z][A-Z0-9_]+)')


def settings_env():
    """Environment variable names attnd/settings.py reads."""
    return sorted(set(ENV_RE.findall((ROOT / 'attnd' / 'settings.py').read_text())))


def push_types():
    text = (ROOT / 'events' / 'push.py').read_text()
    return sorted(set(re.findall(r"'type':\s*'([a-z_]+)'", text)))


def drift(data=None):
    """[(kind, name, where)] the code has that docs/architecture.toml doesn't."""
    data = data or load()
    nodes = data.get('node', [])
    ids = {n['id'] for n in nodes}
    hosts = {h for n in nodes for h in n.get('hosts', [])} | set(data.get('ignore_hosts', {}))
    env = {e for n in nodes for e in n.get('env', [])}
    out = []
    for label in local_apps():
        if label not in ids:
            out.append(('app', label, f'{label}/'))
    for host, files in called_hosts().items():
        if host not in hosts:
            out.append(('host', host, ', '.join(files[:3])))
    for name in settings_env():
        if name not in env:
            out.append(('env', name, 'attnd/settings.py'))
    for t in push_types():
        if t not in data.get('push_types', {}):
            out.append(('push', t, 'events/push.py'))
    for n in nodes:
        for e in n.get('edges', []):
            if e['to'] not in ids:
                out.append(('edge', f'{n["id"]} → {e["to"]}', 'docs/architecture.toml'))
    for f in data.get('flow', []):
        for node, _ in f['steps']:
            if node not in ids:
                out.append(('flow', f'{f["id"]}: {node}', 'docs/architecture.toml'))
    return out


def models():
    """The ERD: our models with fields, relations and admin links."""
    from django.contrib import admin
    from django.urls import NoReverseMatch, reverse
    out = []
    ours = set(local_apps())
    for model in django_apps.get_models():
        meta = model._meta
        if meta.app_label not in ours:
            continue
        fields, relations = [], []
        for f in meta.get_fields():
            if f.auto_created and not f.concrete:
                continue
            if f.is_relation and f.related_model is not None:
                relations.append({'name': f.name, 'to': f'{f.related_model._meta.app_label}.{f.related_model.__name__}',
                                  'kind': type(f).__name__})
            elif getattr(f, 'concrete', False):
                fields.append(f'{f.name}: {type(f).__name__.replace("Field", "")}')
        url = ''
        if model in admin.site._registry:
            try:
                url = reverse(f'admin:{meta.app_label}_{meta.model_name}_changelist')
            except NoReverseMatch:
                url = ''
        out.append({'app': meta.app_label, 'name': model.__name__, 'fields': fields[:40], 'relations': relations,
                    'admin': url})
    return sorted(out, key=lambda m: (m['app'], m['name']))


def _walk(patterns, prefix='', ns=''):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern), p.namespace or ns)
        elif isinstance(p, URLPattern):
            yield ns, prefix + str(p.pattern), p.name


def url_namespaces():
    counts = defaultdict(int)
    for ns, _path, _name in _walk(get_resolver().url_patterns):
        counts[ns or '(root)'] += 1
    return sorted(counts.items())


def api_endpoints():
    return sorted({path for _ns, path, _n in _walk(get_resolver().url_patterns) if path.startswith('api/v2/')})


def agent_counts():
    from agent import registry
    r = registry.registry()
    writes = r['writes'].values()
    return {'writes': len(r['writes']), 'reads': len(r['reads']), 'always_ask': sum(1 for a in writes if a.confirm)}


def scheduled_tasks():
    path = ROOT / 'events' / 'management' / 'commands' / 'run_scheduled_tasks.py'
    text = path.read_text() if path.exists() else ''
    return re.findall(r'^\s*#\s*(.+)$', text, re.M)[:30] or ['see events/management/commands/run_scheduled_tasks.py']


def configured(node):
    """{env: True/False} — whether each env var is set (never the value)."""
    import os
    return {name: bool(os.environ.get(name) or getattr(settings, name, '')) for name in node.get('env', [])}


# ---------------------------------------------------------------------------
# Live status (green / amber / red per service)
# ---------------------------------------------------------------------------

def status():
    """{node_id: {state, text}} from today's counters: Google, push, AI ledger, email log, endpoint timings."""
    out = {}
    since = timezone.now() - timedelta(hours=24)
    try:
        from attnd import google
        usage = google.usage()
        calls = sum(v['calls'] for v in usage.values())
        errors = sum(v['errors'] + v['quota'] for v in usage.values())
        g = {'state': 'red' if errors and errors >= calls else 'amber' if errors else 'green',
             'text': f'{calls} calls, {errors} errors or quota refusals today'}
        out['google_places'] = out['google_geocoding'] = g
    except Exception:
        pass
    try:
        from django.db.models import Sum

        from events.models import Notification
        agg = Notification.objects.filter(sent_at__gte=since).aggregate(d=Sum('delivered_count'), f=Sum('failed_count'))
        d, f = agg['d'] or 0, agg['f'] or 0
        p = {'state': 'red' if f and not d else 'amber' if f > d * 0.1 else 'green',
             'text': f'{d} delivered, {f} failed in 24 h'}
        out['apns'] = out['fcm'] = p
    except Exception:
        pass
    try:
        from django.db.models import Count, Sum

        from credit.models import AiSpend
        agg = AiSpend.objects.filter(created_at__gte=since).aggregate(n=Count('pk'), cost=Sum('cost_usd'))
        out['anthropic'] = {'state': 'green', 'text': f'{agg["n"] or 0} calls, ${agg["cost"] or 0:.2f} in 24 h'}
    except Exception:
        pass
    try:
        from platform_admin.models import EmailLog
        qs = EmailLog.objects.filter(created_at__gte=since)
        failed = qs.filter(status__in=[EmailLog.FAILED, EmailLog.BOUNCED]).count()
        out['mailgun'] = {'state': 'red' if failed and failed == qs.count() else 'amber' if failed else 'green',
                          'text': f'{qs.count()} sent, {failed} failed in 24 h'}
    except Exception:
        pass
    try:
        from django.db.models import Sum

        from diagnostics.models import EndpointTiming
        agg = EndpointTiming.objects.filter(hour__gte=since).aggregate(n=Sum('count'), e=Sum('server_errors'))
        n, e = agg['n'] or 0, agg['e'] or 0
        out['heroku_router'] = {'state': 'red' if n and e > n * 0.05 else 'amber' if e else 'green',
                                'text': f'{n} API requests, {e} server errors in 24 h'}
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# The diagram (server-drawn SVG: layered columns)
# ---------------------------------------------------------------------------

NODE_W, NODE_H, GAP_X, GAP_Y, PAD = 190, 46, 90, 14, 30


def layout(nodes):
    """{id: (x, y)} by layer, top to bottom."""
    pos, heights = {}, []
    for col, (layer, _label) in enumerate(LAYERS):
        members = [n for n in nodes if n['layer'] == layer]
        for row, n in enumerate(members):
            pos[n['id']] = (PAD + col * (NODE_W + GAP_X), PAD + 34 + row * (NODE_H + GAP_Y))
        heights.append(PAD + 34 + len(members) * (NODE_H + GAP_Y))
    width = PAD * 2 + len(LAYERS) * NODE_W + (len(LAYERS) - 1) * GAP_X
    return pos, width, max(heights) + PAD


def diagram(data=None, live=None):
    data = data or load()
    nodes = data['node']
    live = live or {}
    pos, width, height = layout(nodes)
    edges = []
    for n in nodes:
        for e in n.get('edges', []):
            if e['to'] not in pos:
                continue
            x1, y1 = pos[n['id']]
            x2, y2 = pos[e['to']]
            a = (x1 + NODE_W, y1 + NODE_H / 2) if x2 > x1 else (x1, y1 + NODE_H / 2)
            b = (x2, y2 + NODE_H / 2) if x2 > x1 else (x2 + NODE_W, y2 + NODE_H / 2)
            if x2 == x1:
                a, b = (x1 + NODE_W, y1 + NODE_H / 2), (x2 + NODE_W, y2 + NODE_H / 2)
            mid = (a[0] + b[0]) / 2 + (40 if x2 == x1 else 0)
            edges.append({'from': n['id'], 'to': e['to'], 'label': e.get('label', ''),
                          'd': f'M{a[0]:.0f},{a[1]:.0f} C{mid:.0f},{a[1]:.0f} {mid:.0f},{b[1]:.0f} {b[0]:.0f},{b[1]:.0f}'})
    boxes = []
    for n in nodes:
        x, y = pos[n['id']]
        boxes.append({**n, 'x': x, 'y': y, 'w': NODE_W, 'h': NODE_H, 'state': (live.get(n['id']) or {}).get('state', ''),
                      'tags_text': ' '.join(n.get('tags', []))})
    columns = [{'label': label, 'x': PAD + i * (NODE_W + GAP_X)} for i, (_l, label) in enumerate(LAYERS)]
    return {'width': width, 'height': height, 'nodes': boxes, 'edges': edges, 'columns': columns}


def panel_data(data=None, live=None):
    """What the side panel shows per node (JSON in the page)."""
    data = data or load()
    live = live or status()
    out = {}
    for n in data['node']:
        code = [{'path': c, 'url': (GITHUB + c.rstrip('*/')) if ':' not in c and '*' not in c else ''}
                for c in n.get('code', [])]
        out[n['id']] = {'label': n['label'], 'description': n.get('description', ''), 'docs': n.get('docs', ''),
                        'code': code, 'env': configured(n), 'hosts': n.get('hosts', []), 'tags': n.get('tags', []),
                        'edges': n.get('edges', []), 'status': live.get(n['id'])}
    return out


def markdown(data=None):
    data = data or load()
    lines = ['# Attnd architecture snapshot', '', f'Generated {timezone.now():%Y-%m-%d %H:%M} UTC from docs/architecture.toml and the code.', '']
    for layer, label in LAYERS:
        lines += [f'## {label}', '']
        for n in [n for n in data['node'] if n['layer'] == layer]:
            lines.append(f'- **{n["label"]}** (`{n["id"]}`): {n.get("description", "")}')
            for e in n.get('edges', []):
                lines.append(f'  - → `{e["to"]}`: {e.get("label", "")} ({e.get("protocol", "")}{", " + e["auth"] if e.get("auth") else ""})')
        lines.append('')
    lines += ['## Flows', '']
    for f in data.get('flow', []):
        lines.append(f'### {f["label"]}')
        lines += [f'{i}. `{node}`: {text}' for i, (node, text) in enumerate(f['steps'], 1)]
        lines.append('')
    lines += ['## Generated', '', f'- Django apps: {", ".join(local_apps())}',
              f'- API v2 endpoints: {len(api_endpoints())}', f'- Push types: {", ".join(push_types())}',
              f'- Ask Attnd actions: {agent_counts()}', '']
    return '\n'.join(lines)


@lru_cache(maxsize=1)
def _cached_models():
    return models()

"""Platform → Architecture, App options (superusers) and Brand (staff; the public copy is /brand/)."""

from django.http import HttpResponse
from django.shortcuts import render

from agent.registry import agent_action
from attnd.permissions import requires
from attnd.public import public
from platform_admin import architecture as arch


@agent_action('platform_architecture', "Attnd's architecture (superusers): every client, Django app, data store and "
              'outside service with its connections, live status, flows and the data model. ?format=md = a Markdown '
              'snapshot.', kind='read')
@requires('platform_admin.view_architecture')
def architecture(request):
    if request.GET.get('format') == 'md':
        response = HttpResponse(arch.markdown(), content_type='text/markdown; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="attnd-architecture.md"'
        return response
    data = arch.load()
    live = arch.status()
    tab = request.GET.get('tab') if request.GET.get('tab') in ('diagram', 'flows', 'data') else 'diagram'
    return render(request, 'platform_admin/architecture.html', {
        'tab': tab, 'diagram': arch.diagram(data, live), 'panel': arch.panel_data(data, live), 'flows': data.get('flow', []),
        'layers': arch.LAYERS, 'tags': arch.TAGS, 'models': arch.models() if tab == 'data' else [],
        'drift': arch.drift(data), 'live': live, 'agent': arch.agent_counts(), 'apps': arch.local_apps(),
        'endpoints': arch.api_endpoints(), 'push_types': data.get('push_types', {}), 'namespaces': arch.url_namespaces()})


def _options():
    from events import display, layout, live_activity, theme
    screens = []
    for screen in layout.SCREENS:
        types = layout.CATALOGUE.get(screen, {})
        screens.append({'key': screen, 'label': layout.SCREEN_LABELS.get(screen, screen),
                        'sections': [{'type': t, **spec} for t, spec in types.items()],
                        'defaults': [s.get('type') for s in layout.DEFAULTS.get(screen, [])]})
    return {
        'display': {'tabs': display.TABS, 'max_tabs': display.MAX_TABS, 'presets': display.PRESETS,
                    'locations_styles': display.LOCATIONS_STYLES, 'schedule_styles': display.SCHEDULE_STYLES,
                    'secondary_panes': display.SECONDARY_PANES, 'overridable': display.OVERRIDABLE_KEYS,
                    'bools': display.BOOL_KEYS, 'preset_defaults': display.PRESET_DEFAULTS,
                    'labels': display.LABELS, 'schema': display.SCHEMA_VERSION},
        'catalogue_version': layout.CATALOGUE_VERSION, 'screens': screens, 'conditions': layout.CONDITION_LABELS,
        'live_activity': {'follows': live_activity.FOLLOWS, 'buttons': live_activity.BUTTONS,
                          'default': live_activity.DEFAULT, 'max_stale': live_activity.MAX_STALE},
        'theme_roles': ['primary', 'secondary', 'tertiary'], 'theme_default': getattr(theme, 'DEFAULT', {}),
    }


@agent_action('platform_app_options', 'Every server-driven option the apps read (superusers): display config, tabs, '
              'styles, the screen layout catalogue (sections, parts, versions), theme roles, Live Activity settings, '
              'with their defaults.', kind='read')
@requires('platform_admin.view_architecture')
def app_options(request):
    return render(request, 'platform_admin/app_options.html', {'o': _options()})


BRAND = {
    'colours': [('Brand purple', '#431F86', 'Fills, the app icon, buttons'), ('Purple (dark mode)', '#9D7BEA', 'Tint on dark'),
                ('Highlight', '#7B4FD6', 'Gradients, highlights'), ('Soft lavender', '#F0EAFC', 'Tinted backgrounds'),
                ('Ink', '#1B1830', 'Text'), ('Muted', '#6B6880', 'Secondary text')],
    'voice': ['Plain words, short sentences: say what happens ("Publish 5 changes to 214 phones?").',
              'Friendly and direct, never cute; no exclamation marks in the product.',
              'Name things the way organizers do: places, schedule, notifications, the app.',
              'Times in the event’s zone, numbers with thousands separators.'],
    'do': ['Use the mark on white or the brand purple with clear space of one “a” height.',
           'Write "Attnd" with a capital A in text; the wordmark is lower case "attnd."',
           'Use the system font stack (no web fonts).'],
    'dont': ['Stretch, rotate, recolour or outline the mark.', 'Put the mark on busy photos without a plate.',
             'Use gradients other than purple → highlight.', 'Write ATTND in capitals or "Attend".'],
    'type': [('Headlines', 'System UI, 700, tight'), ('Body', 'System UI, 400, 1.5 line height'),
             ('Numbers', 'Tabular figures')],
    'files': [('App icon 1024 (PNG)', '/icon-512.png'), ('Icon 192 (PNG)', '/icon-192.png'),
              ('Favicon (ICO)', '/favicon.ico'), ('Apple touch icon 180 (PNG)', '/apple-touch-icon.png'),
              ('Maskable icon 512 (PNG)', '/icon-maskable-512.png')],
}

MARK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80" role="img" aria-label="attnd.">'
            '<text x="0" y="60" font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" font-size="64" '
            'font-weight="800" fill="#431F86">attnd<tspan fill="#7B4FD6">.</tspan></text></svg>')
ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="Attnd app icon">'
            '<rect width="512" height="512" rx="112" fill="#431F86"/><text x="256" y="330" text-anchor="middle" '
            'font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" font-size="300" font-weight="800" '
            'fill="#fff">a<tspan fill="#B79CF2">.</tspan></text></svg>')


def _brand_zip():
    import io
    import zipfile
    from pathlib import Path

    from django.conf import settings
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('attnd-wordmark.svg', MARK_SVG)
        zf.writestr('attnd-app-icon.svg', ICON_SVG)
        public_dir = Path(settings.BASE_DIR) / 'public'
        for _label, path in BRAND['files']:
            f = public_dir / path.lstrip('/')
            if f.exists():
                zf.write(f, f.name)
        src = Path(settings.BASE_DIR) / 'brand' / 'app-icon-1024.png'
        if src.exists():
            zf.write(src, 'attnd-app-icon-1024.png')
        zf.writestr('colours.txt', '\n'.join(f'{n}: {h} ({use})' for n, h, use in BRAND['colours']) + '\n')
    return out.getvalue()


def _brand(request, template):
    if request.GET.get('download') == 'zip':
        response = HttpResponse(_brand_zip(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="attnd-brand.zip"'
        return response
    if request.GET.get('file') in ('wordmark.svg', 'icon.svg'):
        response = HttpResponse(MARK_SVG if request.GET['file'] == 'wordmark.svg' else ICON_SVG, content_type='image/svg+xml')
        response['Content-Disposition'] = f'attachment; filename="attnd-{request.GET["file"]}"'
        return response
    return render(request, template, {'b': BRAND, 'mark_svg': MARK_SVG, 'icon_svg': ICON_SVG})


@agent_action('platform_brand', "Attnd's brand center (staff): logo, mark, app icon (SVG and PNG), colours, type, voice, "
              "do's and don'ts, a zip of everything; the public copy is /brand/.", kind='read')
@requires('platform_admin.view_platformsettings')
def brand(request):
    return _brand(request, 'platform_admin/brand.html')


@public
def brand_public(request):
    return _brand(request, 'platform_admin/brand_public.html')

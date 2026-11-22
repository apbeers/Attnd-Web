"""Batch 7 Q1 / P0 #3 (Nov 22, 2026): Home is always on, first and the default for apps that draw sections, so it is no
longer part of an organizer's saved tab list. Saved lists lose `home` (and a saved `default_tab: home`); the server
adds Home in front when it resolves the tabs. Lists that are still over the limit are cut at resolve time (fit_tabs)."""
from django.db import migrations


def normalise(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    for event in Event.objects.exclude(display_config={}).only('pk', 'display_config').iterator():
        cfg = dict(event.display_config or {})
        changed = False
        if isinstance(cfg.get('tabs'), list) and 'home' in cfg['tabs']:
            cfg['tabs'] = [t for t in cfg['tabs'] if t != 'home']
            changed = True
        if cfg.get('default_tab') == 'home':
            cfg.pop('default_tab')
            changed = True
        if changed:
            Event.objects.filter(pk=event.pk).update(display_config=cfg)


class Migration(migrations.Migration):
    dependencies = [('events', '0051_published_bundle')]
    operations = [migrations.RunPython(normalise, migrations.RunPython.noop)]

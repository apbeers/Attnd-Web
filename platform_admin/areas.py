"""The Platform page's sub-tabs (owner, Nov 22, 2026): every admin tool lives under /console/platform/, the console's
top bar has one "Platform" entry for anyone with any platform permission, and each sub-tab is gated by its own
permission (attnd.permissions: Staff areas for staff, the Platform admin's own tabs for platform admins). The areas
keep their URLs (old links work); they draw this nav at the top (`{% platform_nav "diagnostics" %}`)."""

from django.urls import reverse

# (key, label, url name, query, permission)
AREAS = [
    ('health', 'Health', 'platform:home', '?tab=overview', 'platform_admin.change_platformsettings'),
    ('customers', 'Customers', 'platform:home', '?tab=customers', 'platform_admin.change_platformsettings'),
    ('requests', 'Upgrade requests', 'platform:home', '?tab=requests', 'platform_admin.change_platformsettings'),
    ('pricing', 'Pricing', 'platform:home', '?tab=pricing', 'platform_admin.change_platformsettings'),
    ('overrides', 'Overrides', 'platform:home', '?tab=events', 'platform_admin.change_platformsettings'),
    ('ai', 'AI spend', 'platform:home', '?tab=ai', 'credit.view_aispend'),
    ('diagnostics', 'Diagnostics', 'diagnostics:overview', '', 'diagnostics.view_diagnosticsession'),
    ('prospects', 'Prospects', 'prospects:list', '', 'prospects.view_prospect'),
    ('claims', 'Claims', 'prospects:claims', '', 'prospects.view_prospect'),
    ('studio_reports', 'Studio reports', 'studio:reports', '', 'studio.view_sitereport'),
    ('app_usage', 'App usage', 'console:admin_usage', '', 'events.view_all_usage'),
    ('architecture', 'Architecture', 'platform:architecture', '', 'platform_admin.view_architecture'),
    ('app_options', 'App options', 'platform:app_options', '', 'platform_admin.view_architecture'),
    ('brand', 'Brand', 'platform:brand', '', 'platform_admin.view_platformsettings'),
    ('emails', 'Emails', 'platform:emails', '', 'platform_admin.view_emaillog'),
]
# Platform home's own ?tab= values → the area key
HOME_TABS = {'overview': 'health', 'customers': 'customers', 'requests': 'requests', 'pricing': 'pricing',
             'events': 'overrides', 'ai': 'ai'}


def visible(user):
    """[{key, label, url}] the person may open, in order."""
    if not getattr(user, 'is_authenticated', False):
        return []
    return [{'key': key, 'label': label, 'url': reverse(name) + query}
            for key, label, name, query, perm in AREAS if user.has_perm(perm)]


def allowed(user, key):
    return any(a['key'] == key for a in visible(user))


def has_platform(user):
    return bool(visible(user))


def first_url(user):
    areas = visible(user)
    return areas[0]['url'] if areas else ''


def actions(user):
    """Extra links on the Platform page: Preview onboarding (anyone with the page) and the Django admin."""
    out = []
    if has_platform(user):
        out.append({'label': 'Preview onboarding', 'url': reverse('console:dashboard') + '?onboarding=1',
                    'title': "What a new organizer sees (your data isn't touched)"})
    if getattr(user, 'is_superuser', False):
        out.append({'label': 'Django admin', 'url': reverse('admin:index'), 'title': ''})
    return out

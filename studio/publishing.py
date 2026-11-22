"""Sites, versions, undo/restore and publishing (spec §2)."""

import logging

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from events import content
from events.display import clean_overrides
from events.models import Event
from studio import hosting
from studio.models import Site, SiteVersion, StudioAsset, unique_slug

logger = logging.getLogger(__name__)


def get_site(event):
    return Site.objects.filter(event=event).select_related('draft', 'published').first()


def ensure_site(event, user=None):
    """The event's Site, created (with an empty first version) when Studio is first opened."""
    site = get_site(event)
    if site is not None:
        return site
    with transaction.atomic():
        site = Site.objects.create(event=event, slug=unique_slug(event.name))
        look = {'accent_color': event.accent_color or '', 'display': clean_overrides(event.display_config)}
        version = SiteVersion.objects.create(site=site, number=1, files={}, app_look=look, summary='Started',
                                             source=SiteVersion.SOURCE_START, created_by=user)
        site.draft = version
        site.save(update_fields=['draft'])
    return site


def new_version(site, user, files=None, app_look=None, flyer_asset=..., summary='', source=SiteVersion.SOURCE_CODE):
    """A new version based on the draft with the given parts replaced; it becomes the draft."""
    draft = site.draft
    with transaction.atomic():
        version = SiteVersion.objects.create(
            site=site, number=site.next_number(),
            files=files if files is not None else dict(draft.files if draft else {}),
            app_look=app_look if app_look is not None else dict(draft.app_look if draft else {}),
            flyer_asset=(draft.flyer_asset if draft else None) if flyer_asset is ... else flyer_asset,
            summary=summary[:256], source=source, created_by=user)
        site.draft = version
        site.save(update_fields=['draft'])
    return version


def restore(site, version, user):
    return new_version(site, user, files=dict(version.files), app_look=dict(version.app_look),
                       flyer_asset=version.flyer_asset, summary=f'Restored version {version.number}',
                       source=SiteVersion.SOURCE_RESTORE)


def undo_target(site):
    """The version before the draft (by number), or None."""
    if not site.draft_id:
        return None
    return site.versions.filter(number__lt=site.draft.number).order_by('-number').first()


def logo_url(site, look):
    asset_id = (look or {}).get('logo_asset_id')
    if not asset_id:
        return None
    asset = StudioAsset.objects.filter(pk=asset_id, event=site.event).first()
    return hosting.site_url(site, asset.site_path) if asset else None


def apply_look(site, version):
    """Copy a version's app look and flyer onto the event (what the apps show). Saves the event."""
    event = site.event
    look = version.app_look or {}
    fields = []
    accent = look.get('accent_color')
    if accent and accent != event.accent_color:
        event.accent_color = accent
        fields.append('accent_color')
    logo = logo_url(site, look)
    if logo and logo != event.logo_url:
        event.logo_url = logo
        fields.append('logo_url')
    if look.get('display'):
        merged = clean_overrides({**(event.display_config or {}), **look['display']})
        if merged != event.display_config:
            event.display_config = merged
            fields.append('display_config')
    flyer = version.flyer_asset
    if flyer is not None and str(flyer.pk) != site.applied_flyer:
        from studio.assets import read_bytes
        try:
            event.flyer.save(flyer.name, ContentFile(read_bytes(flyer)), save=False)
            fields.append('flyer')
            site.applied_flyer = str(flyer.pk)
            site.save(update_fields=['applied_flyer'])
        except Exception:
            logger.exception('Could not copy flyer %s to event %s', flyer.pk, event.pk)
    if fields:
        event.save(update_fields=fields)
    return fields


def publish(site, version, user):
    """Make `version` live: the site serves it, the event gets its look and flyer, and apps get website_url."""
    event = site.event
    with transaction.atomic(), content.batch_changes(event):
        site.published = version
        site.published_at = timezone.now()
        site.published_by = user
        site.save(update_fields=['published', 'published_at', 'published_by'])
        apply_look(site, version)
    _attendees_now(event)
    from attnd import indexnow
    indexnow.site_changed(site)
    return site


def unpublish(site):
    with transaction.atomic(), content.batch_changes(site.event):
        site.published = None
        site.save(update_fields=['published'])
    _attendees_now(site.event)
    from attnd import indexnow
    indexnow.site_changed(site)  # gone: engines drop it sooner
    return site


def _attendees_now(event):
    """The website's address (bundle `event.website_url`) reaches attendees at once, without the app content's
    Publish (§4: events.drafts overlays it live); its app look waits for Publish changes like other edits."""
    from events import drafts
    drafts.sync(event.pk)


def suspend(site, reason):
    with content.batch_changes(site.event):
        site.suspended, site.suspended_reason = True, reason[:256]
        site.save(update_fields=['suspended', 'suspended_reason'])
    _attendees_now(site.event)
    from attnd import indexnow
    indexnow.site_changed(site)


def reinstate(site):
    with content.batch_changes(site.event):
        site.suspended, site.suspended_reason = False, ''
        site.save(update_fields=['suspended', 'suspended_reason'])
    _attendees_now(site.event)


def switch_plan(event, plan):
    if plan not in dict(Event.PLAN_CHOICES):
        raise ValueError(plan)
    with content.batch_changes(event):  # website_url depends on the plan
        event.plan = plan
        event.save(update_fields=['plan'])


def website_url(event):
    """For the bundle (API-016): the live site's URL or None (a site goes live only on the Studio plan or with the
    owner's override, platform_admin.plans.can_go_live)."""
    from platform_admin.plans import can_go_live
    if not can_go_live(event):
        return None
    site = Site.objects.filter(event=event).first()
    if site is None or not site.published_id or site.suspended:
        return None
    return hosting.site_url(site) or None

"""Public routes on SITES_HOST (spec §4): hosted pages, the data library, assets, forms and reports.

Only reachable through studio.urls_sites, which SitesHostMiddleware selects for the sites host; attnd.app never
routes here. Nothing here reads or sets cookies.
"""

import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail
from django.http import (Http404, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect, JsonResponse)
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from attnd.links import qr_svg
from attnd.ratelimit import RateLimit, client_ip
from events.models import Event
from studio import hosting
from studio.models import FormSubmission, Site, SiteReport, SiteVersion, StudioAsset, StudioRun
from studio.sitedata import join_url, site_data

logger = logging.getLogger(__name__)

SITE_STATIC = Path(__file__).resolve().parent / 'site_static'
CONTENT_TYPES = {'html': 'text/html; charset=utf-8', 'css': 'text/css; charset=utf-8',
                 'js': 'text/javascript; charset=utf-8', 'json': 'application/json; charset=utf-8',
                 'svg': 'image/svg+xml', 'txt': 'text/plain; charset=utf-8'}
FORM_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')
MAX_FIELDS, MAX_VALUE, MAX_BODY = 30, 5000, 20_000


@lru_cache(maxsize=8)
def _static_text(name):
    return (SITE_STATIC / name).read_text()


class Target:
    """What a request is for: the site, the version or working files to show, and the page base URL."""

    def __init__(self, site, files, version, base, preview, run=None):
        self.site, self.files, self.version, self.base, self.preview, self.run = site, files, version, base, preview, run


def _target(slug, token, asset=False):
    site = Site.objects.select_related('event', 'published', 'draft').filter(slug=slug).first()
    if site is None:
        raise Http404
    if token is None:
        from platform_admin.plans import can_go_live
        if asset and not site.suspended and can_go_live(site.event):
            # Assets stay reachable while the site is unpublished: the app's logo and flyer may point at them.
            files = site.published.files if site.published else {}
            return Target(site, files, site.published, f'/{site.slug}/', preview=False)
        if not site.is_live:
            raise Http404
        return Target(site, site.published.files, site.published, f'/{site.slug}/', preview=False)
    payload = hosting.read_preview_token(site, token)
    if payload is None:
        raise Http404
    base = f'/{site.slug}/_preview/{token}/'
    if payload.get('r'):
        run = StudioRun.objects.filter(pk=payload['r'], site=site).first()
        if run is None:
            raise Http404
        files = run.working_files if run.working_files is not None else (site.draft.files if site.draft else {})
        return Target(site, files, site.draft, base, preview=True, run=run)
    version = site.draft
    if payload.get('v'):
        version = SiteVersion.objects.filter(pk=payload['v'], site=site).first()
        if version is None:
            raise Http404
    return Target(site, version.files if version else {}, version, base, preview=True)


def _page_response(target, text, status=200):
    canonical = '' if target.preview or status != 200 else hosting.sites_origin() + target.base + getattr(target, 'path', '')
    body = hosting.render_page(text, target.base, preview=target.preview, bridge=target.preview, canonical=canonical)
    response = HttpResponse(body, content_type=CONTENT_TYPES['html'], status=status)
    response['Cache-Control'] = 'no-store' if target.preview else 'public, max-age=60'
    if target.preview:
        response['X-Robots-Tag'] = 'noindex'
    return response


def _not_found(target):
    page = target.files.get('404.html')
    if page:
        return _page_response(target, page, status=404)
    raise Http404


@csrf_exempt
def serve(request, slug, rest=None):
    if rest is None:
        return HttpResponseRedirect(f'/{slug}/')
    path = rest.lstrip('/')
    token = None
    if path.startswith('_preview/'):
        token, _, path = path[len('_preview/'):].partition('/')
        if not token:
            raise Http404
    target = _target(slug, token, asset=path.startswith('_attnd/assets/'))
    if path.startswith('_attnd/'):
        return _internal(request, target, path[len('_attnd/'):])
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    files = target.files
    candidates = ['index.html'] if path in ('', 'index') else (
        [path + 'index.html'] if path.endswith('/') else [path, path + '.html', path + '/index.html'])
    for candidate in candidates:
        if candidate in files:
            ext = candidate.rsplit('.', 1)[-1]
            if ext == 'html':
                target.path = '' if candidate == 'index.html' else (path if not path.endswith('.html') else path)
                return _page_response(target, files[candidate])
            response = HttpResponse(files[candidate], content_type=CONTENT_TYPES.get(ext, 'text/plain'))
            response['Cache-Control'] = 'no-store' if target.preview else 'public, max-age=60'
            return response
    return _not_found(target)


def _internal(request, target, path):
    if path == 'attnd.js':
        response = HttpResponse(_static_text('attnd.js'), content_type=CONTENT_TYPES['js'])
        response['Cache-Control'] = 'public, max-age=300'
        return response
    if path == 'bridge.js' and target.preview:
        response = HttpResponse(_static_text('bridge.js').replace(
            '__CONSOLE_ORIGINS__', json.dumps(settings.STUDIO_CONSOLE_ORIGINS)), content_type=CONTENT_TYPES['js'])
        response['Cache-Control'] = 'no-store'
        return response
    if path == 'data.json':
        return _data(request, target)
    if path == 'qr.svg':
        return _qr(target)
    if path.startswith('assets/'):
        return _asset(target, path[len('assets/'):])
    if path.startswith('forms/'):
        return _form(request, target, path[len('forms/'):].strip('/'))
    if path in ('report', 'report/'):
        return _report(request, target)
    raise Http404


def _data(request, target):
    site, event = target.site, target.site.event
    etag = f'"s{site.pk}-c{event.content_version}-{"p" if target.preview else "l"}{site.published_id or 0}"'
    if request.headers.get('If-None-Match') == etag:
        response = HttpResponse(status=304)
    else:
        absolute_base = hosting.sites_origin() + target.base
        response = JsonResponse(site_data(site, absolute_base), json_dumps_params={'ensure_ascii': False})
    response['ETag'] = etag
    response['Cache-Control'] = 'no-cache'
    response['Access-Control-Allow-Origin'] = '*'  # public data; previews run in an opaque-origin sandbox
    return response


def _qr(target):
    response = HttpResponse(qr_svg(join_url(target.site.event)), content_type='image/svg+xml')
    response['Cache-Control'] = 'public, max-age=86400'
    response['Access-Control-Allow-Origin'] = '*'
    return response


def _asset(target, rest):
    asset_id, _, name = rest.partition('/')
    try:
        asset = StudioAsset.objects.filter(pk=asset_id, event=target.site.event).first()
    except Exception:  # not a UUID
        asset = None
    if asset is None or name != asset.name:
        raise Http404
    try:
        url = asset.file.url
    except Exception:
        url = ''
    if asset.kind == StudioAsset.KIND_IMAGE and url.startswith('https://'):
        response = HttpResponseRedirect(url)  # straight from the storage CDN (Cloudinary)
        response['Cache-Control'] = 'public, max-age=86400'
        return response
    from studio.assets import read_bytes
    try:
        data = read_bytes(asset)
    except Exception:
        logger.warning('Asset %s could not be read', asset.pk, exc_info=True)
        raise Http404
    response = HttpResponse(data, content_type=asset.content_type)
    response['Cache-Control'] = 'public, max-age=31536000, immutable'
    response['Access-Control-Allow-Origin'] = '*'  # fonts are loaded with CORS
    if asset.kind == StudioAsset.KIND_SVG:
        response.studio_csp = hosting.SVG_CSP  # opened directly, an SVG's scripts must not run
    if asset.kind == StudioAsset.KIND_PDF:
        response['Content-Disposition'] = f'inline; filename="{asset.name}"'
    return response


def _wants_json(request):
    return 'application/json' in request.headers.get('Accept', '')


def _form_done(request, target, status=200, message=None):
    if _wants_json(request):
        return JsonResponse({'ok': status == 200, 'preview': target.preview,
                             **({'error': message} if message else {})}, status=status)
    return render(request, 'studio/site_form_done.html', {
        'site': target.site, 'base': target.base, 'error': message}, status=status)


def _form(request, target, name):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    site = target.site
    if not FORM_NAME_RE.match(name):
        raise Http404
    if len(request.body) > MAX_BODY:
        return _form_done(request, target, 413, 'That was too long to send.')
    data = {}
    for key in request.POST:
        if key.startswith('_') or key == 'csrfmiddlewaretoken':
            continue
        values = [v.strip()[:MAX_VALUE] for v in request.POST.getlist(key) if v.strip()]
        if values:
            data[key[:64]] = values[0] if len(values) == 1 else ', '.join(values)[:MAX_VALUE]
        if len(data) >= MAX_FIELDS:
            break
    # Spam: a filled honeypot or a form sent within 2 s of the page loading look like success but aren't kept.
    honeypot = request.POST.get('_hp', '').strip()
    try:
        too_fast = request.POST.get('_ts') and time.time() * 1000 - float(request.POST['_ts']) < 2000
    except ValueError:
        too_fast = False
    if honeypot or too_fast or not data:
        return _form_done(request, target)
    if target.preview:
        return _form_done(request, target)  # previews never store entries
    ip_limit, site_limit = RateLimit('studio_form_ip'), RateLimit('studio_form_site')
    ip_key = f'{site.pk}:{client_ip(request)}'
    if ip_limit.exceeded(ip_key) or site_limit.exceeded(site.pk):
        return _form_done(request, target, 429, 'Too many entries. Please try again later.')
    ip_limit.hit(ip_key)
    site_limit.hit(site.pk)
    page = (request.POST.get('_page') or '')[:200]
    submission = FormSubmission.objects.create(site=site, form=name, data=data, page=page)
    if site.notify_on_forms:
        _notify(site, submission)
    return _form_done(request, target)


def _notify(site, submission):
    from console.mail import site_url
    from events.models import EventMember
    event = site.event
    emails = {event.user.email} if event.user and event.user.email else set()
    emails |= set(EventMember.objects.filter(event=event, role=EventMember.ROLE_ADMIN)
                  .exclude(user__email='').values_list('user__email', flat=True))
    if not emails:
        return
    lines = '\n'.join(f'{k}: {v}' for k, v in submission.data.items())
    try:
        send_mail(f'New “{submission.form}” entry on {event.name}',
                  f'{lines}\n\nAll entries: {site_url(f"/console/events/{event.pk}/studio/")}#forms\n',
                  from_email=None, recipient_list=sorted(emails))
    except Exception:
        logger.exception('Could not email the form entry for site %s', site.pk)


def _report(request, target):
    site = target.site
    if request.method == 'POST':
        limit = RateLimit('studio_report_ip')
        ip = client_ip(request)
        reason = request.POST.get('reason', '')
        if request.POST.get('_hp'):
            return render(request, 'studio/site_report.html', {'site': site, 'base': target.base, 'done': True})
        if limit.exceeded(ip):
            return render(request, 'studio/site_report.html', {
                'site': site, 'base': target.base, 'reasons': SiteReport.REASONS,
                'error': 'Too many reports from your network. Please try again later.'}, status=429)
        if reason not in dict(SiteReport.REASONS):
            return render(request, 'studio/site_report.html', {
                'site': site, 'base': target.base, 'reasons': SiteReport.REASONS,
                'error': 'Please pick a reason.'}, status=400)
        limit.hit(ip)
        if not target.preview:
            SiteReport.objects.create(site=site, reason=reason, details=request.POST.get('details', '')[:2000],
                                      email=request.POST.get('email', '')[:254] if '@' in request.POST.get('email', '')
                                      else '', page=request.POST.get('page', '')[:200])
        return render(request, 'studio/site_report.html', {'site': site, 'base': target.base, 'done': True})
    return render(request, 'studio/site_report.html', {
        'site': site, 'base': target.base, 'reasons': SiteReport.REASONS,
        'page': request.GET.get('page', '')[:200]})


def not_found(request, exception=None):
    return HttpResponse('Not found', status=404, content_type='text/plain')



def sitemap(request):
    """The sites host's sitemap: every published site that is up (not taken down, Studio plan)."""
    from xml.sax.saxutils import escape
    from platform_admin.plans import can_go_live
    sites = [s for s in Site.objects.filter(published__isnull=False, suspended=False).select_related('event')
             .order_by('pk') if can_go_live(s.event)]
    rows = ''.join(f'<url><loc>{escape(hosting.sites_origin() + "/" + s.slug + "/")}</loc>'
                   f'{"<lastmod>" + s.published_at.date().isoformat() + "</lastmod>" if s.published_at else ""}</url>'
                   for s in sites)
    return HttpResponse('<?xml version="1.0" encoding="UTF-8"?>'
                        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>',
                        content_type='application/xml')

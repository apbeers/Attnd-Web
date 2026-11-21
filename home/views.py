import json
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from home import onboarding


def _release() -> str:
    """The deployed Heroku release ("v123"), set only with the runtime-dyno-metadata feature; else empty."""
    return os.getenv('HEROKU_RELEASE_VERSION', '')


def _structured_data(site_url):
    """schema.org for search engines: the organization, the site and the apps."""
    return json.dumps([
        {'@context': 'https://schema.org', '@type': 'Organization', 'name': 'Attnd', 'url': f'{site_url}/',
         'logo': f'{site_url}/icon-512.png'},
        {'@context': 'https://schema.org', '@type': 'WebSite', 'name': 'Attnd', 'url': f'{site_url}/'},
        {'@context': 'https://schema.org', '@type': 'SoftwareApplication', 'name': 'Attnd',
         'applicationCategory': 'LifestyleApplication', 'operatingSystem': 'iOS, Android',
         'offers': {'@type': 'Offer', 'price': '0', 'priceCurrency': 'USD'},
         'description': 'Live maps, schedules and updates for events, built from your website or flyer.'},
    ]).replace('</', '<\\/')


def home(request):
    """/: signed-in people go straight to the console (owner, Nov 21, 2026); /home/ always shows the home page."""
    if request.user.is_authenticated and request.path == '/':
        return redirect('console:dashboard')
    site_url = getattr(settings, 'SITE_URL', 'https://attnd.app').rstrip('/')
    context = onboarding.page_context(request)
    context.update(release=_release(), site_url=site_url, structured_data=_structured_data(site_url))
    return render(request, 'home/home.html', context)


def privacy(request):
    return render(request, template_name='home/privacy.html',
                  context={'contact_email': getattr(settings, 'CONTACT_EMAIL', '')})


@require_POST
def start(request):
    """The composer: keep what the visitor gave, then sign up (or straight on to the console when signed in), where
    it becomes their AI import (home.onboarding.consume)."""
    from attnd.ratelimit import RateLimit, client_ip
    from home.models import HomeMetric
    back = reverse('console:dashboard') if request.user.is_authenticated else reverse('home:home_page') + '#try'
    limit = RateLimit('pending_import_ip')
    if limit.exceeded(client_ip(request)):
        messages.error(request, 'That’s a lot of events in an hour. Try again a little later.')
        return redirect(back)
    pending, error = onboarding.save_pending(request, request.POST.get('text', ''), request.FILES.get('upload'),
                                             request.POST.get('time_zone', ''))
    if error:
        messages.error(request, error)
        return redirect(back)
    limit.hit(client_ip(request))
    HomeMetric.add('submit')
    if request.user.is_authenticated:
        return redirect('console:dashboard')
    return redirect(reverse('console:signup') + '?building=1')


@csrf_exempt
@require_POST
def metric(request):
    """Anonymous counts of the page's own taps (navigator.sendBeacon; no cookie, identifier or text is kept)."""
    from attnd.ratelimit import RateLimit, client_ip
    from home.models import HomeMetric
    limit = RateLimit('home_metric_ip')
    ip = client_ip(request)
    if not limit.exceeded(ip):
        limit.hit(ip)
        try:
            data = json.loads(request.body or b'{}')
        except (ValueError, UnicodeDecodeError):
            data = {}
        if isinstance(data, dict):
            HomeMetric.add(str(data.get('name', ''))[:32], str(data.get('detail', ''))[:64])
    return HttpResponse(status=204)


def near(request):
    """"Happening near you" for the browser's own location (only if the visitor allowed it): the cards' HTML."""
    try:
        lat, lng = float(request.GET['lat']), float(request.GET['lng'])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError
    except (KeyError, ValueError):
        return JsonResponse({'error': 'lat and lng are required numbers'}, status=400)
    near_you, _ = onboarding.gallery((lat, lng))
    html = ''.join(render_to_string('home/_card.html', {'c': c}, request=request) for c in near_you)
    return JsonResponse({'count': len(near_you), 'html': html})


@login_required
def claim(request, event_code):
    """"Is this your event? Claim it": a prospect's test event. Never automatic: staff approve it in Prospects."""
    from prospects import claims
    event = claims.claimable(event_code)
    if event is None:
        raise Http404
    form = claims.ClaimForm(request.POST or None, initial={'email': request.user.email})
    if request.method == 'POST' and form.is_valid():
        ok, text = claims.request_claim(request.user, event, form.cleaned_data)
        (messages.success if ok else messages.error)(request, text)
        if ok:
            return redirect('console:dashboard')
    return render(request, 'home/claim.html', {'event': event, 'form': form})

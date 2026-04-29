import base64
import io
import json
import logging
from datetime import datetime

import qrcode
import qrcode.image.svg

from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django.conf import settings

from events.models import Event, Location, ScheduledItem, generate_event_code
from scrape.service import scrape_event

logger = logging.getLogger(__name__)

TRIAL_USERNAME = "trialuser"
RATE_LIMIT_PER_IP = 5       # max requests per window
RATE_LIMIT_WINDOW = 3600    # 1 hour in seconds


def _get_or_create_trial_user() -> User:
    user, created = User.objects.get_or_create(
        username=TRIAL_USERNAME,
        defaults={"email": "trial@attnd.app"},
    )
    if created:
        user.set_unusable_password()
        user.save()
    return user



def _check_rate_limit(ip: str) -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    key = f"scrape_rl:{ip}"
    count = cache.get(key, 0)
    if count >= RATE_LIMIT_PER_IP:
        return False
    cache.set(key, count + 1, RATE_LIMIT_WINDOW)
    return True


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


@csrf_exempt
@require_POST
def scrape_view(request):
    # Rate limit
    ip = _client_ip(request)
    if not _check_rate_limit(ip):
        return JsonResponse(
            {"error": "Too many requests. Please try again in an hour."},
            status=429,
        )

    # Parse body
    try:
        body = json.loads(request.body)
        url = body.get("url", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not url:
        return JsonResponse({"error": "A URL is required."}, status=400)

    if not url.startswith(("http://", "https://")):
        return JsonResponse({"error": "URL must start with http:// or https://"}, status=400)

    # Scrape
    logger.info("Scrape request from %s for URL: %s", ip, url)
    try:
        data = scrape_event(url, google_api_key=getattr(settings, "GOOGLE_PLACES_API_KEY", ""))
    except ValueError as exc:
        logger.warning("Scrape ValueError for %s: %s", url, exc)
        return JsonResponse({"error": str(exc)}, status=422)
    except Exception as exc:
        logger.exception("Scrape unexpected error for %s", url)
        return JsonResponse({"error": f"Scraping failed: {exc}"}, status=500)

    # Parse start_time — guard against None before calling parse_datetime
    from datetime import timezone, timedelta
    raw_time = data.get("start_time")
    if not raw_time:
        start_time = datetime.now(timezone.utc) + timedelta(days=30)
    else:
        start_time = parse_datetime(raw_time)
        if start_time is None:
            try:
                start_time = datetime.fromisoformat(raw_time)
            except ValueError:
                start_time = datetime.now(timezone.utc) + timedelta(days=30)

    # Write to DB
    trial_user = _get_or_create_trial_user()
    event_code = generate_event_code()

    event = Event.objects.create(
        name=data["name"][:256],
        event_code=event_code,
        description=data["description"][:2048],
        latitude=data.get("latitude") or 0,
        longitude=data.get("longitude") or 0,
        start_time=start_time,
        paid=False,
        public=True,
        address=data.get("address", "")[:512],
        user=trial_user,
    )

    locations_created = 0
    scheduled_created = 0
    for loc in data.get("locations", []):
        try:
            location_obj = Location.objects.create(
                name=loc["name"][:256],
                snippet=loc["snippet"][:200],
                description=loc["description"][:1024],
                website=loc.get("website") or None,
                image_url=loc.get("image_url") or None,
                address=loc.get("address", "")[:512],
                latitude=loc.get("latitude") or 0,
                longitude=loc.get("longitude") or 0,
                user=trial_user,
                event=event,
            )
            locations_created += 1

            for item in loc.get("scheduled_items", []):
                try:
                    raw_time = item.get("start_time", "")
                    if raw_time:
                        item_start = parse_datetime(raw_time)
                        if item_start is None:
                            try:
                                item_start = datetime.fromisoformat(raw_time)
                            except ValueError:
                                item_start = start_time
                    else:
                        item_start = start_time
                    ScheduledItem.objects.create(
                        name=item["name"][:256],
                        description=item["description"][:1024],
                        start_time=item_start,
                        location=location_obj,
                        event=event,
                        user=trial_user,
                    )
                    scheduled_created += 1
                except Exception:
                    pass

        except Exception:
            pass  # Don't fail the whole request over a single bad location

    # Generate QR code for the join URL
    join_url = request.build_absolute_uri(f"/join/{event_code}/")
    qr_img = qrcode.make(join_url, box_size=8, border=2)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    logger.info("Event created: %s (code=%s, locations=%d, scheduled_items=%d)", event.name, event_code, locations_created, scheduled_created)

    return JsonResponse({
        "event_code": event_code,
        "event_name": event.name,
        "location_count": locations_created,
        "qr_code": f"data:image/png;base64,{qr_b64}",
        "join_url": join_url,
    }, status=201)

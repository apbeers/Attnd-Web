import base64
import io
import json
import logging
import queue
import threading
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import qrcode

from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from django.utils.dateparse import parse_datetime
from django.utils import timezone as django_tz
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django.conf import settings

from events.models import Event, Location, ScheduledItem, generate_event_code
from scrape.service import scrape_event

logger = logging.getLogger(__name__)

TRIAL_USERNAME = "trialuser"
RATE_LIMIT_PER_IP = 5
RATE_LIMIT_WINDOW = 3600


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


def _parse_timezone(tz_name: str) -> ZoneInfo:
    """Parse an IANA timezone name; fall back to UTC on any failure."""
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, Exception):
            pass
    return ZoneInfo("UTC")


def _parse_dt(raw: str | None, user_tz: ZoneInfo) -> datetime:
    """
    Parse a raw datetime string into a timezone-aware datetime.
    Naive strings are treated as being in the user's local timezone.
    Returns 30 days from now (UTC) if parsing fails or raw is empty.
    """
    if not raw:
        return django_tz.now() + __import__('datetime').timedelta(days=30)

    dt = parse_datetime(raw)
    if dt is not None:
        return dt if dt.tzinfo else dt.replace(tzinfo=user_tz)

    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=user_tz)
    except ValueError:
        return django_tz.now() + __import__('datetime').timedelta(days=30)


@csrf_exempt
@require_POST
def scrape_view(request):
    # ── Synchronous guards (can fail-fast before streaming starts) ───────────

    ip = _client_ip(request)
    if not _check_rate_limit(ip):
        return JsonResponse(
            {"error": "Too many requests. Please try again in an hour."},
            status=429,
        )

    try:
        body = json.loads(request.body)
        url = body.get("url", "").strip()
        tz_name = body.get("timezone", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not url:
        return JsonResponse({"error": "A URL is required."}, status=400)
    if not url.startswith(("http://", "https://")):
        return JsonResponse({"error": "URL must start with http:// or https://"}, status=400)

    user_tz = _parse_timezone(tz_name)
    google_api_key = getattr(settings, "GOOGLE_PLACES_API_KEY", "")
    join_url_base = request.build_absolute_uri("/join/")

    logger.info("Scrape request from %s for URL: %s (tz=%s)", ip, url, tz_name or "UTC")

    # ── Run the slow scrape in a background thread ───────────────────────────
    result_queue: queue.Queue = queue.Queue()

    def worker():
        try:
            data = scrape_event(url, google_api_key=google_api_key)
            result_queue.put(("ok", data))
        except ValueError as exc:
            logger.warning("Scrape ValueError for %s: %s", url, exc)
            result_queue.put(("error", str(exc)))
        except Exception as exc:
            logger.exception("Scrape unexpected error for %s", url)
            result_queue.put(("error", f"Scraping failed: {exc}"))

    threading.Thread(target=worker, daemon=True).start()

    # ── Stream keepalive newlines, then the final JSON ───────────────────────
    # Each yielded chunk resets Heroku's 30-second router timeout (H12).
    # The client reads the full body with resp.text() and JSON.parse(text.trim()).

    def generate():
        # Yield a newline every 8 s while the scrape is running
        while True:
            try:
                outcome, payload = result_queue.get(timeout=8)
                break
            except queue.Empty:
                yield b"\n"

        if outcome != "ok":
            yield json.dumps({"error": payload}).encode()
            return

        data = payload

        try:
            trial_user = _get_or_create_trial_user()
            event_code = generate_event_code()
            start_time = _parse_dt(data.get("start_time"), user_tz)

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
                            item_start = _parse_dt(item.get("start_time", ""), user_tz)
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
                    pass

            join_url = f"{join_url_base}{event_code}/"
            qr_img = qrcode.make(join_url, box_size=8, border=2)
            buf = io.BytesIO()
            qr_img.save(buf, format="PNG")
            qr_b64 = base64.b64encode(buf.getvalue()).decode()

            logger.info(
                "Event created: %s (code=%s, locations=%d, scheduled_items=%d)",
                event.name, event_code, locations_created, scheduled_created,
            )

            yield json.dumps({
                "event_code": event_code,
                "event_name": event.name,
                "location_count": locations_created,
                "qr_code": f"data:image/png;base64,{qr_b64}",
                "join_url": join_url,
            }).encode()

        except Exception as exc:
            logger.exception("Error persisting scrape result")
            yield json.dumps({"error": f"Failed to save event: {exc}"}).encode()

    response = StreamingHttpResponse(generate(), content_type="application/json")
    response["X-Accel-Buffering"] = "no"
    return response

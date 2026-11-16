"""
Public "try it" endpoints used by the homepage. No account needed while SIGNUPS_OPEN is on; while it is off
(invite-only) POST /scrape/ answers 403 for anonymous visitors (console.signups.trial_import_open).

    POST /scrape/                 {"url", "timezone"} → 202 {"job_id", "status_url"}
    GET  /scrape/jobs/<job_id>/   → {"status", "progress", "error", ...result}
"""

import base64
import io
import json
import logging

import qrcode
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from attnd.ratelimit import client_ip
from console.signups import INVITE_ONLY_MESSAGE, trial_import_open
from scrape import jobs
from scrape.diff import valid_tz
from scrape.models import IngestJob

logger = logging.getLogger(__name__)

RATE_LIMIT_PER_IP = 5
RATE_LIMIT_WINDOW = 3600


def _check_rate_limit(ip: str) -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    key = f"scrape_rl:{ip}"
    count = cache.get(key, 0)
    if count >= RATE_LIMIT_PER_IP:
        return False
    cache.set(key, count + 1, RATE_LIMIT_WINDOW)
    return True


def _client_ip(request) -> str:
    return client_ip(request)  # right-most X-Forwarded-For hop (the one Heroku's router added)


def qr_data_url(text: str) -> str:
    img = qrcode.make(text, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@csrf_exempt
@require_POST
def scrape_view(request):
    if not trial_import_open(request):
        # Invite-only (SIGNUPS_OPEN off): no anonymous AI imports, so no OpenAI calls for visitors.
        return JsonResponse({"error": INVITE_ONLY_MESSAGE, "invite_only": True}, status=403)
    try:
        body = json.loads(request.body)
        url = str(body.get("url", "")).strip()
        tz_name = str(body.get("timezone", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if not url:
        return JsonResponse({"error": "A URL is required."}, status=400)
    if not url.startswith(("http://", "https://")):
        return JsonResponse({"error": "URL must start with http:// or https://"}, status=400)

    ip = _client_ip(request)
    if not _check_rate_limit(ip):
        return JsonResponse({"error": "Too many requests. Please try again in an hour."}, status=429)

    job = IngestJob.objects.create(
        kind=IngestJob.KIND_CREATE, trigger=IngestJob.TRIGGER_TRIAL,
        urls=[url[:2000]], timezone=valid_tz(tz_name) or "UTC",
    )
    logger.info("Trial import queued from %s for %s (job %s)", ip, url, job.pk)
    jobs.enqueue(job)
    return JsonResponse(
        {"job_id": str(job.pk), "status_url": reverse("scrape:job_status", args=[job.pk])},
        status=202,
    )


@require_GET
def job_status(request, job_id):
    job = get_object_or_404(IngestJob, pk=job_id, trigger=IngestJob.TRIGGER_TRIAL)
    payload = {"status": job.status, "progress": job.progress, "error": job.error or None}
    if job.status == IngestJob.DONE and job.event_id:
        code = job.result.get("event_code")
        join_url = request.build_absolute_uri(f"/join/{code}/")
        payload.update(job.result)
        payload.update({
            "join_url": join_url,
            "qr_code": qr_data_url(join_url),
            "claim_url": reverse("console:signup") + f"?claim={code}",
        })
    return JsonResponse(payload)

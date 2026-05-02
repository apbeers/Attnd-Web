#!/usr/bin/env python3
"""Smoke tests for the Attnd API.

Runs against the URL in ATTND_BASE_URL (default: https://dev.attnd.app).
Requires ATTND_API_KEY to be set.

Usage:
    python infra/scripts/smoke_test.py
"""

import os
import sys
import httpx

BASE_URL = os.environ.get("ATTND_BASE_URL", "https://dev.attnd.app").rstrip("/")
API_KEY = os.environ.get("ATTND_API_KEY", "")
API_BASE = f"{BASE_URL}/api/v1/events"

if not API_KEY:
    print("ERROR: ATTND_API_KEY is not set", file=sys.stderr)
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS  {name}")
        passed += 1
    else:
        print(f"  FAIL  {name}" + (f": {detail}" if detail else ""))
        failed += 1


with httpx.Client(headers=HEADERS, timeout=15) as client:

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    print("\n--- Health ---")
    r = client.get(f"{BASE_URL}/health/")
    check("GET /health/ returns 200", r.status_code == 200)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    print("\n--- Auth ---")
    r = client.get(f"{API_BASE}/nearby_events/0/0/",
                   headers={"Accept": "application/json"})  # no auth header
    check("Missing auth returns 401", r.status_code == 401)

    # ------------------------------------------------------------------
    # Nearby events
    # ------------------------------------------------------------------
    print("\n--- Nearby Events ---")
    # Fort Worth, TX — roughly where test events are seeded
    r = client.get(f"{API_BASE}/nearby_events/32.7555/-97.3308/")
    check("GET nearby_events returns 200", r.status_code == 200)
    if r.status_code == 200:
        data = r.json()
        check("nearby_events has 'results' list", isinstance(data.get("results"), list))
        check("nearby_events has 'count' int", isinstance(data.get("count"), int))
        if data["results"]:
            item = data["results"][0]
            event = item.get("event", {})
            check("result has event.id (int)", isinstance(event.get("id"), int))
            check("result has event.name (str)", isinstance(event.get("name"), str))
            check("result has event.event_code (str)", isinstance(event.get("event_code"), str))
            check("result has event.latitude (float)", isinstance(event.get("latitude"), (int, float)))
            check("result has event.longitude (float)", isinstance(event.get("longitude"), (int, float)))
            check("result has event.start_time (str)", isinstance(event.get("start_time"), str))
            check("result has event.paid (bool)", isinstance(event.get("paid"), bool))
            check("result has distance_miles (float)", isinstance(item.get("distance_miles"), (int, float)))
            check("result has starts_in_hours (float)", isinstance(item.get("starts_in_hours"), (int, float)))

            first_event_id = event["id"]
            first_event_code = event["event_code"]
        else:
            print("  SKIP  No nearby events found — skipping event-specific checks")
            first_event_id = None
            first_event_code = None
    else:
        first_event_id = None
        first_event_code = None

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    print("\n--- Lookup ---")
    r = client.get(f"{API_BASE}/lookup/")  # missing q
    check("GET /lookup/ with no q returns 400", r.status_code == 400)

    if first_event_code:
        r = client.get(f"{API_BASE}/lookup/?q={first_event_code}")
        check("GET /lookup/?q=<code> returns 200", r.status_code == 200)
        if r.status_code == 200:
            ev = r.json()
            check("lookup result has id", "id" in ev)
            check("lookup result has event_code", "event_code" in ev)

    # ------------------------------------------------------------------
    # Single event
    # ------------------------------------------------------------------
    print("\n--- Single Event ---")
    if first_event_id:
        r = client.get(f"{API_BASE}/{first_event_id}/")
        check(f"GET /events/{first_event_id}/ returns 200", r.status_code == 200)

    r = client.get(f"{API_BASE}/999999999/")
    check("GET non-existent event returns 404", r.status_code == 404)

    # ------------------------------------------------------------------
    # Locations
    # ------------------------------------------------------------------
    print("\n--- Locations ---")
    if first_event_id:
        r = client.get(f"{API_BASE}/{first_event_id}/locations/")
        check(f"GET /events/{first_event_id}/locations/ returns 200", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            check("locations has 'results' list", isinstance(data.get("results"), list))
            if data["results"]:
                loc = data["results"][0]
                check("location has id", "id" in loc)
                check("location has name", "name" in loc)
                check("location has scheduled_items list",
                      isinstance(loc.get("scheduled_items"), list))

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------
    print("\n--- Schedule ---")
    if first_event_id:
        r = client.get(f"{API_BASE}/{first_event_id}/schedule/")
        check(f"GET /events/{first_event_id}/schedule/ returns 200", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            check("schedule has 'results' list", isinstance(data.get("results"), list))

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    print("\n--- Notifications ---")
    if first_event_id:
        r = client.get(f"{API_BASE}/{first_event_id}/notifications/")
        check(f"GET /events/{first_event_id}/notifications/ returns 200", r.status_code == 200)

    # ------------------------------------------------------------------
    # Join / Leave
    # ------------------------------------------------------------------
    print("\n--- Join / Leave ---")
    if first_event_id:
        test_device = "smoke-test-device-00000000"
        r = client.post(
            f"{API_BASE}/{first_event_id}/join/{test_device}/",
            json={"platform": "android"},
        )
        check("POST /join/<device>/ returns 200", r.status_code == 200)
        if r.status_code == 200:
            check("join returns {status: ok}", r.json().get("status") == "ok")

        r = client.post(f"{API_BASE}/{first_event_id}/leave/{test_device}/")
        check("POST /leave/<device>/ returns 200", r.status_code == 200)

    # ------------------------------------------------------------------
    # Push subscription
    # ------------------------------------------------------------------
    print("\n--- Push Subscription ---")
    test_device = "smoke-test-device-00000000"
    r = client.post(f"{API_BASE}/pushnotifications/subscribe/{test_device}/")
    check("POST /pushnotifications/subscribe/ returns 200", r.status_code == 200)

    r = client.post(f"{API_BASE}/pushnotifications/unsubscribe/{test_device}/")
    check("POST /pushnotifications/unsubscribe/ returns 200", r.status_code == 200)

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"Smoke tests: {passed} passed, {failed} failed")
print(f"Target: {BASE_URL}")
print("="*50)

if failed:
    sys.exit(1)

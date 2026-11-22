"""Long-poll load: N preview phones wait on changes/ while a probe measures /health/ latency. stdlib only."""
import json, sys, threading, time, urllib.request, statistics
# Usage: ATTND_LOAD_KEY=<api key> python3 scripts/longpoll_load.py <origin> <event id> <phones> <since>
# The preview phones must exist (ids load-prev-0 … load-prev-N-1, live preview_until). Prints JSON only.
import os
base, event_id, n, version = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
key = os.environ.get('ATTND_LOAD_KEY', '')
held, quick, errors = [], [], []
def poll(i):
    req = urllib.request.Request(f'{base}/api/v2/events/{event_id}/changes/?since={version}',
        headers={'Authorization': f'Bearer {key}', 'X-Device-Id': f'load-prev-{i}', 'User-Agent': 'attnd-ci load'})
    t = time.monotonic()
    try:
        body = json.loads(urllib.request.urlopen(req, timeout=40).read())
        dt = time.monotonic() - t
        (held if dt > 5 else quick).append(dt)
    except Exception as e:
        errors.append(str(e)[:80])
threads = [threading.Thread(target=poll, args=(i,)) for i in range(n)]
for th in threads: th.start()
time.sleep(1)
lat = []
end = time.monotonic() + 15
while time.monotonic() < end:
    t = time.monotonic()
    try:
        urllib.request.urlopen(f'{base}/health/', timeout=30).read(); lat.append((time.monotonic() - t) * 1000)
    except Exception as e:
        errors.append('health ' + str(e)[:60])
    time.sleep(0.1)
for th in threads: th.join()
print(json.dumps({'pollers': n, 'held_open': len(held), 'answered_at_once': len(quick), 'errors': len(errors),
                  'health_p50_ms': round(statistics.median(lat), 1), 'health_max_ms': round(max(lat), 1),
                  'health_requests': len(lat), 'sample_errors': errors[:3]}))

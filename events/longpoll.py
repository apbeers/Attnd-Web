"""One cap for every request that waits (long-polls) in a web process: Ask Attnd's run poll and the preview phone's
changes poll (API-026). gunicorn runs 4 threads; at most MAX_WAITING of them may sleep in a long-poll at once."""
import threading

MAX_WAITING = 2
_waiting = threading.BoundedSemaphore(MAX_WAITING)


def acquire():
    return _waiting.acquire(blocking=False)


def release():
    _waiting.release()

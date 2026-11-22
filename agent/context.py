"""Which Ask Attnd run and step the current request belongs to (for logs and History summaries)."""

import contextlib
import contextvars

_current = contextvars.ContextVar('agent_step', default=None)


@contextlib.contextmanager
def acting(run, step=None):
    token = _current.set((run.pk, step.seq if step else 0))
    try:
        yield
    finally:
        _current.reset(token)


def current():
    """(run id, step seq) while Ask Attnd runs a view, else None."""
    return _current.get()

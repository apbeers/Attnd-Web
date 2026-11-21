"""Stage F5: Django's tasks framework on our own database queue (`background.backend.DatabaseBackend`).

`enqueue(task, *args)` queues a django.tasks @task when TASKS uses the database backend (a `run_tasks` worker dyno
or Heroku Scheduler's `run_tasks --once` runs it), and otherwise keeps today's behaviour: the work runs after the
transaction commits in one of this process's background threads.
"""


def database_queue():
    from django.tasks import task_backends

    from background.backend import DatabaseBackend
    return isinstance(task_backends['default'], DatabaseBackend)


def enqueue(task, *args, fallback=None, **kwargs):
    """Queue `task` in the database, or call `fallback(*args, **kwargs)` (the in-process path) when the queue is off."""
    if database_queue():
        return task.enqueue(*args, **kwargs)
    if fallback is not None:
        fallback(*args, **kwargs)
    return None

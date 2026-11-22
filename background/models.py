"""Stage F5: Django's tasks framework (django.tasks) with our own database-backed queue (no third-party package)."""

from django.db import models
from django.tasks import TaskResultStatus


class QueuedTask(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    task_path = models.CharField(max_length=255)  # module.function of the @task
    queue_name = models.CharField(max_length=64, default='default')
    priority = models.SmallIntegerField(default=0)
    args = models.JSONField(default=list, blank=True)
    kwargs = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=TaskResultStatus.choices, default=TaskResultStatus.READY)
    # (no db_index: queued_next starts with status)
    run_after = models.DateTimeField(null=True, blank=True)
    enqueued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    worker_ids = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)  # [{exception_class_path, traceback}]
    return_value = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'queue_name', '-priority', 'enqueued_at'], name='queued_next')]

    def __str__(self):
        return f'{self.task_path} ({self.status})'

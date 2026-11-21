from django.contrib import admin

from background.models import QueuedTask


@admin.register(QueuedTask)
class QueuedTaskAdmin(admin.ModelAdmin):
    list_display = ('task_path', 'status', 'queue_name', 'priority', 'enqueued_at', 'finished_at')
    list_filter = ('status', 'queue_name', 'task_path')
    readonly_fields = [f.name for f in QueuedTask._meta.fields]

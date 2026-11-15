from django.contrib import admin

from studio.models import FormSubmission, Site, SiteReport, SiteVersion, StudioAccess, StudioAsset, StudioRun, StudioUsage


@admin.register(StudioAccess)
class StudioAccessAdmin(admin.ModelAdmin):
    list_display = ['email', 'invited_by', 'created_at']
    search_fields = ['email']


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['slug', 'event', 'published', 'suspended', 'created_at']
    list_filter = ['suspended']
    search_fields = ['slug', 'event__name', 'event__event_code']
    raw_id_fields = ['event', 'draft', 'published', 'published_by']


@admin.register(SiteVersion)
class SiteVersionAdmin(admin.ModelAdmin):
    list_display = ['site', 'number', 'source', 'summary', 'created_by', 'created_at']
    raw_id_fields = ['site', 'flyer_asset', 'created_by', 'run']


@admin.register(StudioAsset)
class StudioAssetAdmin(admin.ModelAdmin):
    list_display = ['name', 'event', 'kind', 'size', 'generated', 'created_at']
    list_filter = ['kind', 'generated']
    raw_id_fields = ['event', 'created_by']


@admin.register(StudioRun)
class StudioRunAdmin(admin.ModelAdmin):
    list_display = ['site', 'kind', 'status', 'model', 'cost_usd', 'created_at']
    list_filter = ['status', 'kind', 'provider']
    raw_id_fields = ['site', 'created_by', 'base_version', 'result_version']


@admin.register(StudioUsage)
class StudioUsageAdmin(admin.ModelAdmin):
    list_display = ['event', 'kind', 'model', 'input_tokens', 'output_tokens', 'images', 'cost_usd', 'created_at']
    list_filter = ['kind', 'provider']


@admin.register(SiteReport)
class SiteReportAdmin(admin.ModelAdmin):
    list_display = ['site', 'reason', 'status', 'created_at']
    list_filter = ['status', 'reason']


@admin.register(FormSubmission)
class FormSubmissionAdmin(admin.ModelAdmin):
    list_display = ['site', 'form', 'created_at']
    list_filter = ['form']

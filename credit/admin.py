from django.contrib import admin

from credit.models import AiCreditNotice, AiLimitOverride, AiLimits, AiSpend


@admin.register(AiSpend)
class AiSpendAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'visitor', 'event', 'feature', 'model', 'cost_usd', 'counted', 'background')
    list_filter = ('feature', 'provider', 'counted', 'background')
    search_fields = ('user__email', 'model', 'request_id', 'visitor')
    raw_id_fields = ('user', 'event')
    date_hierarchy = 'created_at'


@admin.register(AiLimits)
class AiLimitsAdmin(admin.ModelAdmin):
    list_display = ('monthly_default_usd', 'visitor_allowance_usd', 'platform_alert_usd', 'updated_at', 'updated_by')


@admin.register(AiLimitOverride)
class AiLimitOverrideAdmin(admin.ModelAdmin):
    list_display = ('user', 'monthly_usd', 'unlimited', 'note', 'updated_at', 'updated_by')
    raw_id_fields = ('user', 'updated_by')


@admin.register(AiCreditNotice)
class AiCreditNoticeAdmin(admin.ModelAdmin):
    list_display = ('month', 'user', 'level')

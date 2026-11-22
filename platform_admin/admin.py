from django.contrib import admin

from platform_admin.models import EventOverride, PaymentRecord, PlatformSettings, UpgradeRequest

admin.site.register(PlatformSettings)
admin.site.register(UpgradeRequest, list_display=('event', 'kind', 'status', 'price_usd', 'created_at'), list_filter=('status', 'kind'))
admin.site.register(PaymentRecord, list_display=('event', 'plan', 'amount_usd', 'paid_at'))
admin.site.register(EventOverride, list_display=('event', 'free_device_limit', 'no_free_limit', 'allow_publishing'))

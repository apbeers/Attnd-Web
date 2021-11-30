from django.contrib import admin

from console.models import Order


class OrderAdmin(admin.ModelAdmin):
    list_filter = ['user', 'event']


admin.site.register(Order, OrderAdmin)

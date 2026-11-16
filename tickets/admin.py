from django.contrib import admin

from tickets.models import (CheckIn, DoorStaff, Order, OrderItem, Payout, PayoutAccount, PromoCode, Refund,
                            StripeEvent, Ticket, TicketSettings, TicketType)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ['ticket_type', 'quantity', 'unit_price_cents', 'discount_cents']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['ref', 'event', 'email', 'status', 'total_cents', 'created_at']
    list_filter = ['status', 'comp']
    search_fields = ['ref', 'email', 'name', 'stripe_payment_intent_id']
    inlines = [OrderItemInline]
    readonly_fields = ['public_id', 'key', 'stripe_session_id', 'stripe_payment_intent_id', 'stripe_charge_id',
                       'stripe_transfer_id']


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['code', 'event', 'ticket_type', 'holder_email', 'status', 'version', 'checked_in_at']
    list_filter = ['status']
    search_fields = ['code', 'holder_email', 'holder_name']
    readonly_fields = ['public_id', 'key']


for model in (TicketSettings, TicketType, PromoCode, PayoutAccount, Payout, Refund, DoorStaff, CheckIn, StripeEvent):
    admin.site.register(model)

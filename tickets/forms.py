import re

from django import forms

from console.forms.event_forms import DT_FORMATS, DT_WIDGET, StyledFormMixin
from tickets import money
from tickets.models import CapacityPool, DoorStaff, PromoCode, TicketSettings, TicketType


class MoneyField(forms.CharField):
    """Dollars in the form, cents in the model."""

    def __init__(self, **kwargs):
        kwargs.setdefault('widget', forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '25.00'}))
        super().__init__(**kwargs)

    def prepare_value(self, value):
        if isinstance(value, int):
            return money.dollars(value)
        return value

    def to_python(self, value):
        if value in (None, ''):
            return 0 if self.required is False else None
        try:
            return money.parse_dollars(value)
        except (ValueError, ArithmeticError) as exc:
            raise forms.ValidationError(str(exc) if isinstance(exc, ValueError) else 'Enter an amount')


class TicketTypeForm(StyledFormMixin, forms.ModelForm):
    price_cents = MoneyField(label='Price ($)', required=False,
                             help_text='Before the processing fee, which buyers pay. Pay what you want: the minimum.')
    sales_start = forms.DateTimeField(required=False, widget=DT_WIDGET, input_formats=DT_FORMATS,
                                      label='Sales start', help_text='Empty = now')
    sales_end = forms.DateTimeField(required=False, widget=DT_WIDGET, input_formats=DT_FORMATS,
                                    label='Sales end', help_text='Empty = until the event ends')
    valid_on = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}),
                               label='Day ticket for', help_text='Only for one day of a multi-day event')

    class Meta:
        model = TicketType
        fields = ['name', 'description', 'kind', 'price_cents', 'quantity', 'pool', 'sales_start', 'sales_end',
                  'min_per_order', 'max_per_order', 'hidden', 'unlock_code', 'add_on', 'valid_on', 'reentry',
                  'transferable', 'waitlist', 'sort_order']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}
        labels = {'quantity': 'Tickets available', 'pool': 'Shared capacity', 'reentry': 'Re-entry allowed',
                  'hidden': 'Hidden (needs the unlock code)', 'add_on': 'Add-on (e.g. parking)'}

    def __init__(self, *args, event=None, sold=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.sold = sold
        self.fields['pool'].queryset = CapacityPool.objects.filter(event=event)
        self.fields['pool'].required = False

    def clean(self):
        data = super().clean()
        kind = data.get('kind')
        price = data.get('price_cents') or 0
        if kind == TicketType.KIND_PAID and price <= 0:
            self.add_error('price_cents', 'Paid tickets need a price (or choose Free / RSVP).')
        if kind == TicketType.KIND_FREE:
            data['price_cents'] = 0
        if price and price < 50 and kind != TicketType.KIND_FREE:
            self.add_error('price_cents', 'Stripe’s minimum charge is $0.50.')
        quantity = data.get('quantity')
        if quantity is not None and quantity < self.sold:
            self.add_error('quantity', f'{self.sold} are already sold or held.')
        if data.get('hidden') and not (data.get('unlock_code') or '').strip():
            self.add_error('unlock_code', 'Hidden tickets need an unlock code.')
        if (data.get('min_per_order') or 1) > (data.get('max_per_order') or 1):
            self.add_error('max_per_order', 'Must be at least the minimum.')
        if data.get('sales_start') and data.get('sales_end') and data['sales_end'] <= data['sales_start']:
            self.add_error('sales_end', 'Must be after the start.')
        data['unlock_code'] = (data.get('unlock_code') or '').strip().upper()
        return data


class PoolForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CapacityPool
        fields = ['name', 'capacity']
        labels = {'name': 'Name (e.g. Venue)', 'capacity': 'Capacity'}


class PromoForm(StyledFormMixin, forms.ModelForm):
    amount_off_cents = MoneyField(label='Amount off per ticket ($)', required=False)
    starts_at = forms.DateTimeField(required=False, widget=DT_WIDGET, input_formats=DT_FORMATS)
    ends_at = forms.DateTimeField(required=False, widget=DT_WIDGET, input_formats=DT_FORMATS)

    class Meta:
        model = PromoCode
        fields = ['code', 'percent_off', 'amount_off_cents', 'ticket_types', 'max_uses', 'starts_at', 'ends_at']
        widgets = {'ticket_types': forms.CheckboxSelectMultiple}

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields['ticket_types'].queryset = TicketType.objects.filter(event=event, active=True)

    def clean_code(self):
        code = self.cleaned_data['code'].strip().upper()
        if not re.fullmatch(r'[A-Z0-9_-]{3,32}', code):
            raise forms.ValidationError('3–32 letters, digits, - or _.')
        if PromoCode.objects.filter(event=self.event, code=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('That code exists already.')
        return code

    def clean(self):
        data = super().clean()
        pct, amount = data.get('percent_off'), data.get('amount_off_cents')
        if bool(pct) == bool(amount):
            raise forms.ValidationError('Give either a percentage or an amount off.')
        if pct and pct > 100:
            self.add_error('percent_off', 'At most 100.')
        data['amount_off_cents'] = amount or None
        return data


QUESTION_HELP = ('One per line: Label | text, select: A, B, C or checkbox | required | per ticket. '
                 'Example: T-shirt size | select: S, M, L | required | per ticket')


def parse_questions(text):
    out, seen = [], set()
    for line in (text or '').splitlines():
        parts = [p.strip() for p in line.split('|')]
        if not parts or not parts[0]:
            continue
        label = parts[0][:120]
        kind, options = 'text', []
        flags = [p.lower() for p in parts[1:]]
        for p in parts[1:]:
            low = p.lower()
            if low.startswith('select'):
                kind = 'select'
                options = [o.strip()[:60] for o in p.split(':', 1)[1].split(',') if o.strip()][:20] if ':' in p else []
            elif low == 'checkbox':
                kind = 'checkbox'
        qid = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:30] or f'q{len(out) + 1}'
        while qid in seen:
            qid += '_'
        seen.add(qid)
        out.append({'id': qid, 'label': label, 'kind': kind, 'options': options, 'required': 'required' in flags,
                    'per_ticket': 'per ticket' in flags})
    return out[:20]


def format_questions(questions):
    lines = []
    for q in questions or []:
        parts = [q.get('label', '')]
        if q.get('kind') == 'select':
            parts.append('select: ' + ', '.join(q.get('options') or []))
        elif q.get('kind') == 'checkbox':
            parts.append('checkbox')
        if q.get('required'):
            parts.append('required')
        if q.get('per_ticket'):
            parts.append('per ticket')
        lines.append(' | '.join(parts))
    return '\n'.join(lines)


class TicketSettingsForm(StyledFormMixin, forms.ModelForm):
    questions_text = forms.CharField(label='Questions for buyers', required=False, help_text=QUESTION_HELP,
                                     widget=forms.Textarea(attrs={'rows': 3}))

    class Meta:
        model = TicketSettings
        fields = ['page_text', 'refund_policy', 'refund_days_before', 'refund_policy_text', 'faq', 'max_per_order',
                  'payout_delay_days']
        widgets = {'page_text': forms.Textarea(attrs={'rows': 3}), 'refund_policy_text': forms.Textarea(attrs={'rows': 3}),
                   'faq': forms.Textarea(attrs={'rows': 4})}
        labels = {'page_text': 'Ticket page intro', 'refund_days_before': 'Refund deadline (days before the event)',
                  'refund_policy_text': 'Refund policy (shown to buyers)', 'faq': 'FAQ',
                  'payout_delay_days': 'Pay out this many days after the event'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['questions_text'].initial = format_questions(self.instance.questions)
        self.fields['payout_delay_days'].help_text = 'Empty = the default (3 days)'

    def clean_max_per_order(self):
        from django.conf import settings
        value = self.cleaned_data['max_per_order']
        if not 1 <= value <= settings.TICKETS_MAX_PER_ORDER:
            raise forms.ValidationError(f'Between 1 and {settings.TICKETS_MAX_PER_ORDER}.')
        return value

    def save(self, commit=True):
        self.instance.questions = parse_questions(self.cleaned_data.get('questions_text'))
        return super().save(commit)


class DoorStaffForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DoorStaff
        fields = ['email', 'name']
        labels = {'name': 'Name (optional)'}


class CompForm(StyledFormMixin, forms.Form):
    ticket_type = forms.ModelChoiceField(queryset=TicketType.objects.none())
    quantity = forms.IntegerField(min_value=1, max_value=100, initial=1)
    email = forms.EmailField()
    name = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['ticket_type'].queryset = TicketType.objects.filter(event=event, active=True)


class MessageForm(StyledFormMixin, forms.Form):
    subject = forms.CharField(max_length=160)
    body = forms.CharField(widget=forms.Textarea(attrs={'rows': 6}), max_length=5000)


class DraftForm(StyledFormMixin, forms.Form):
    instructions = forms.CharField(
        label='What tickets do you want?', max_length=2000,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'General admission $25, VIP with drinks $75 (50 of them), '
                                                                'free for kids under 12. Refunds until a week before.'}))

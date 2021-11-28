from django.forms import Form, CharField, HiddenInput


class ValidatePayPalTransactionForm(Form):
    event_id = CharField(max_length=16)
    order_id = CharField(max_length=128)

    class Meta:
        widget = {'event_id': HiddenInput(),
                  'order_id': HiddenInput()}

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0020_rename_payment_id_event_order_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='public',
            field=models.BooleanField(default=False),
        ),
    ]

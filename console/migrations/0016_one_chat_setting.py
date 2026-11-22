"""One "open in chat" setting on every screen (Nov 22, 2026): phone_chat (picked by user agent) merges into
chat_default. Whoever turned the chat off anywhere keeps it off."""
from django.db import migrations


def merge(apps, schema_editor):
    Pref = apps.get_model('console', 'ConsolePreference')
    Pref.objects.filter(phone_chat=False, chat_default=True).update(chat_default=False)


class Migration(migrations.Migration):

    dependencies = [
        ("console", "0015_db_review"),
    ]

    operations = [
        migrations.RunPython(merge, migrations.RunPython.noop),
        migrations.RemoveField(model_name="consolepreference", name="phone_chat"),
    ]

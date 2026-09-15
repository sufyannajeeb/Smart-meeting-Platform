from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("meetings", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="transcript",
            name="summary_json",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

# Generated by Django 4.2.4 on 2023-08-09 13:26

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0012_apidentity"),
        ("social", "0006_alter_localactivity_template"),
    ]

    operations = [
        migrations.AlterField(
            model_name="localactivity",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="users.apidentity"
            ),
        ),
    ]

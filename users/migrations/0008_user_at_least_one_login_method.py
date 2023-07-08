# Generated by Django 3.2.19 on 2023-07-04 02:57

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0007_user_pending_email"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                check=models.Q(
                    ("is_active", False),
                    ("mastodon_username__isnull", False),
                    ("email__isnull", False),
                    _connector="OR",
                ),
                name="at_least_one_login_method",
            ),
        ),
    ]

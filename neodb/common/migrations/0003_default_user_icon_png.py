"""
Data migration: the default user icon changed from avatar.svg to avatar.png.

If SiteConfig stores a stock default explicitly (the old svg one, or the new
png one that 0002 stores on fresh installs because it compares against the
svg-era defaults), drop the key so the site follows the SystemOptions
default; a customized user_icon is left untouched.
"""

from django.db import migrations

STOCK_USER_ICONS = ("/s/img/avatar.svg", "/s/img/avatar.png")


def drop_stock_user_icon(apps, schema_editor):
    SiteConfig = apps.get_model("common", "SiteConfig")
    config = SiteConfig.objects.filter(pk=1).first()
    if config and config.data.get("user_icon") in STOCK_USER_ICONS:
        del config.data["user_icon"]
        config.save(update_fields=["data"])


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0002_import_env_to_siteconfig"),
    ]

    operations = [
        migrations.RunPython(drop_stock_user_icon, migrations.RunPython.noop),
    ]

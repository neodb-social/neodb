"""
Data migration: the default avatar changed from avatar.svg to avatar.png,
and local identities without a custom avatar now serve the default icon in
their ActivityPub actor document.

Per identity, if the profile icon is the old stock default (icon_uri
pointing at avatar.svg), clear it so the identity follows the current
default. Then transition every local identity affected by the new default
(old stock icon_uri, or no icon at all) to the "edited" state so stator
fans out an Update activity and remote servers pick up the new icon; the
"outdated" state would not fan out for local identities. Identities already
in edited/deleted/moved flows are left for their handlers.
"""

from django.db import migrations, models
from django.utils import timezone

OLD_DEFAULT_ICON_PATH = "/s/img/avatar.svg"


def update_default_icons(apps, schema_editor):
    Identity = apps.get_model("users", "Identity")
    local = Identity.objects.filter(local=True, deleted__isnull=True)

    # fan out first, while stock icon_uri values are still recognizable
    no_icon = (models.Q(icon="") | models.Q(icon__isnull=True)) & (
        models.Q(icon_uri="") | models.Q(icon_uri__isnull=True)
    )
    local.filter(state__in=["outdated", "updated"]).filter(
        models.Q(icon_uri__endswith=OLD_DEFAULT_ICON_PATH) | no_icon
    ).update(
        state="edited",
        state_changed=timezone.now(),
        state_next_attempt=None,
        state_locked_until=None,
    )
    local.filter(icon_uri__endswith=OLD_DEFAULT_ICON_PATH).update(icon_uri="")


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0034_identity_ix_identity_handle_ci"),
    ]

    operations = [
        migrations.RunPython(update_default_icons, migrations.RunPython.noop),
    ]

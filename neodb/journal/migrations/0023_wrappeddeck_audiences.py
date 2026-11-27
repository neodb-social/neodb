from django.db import migrations, models

LEVELS = (0, 1, 2)


def to_audiences(apps, schema_editor):
    """One page level becomes three audiences: those at or above the old level keep the old choices,
    and those below it see nothing, so no deck shows more than it did."""
    Deck = apps.get_model("journal", "WrappedDeck")
    for d in Deck.objects.all():
        level = int(d.visibility)
        old_hidden = d.hidden if isinstance(d.hidden, dict) else {}
        old_shown = d.shown if isinstance(d.shown, list) else None
        d.hidden = {
            str(lv): dict(old_hidden) if lv >= level else {"*": True} for lv in LEVELS
        }
        shown = {str(lv): [] for lv in LEVELS if lv < level}
        if old_shown is not None:
            shown.update({str(lv): list(old_shown) for lv in LEVELS if lv >= level})
        d.shown = shown
        d.save(update_fields=["hidden", "shown"])


def to_page_level(apps, schema_editor):
    """Back to one level: the widest audience that sees something, with its choices."""
    Deck = apps.get_model("journal", "WrappedDeck")
    for d in Deck.objects.all():
        hidden = d.hidden if isinstance(d.hidden, dict) else {}
        shown = d.shown if isinstance(d.shown, dict) else {}

        def off(lv):
            o = hidden.get(str(lv)) or {}
            return (
                o.get("*") is True
                and not any(v is False for k, v in o.items() if k != "*")
            ) or shown.get(str(lv)) == []

        level = next((lv for lv in LEVELS if not off(lv)), 2)
        d.visibility = level
        d.hidden = {k: v for k, v in (hidden.get(str(level)) or {}).items() if k != "*"}
        d.shown = shown.get(str(level))
        d.save(update_fields=["visibility", "hidden", "shown"])


class Migration(migrations.Migration):
    dependencies = [("journal", "0022_wrappeddeck")]

    operations = [
        migrations.RunPython(to_audiences, to_page_level),
        migrations.AlterField(
            model_name="wrappeddeck",
            name="shown",
            field=models.JSONField(default=dict),
        ),
        migrations.RemoveField(model_name="wrappeddeck", name="visibility"),
    ]

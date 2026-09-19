import os
from io import StringIO

import pytest
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import override_settings

pytestmark = pytest.mark.django_db(databases="__all__")


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("sitemap", stdout=out, **kwargs)
    return out.getvalue()


def test_sitemap_writes_to_the_given_local_path(tmp_path):
    target = tmp_path / "root" / "sitemap.txt"

    # isolated so the "not in storage" check cannot see a real deployment's
    # sitemap, or one an earlier run of this command left behind
    with override_settings(MEDIA_ROOT=str(tmp_path / "media")):
        output = _run(output=str(target))

        assert not default_storage.exists("export/sitemap.txt")

    assert target.is_file()
    # mkstemp() makes the temp file 0600; the web server needs it readable
    assert oct(os.stat(target).st_mode)[-3:] == "644"
    assert str(target) in output


def test_sitemap_without_a_path_goes_to_media_storage(tmp_path):
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        output = _run()

        assert default_storage.exists("export/sitemap.txt")
        assert "sitemap.txt" in output


def test_sitemap_replaces_the_stored_copy_rather_than_suffixing(tmp_path):
    """FileSystemStorage.save() would pick sitemap_<random>.txt on the second
    run and leave the advertised URL serving the first run's file."""
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        _run()
        _run()

    stored = sorted(p.name for p in (tmp_path / "export").iterdir())
    assert stored == ["sitemap.txt"]

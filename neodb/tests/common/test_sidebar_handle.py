import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory

from users.models import APIdentity, User

EVIL_HANDLE = "x');alert(document.domain);('@evil.example"


@pytest.mark.django_db(databases="__all__")
def test_copy_handle_reads_data_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = User.register(username="sidebaruser").identity
    monkeypatch.setattr(APIdentity, "full_handle", property(lambda self: EVIL_HANDLE))
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    html = render_to_string(
        "_sidebar.html",
        {"identity": identity, "show_profile": 1, "request": request},
    )
    anchor = re.search(r'<a data-handle="([^"]*)"\s+_="([^"]*)">', html)
    assert anchor is not None
    assert anchor.group(1) == "@x&#x27;);alert(document.domain);(&#x27;@evil.example"
    assert anchor.group(2) == (
        "on click call navigator.clipboard.writeText(@data-handle) then halt"
    )

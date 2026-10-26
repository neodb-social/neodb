import pytest
from django.conf import settings
from django.template.loader import render_to_string

from core.models import Config
from users.models import Report


@pytest.mark.django_db
def test_report_email_escapes_the_complaint(identity, domain, config_system):
    report = Report(
        subject_identity=identity,
        source_domain=domain,
        type="remote",
        complaint='<a href="https://evil.test/login">reset your password</a>',
    )

    html = render_to_string(
        "emails/report_new.html",
        {"report": report, "config": Config.system, "settings": settings},
    )

    assert '<a href="https://evil.test/login">' not in html
    assert "&lt;a href=" in html

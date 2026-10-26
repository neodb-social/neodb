import pytest
from django.core.exceptions import ValidationError

from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.mark.parametrize("username", ["admin", "bad name", "x"])
def test_register_rejects_reserved_and_malformed_usernames(username: str) -> None:
    with pytest.raises(ValidationError):
        User.register(email=f"{username.strip()}@example.com", username=username)
    assert not User.objects.filter(username=username).exists()


def test_register_accepts_a_plain_username() -> None:
    user = User.register(email="plain@example.com", username="plain_user")
    assert user.pk

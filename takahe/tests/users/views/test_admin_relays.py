import pytest
from django.test import Client

from users.models import User


@pytest.mark.django_db
@pytest.mark.parametrize(
    "action,data,status",
    [
        ("unsubscribe", {}, 400),
        ("unsubscribe", {"id": "x"}, 400),
        ("remove", {"id": ""}, 400),
        ("remove", {"id": "999999"}, 404),
    ],
)
def test_relay_action_with_bad_id(
    client: Client, user: User, action: str, data: dict, status: int
):
    user.admin = True
    user.save()
    client.force_login(user)
    response = client.post(f"/admin/relays/?{action}", data)
    assert response.status_code == status

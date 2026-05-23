import pytest


@pytest.mark.django_db
def test_set_markers_json_body(api_client):
    """POST with nested JSON body should work (Mastodon/Pleroma compatible format)."""
    response = api_client.post(
        "/api/v1/markers",
        content_type="application/json",
        data={"notifications": {"last_read_id": "46748898"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert "notifications" in data
    assert data["notifications"]["last_read_id"] == "46748898"


@pytest.mark.django_db
def test_set_markers_form_bracket_notation(api_client):
    """POST with bracket-notation form params should still work."""
    response = api_client.post(
        "/api/v1/markers",
        data={"notifications[last_read_id]": "46748898"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "notifications" in data
    assert data["notifications"]["last_read_id"] == "46748898"


@pytest.mark.django_db
def test_set_markers_multiple_timelines(api_client):
    """POST can set multiple timelines at once."""
    response = api_client.post(
        "/api/v1/markers",
        content_type="application/json",
        data={
            "notifications": {"last_read_id": "111"},
            "home": {"last_read_id": "222"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["notifications"]["last_read_id"] == "111"
    assert data["home"]["last_read_id"] == "222"


@pytest.mark.django_db
def test_set_markers_updates_existing(api_client):
    """Posting twice updates the existing marker."""
    api_client.post(
        "/api/v1/markers",
        content_type="application/json",
        data={"notifications": {"last_read_id": "100"}},
    )
    response = api_client.post(
        "/api/v1/markers",
        content_type="application/json",
        data={"notifications": {"last_read_id": "200"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["notifications"]["last_read_id"] == "200"


@pytest.mark.django_db
def test_get_markers(api_client):
    """GET returns previously set markers."""
    api_client.post(
        "/api/v1/markers",
        content_type="application/json",
        data={"notifications": {"last_read_id": "999"}},
    )
    response = api_client.get("/api/v1/markers?timeline[]=notifications")
    assert response.status_code == 200
    data = response.json()
    assert data["notifications"]["last_read_id"] == "999"

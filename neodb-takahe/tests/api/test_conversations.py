import pytest

from activities.models import Post
from activities.models.conversation import Conversation, ConversationMembership


@pytest.mark.django_db
def test_list_conversations_empty(api_client):
    """Empty conversations list when no DMs exist."""
    response = api_client.get("/api/v1/conversations")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.django_db
def test_conversation_created_on_direct_post(api_client, identity, other_identity):
    """Posting a direct status creates a conversation."""
    response = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": f"@{other_identity.username}@{other_identity.domain.domain} hello",
            "visibility": "direct",
        },
    ).json()
    assert response["visibility"] == "direct"
    status_id = response["id"]

    # Verify conversation was created
    post = Post.objects.get(pk=status_id)
    assert post.conversation is not None

    # Conversation should appear in the list
    response = api_client.get("/api/v1/conversations")
    assert response.status_code == 200
    conversations = response.json()
    assert len(conversations) == 1
    conv = conversations[0]
    assert conv["last_status"]["id"] == status_id
    # Author should not be unread for their own message
    assert conv["unread"] is False
    # Accounts should be the other participants (not self)
    assert len(conv["accounts"]) == 1
    assert conv["accounts"][0]["username"] == other_identity.username


@pytest.mark.django_db
def test_conversation_groups_by_participants(api_client, identity, other_identity):
    """Two DMs between the same participants belong to one conversation."""
    # First DM
    r1 = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": f"@{other_identity.username}@{other_identity.domain.domain} msg1",
            "visibility": "direct",
        },
    ).json()
    # Second DM to same person
    r2 = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": f"@{other_identity.username}@{other_identity.domain.domain} msg2",
            "visibility": "direct",
        },
    ).json()

    post1 = Post.objects.get(pk=r1["id"])
    post2 = Post.objects.get(pk=r2["id"])
    assert post1.conversation_id == post2.conversation_id

    # Only one conversation in the list
    response = api_client.get("/api/v1/conversations")
    conversations = response.json()
    assert len(conversations) == 1
    # Last status should be the newer one
    assert conversations[0]["last_status"]["id"] == r2["id"]


@pytest.mark.django_db
def test_mark_conversation_read(api_client, identity, other_identity):
    """Mark conversation as read sets unread to false."""
    # Create a DM
    r = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": f"@{other_identity.username}@{other_identity.domain.domain} hi",
            "visibility": "direct",
        },
    ).json()
    post = Post.objects.get(pk=r["id"])
    conv_id = str(post.conversation_id)

    # Manually mark the sender's membership as unread to test the mark-read endpoint
    membership = ConversationMembership.objects.get(
        conversation_id=conv_id, identity=identity
    )
    membership.unread = True
    membership.save(update_fields=["unread"])

    # Mark as read
    response = api_client.post(f"/api/v1/conversations/{conv_id}/read")
    assert response.status_code == 200
    result = response.json()
    assert result["unread"] is False
    assert result["id"] == conv_id

    # Verify persisted
    membership.refresh_from_db()
    assert membership.unread is False


@pytest.mark.django_db
def test_delete_conversation(api_client, identity, other_identity):
    """Deleting a conversation dismisses it from the list."""
    # Create a DM
    r = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": f"@{other_identity.username}@{other_identity.domain.domain} bye",
            "visibility": "direct",
        },
    ).json()
    post = Post.objects.get(pk=r["id"])
    conv_id = str(post.conversation_id)

    # Conversation should be visible
    conversations = api_client.get("/api/v1/conversations").json()
    assert len(conversations) == 1

    # Delete (dismiss) it
    response = api_client.delete(f"/api/v1/conversations/{conv_id}")
    assert response.status_code == 200

    # Should no longer appear in list
    conversations = api_client.get("/api/v1/conversations").json()
    assert len(conversations) == 0


@pytest.mark.django_db
def test_dismissed_conversation_undismissed_on_new_dm(
    api_client, identity, other_identity
):
    """A new DM in a dismissed conversation brings it back."""
    # Create a DM
    r = api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": f"@{other_identity.username}@{other_identity.domain.domain} first",
            "visibility": "direct",
        },
    ).json()
    post = Post.objects.get(pk=r["id"])
    conv_id = str(post.conversation_id)

    # Dismiss it
    api_client.delete(f"/api/v1/conversations/{conv_id}")
    assert len(api_client.get("/api/v1/conversations").json()) == 0

    # Send another DM to same participants
    api_client.post(
        "/api/v1/statuses",
        content_type="application/json",
        data={
            "status": f"@{other_identity.username}@{other_identity.domain.domain} second",
            "visibility": "direct",
        },
    )

    # Should reappear
    conversations = api_client.get("/api/v1/conversations").json()
    assert len(conversations) == 1
    assert conversations[0]["id"] == conv_id


@pytest.mark.django_db
def test_conversation_unread_for_recipient(identity, other_identity, domain):
    """The recipient of a DM should have unread=True."""
    post = Post.create_local(
        author=identity,
        content=f"@{other_identity.username}@{other_identity.domain.domain} hello",
        visibility=Post.Visibilities.mentioned,
    )
    # Reload post from DB since update_for_post uses queryset update
    post.refresh_from_db()
    if post.conversation is None:
        # Mentions may not have been parsed; add manually and retry
        post.mentions.add(other_identity)
        Conversation.update_for_post(post)

    assert post.conversation is not None

    membership = ConversationMembership.objects.get(
        conversation=post.conversation, identity=other_identity
    )
    assert membership.unread is True

    # Author should not be unread
    author_membership = ConversationMembership.objects.get(
        conversation=post.conversation, identity=identity
    )
    assert author_membership.unread is False


@pytest.mark.django_db
def test_conversation_participant_hash_deterministic():
    """Participant hash is stable regardless of input order."""
    h1 = Conversation.compute_participant_hash({1, 2, 3})
    h2 = Conversation.compute_participant_hash({3, 1, 2})
    assert h1 == h2

import json
from typing import TYPE_CHECKING, Optional

import requests
from django.conf import settings
from django.db import models
from pywebpush import webpush

from core.models import Config
from stator.models import State, StateField, StateGraph, StatorModel

if TYPE_CHECKING:
    from users.models import Identity

PushPolicy = models.TextChoices(
    "PushPolicy",
    ["all", "followed", "follower", "none"],
)


class PushType(models.TextChoices):
    mention = "mention", "Mention"
    status = "status", "Post"
    boost = "reblog", "Boost"
    follow = "follow", "Follow"
    follow_request = "follow_request", "Follow Request"
    favorite = "favourite", "Favorite"
    poll = "poll", "Poll"
    update = "update", "Update"
    quote = "quote", "Quote"
    admin_signup = "admin.sign_up", "Account Signup"
    admin_report = "admin.report", "Report"

    def should_send(self, alerts: dict) -> bool:
        return alerts.get(self.value, False)

    def get_title(self, **kwargs):
        return {
            "mention": "{name} mentioned your post",
            "status": "New post from {name}",
            "reblog": "{name} boosted your post",
            "follow": "{name} is now following you",
            "follow_request": "Follow request from {name}",
            "favourite": "{name} favorited your post",
            "poll": "{name} posted a new poll",
            "update": "Update",
            "quote": "{name} quoted your post",
            "admin.sign_up": "{name} signed up for an account",
            "admin.report": "Report",
        }[self.value].format(**kwargs)


class PushSubscription(models.Model):
    token = models.OneToOneField(
        "api.Token",
        on_delete=models.CASCADE,
        related_name="push_subscription",
    )
    endpoint = models.CharField(max_length=500)
    keys = models.JSONField(blank=True, null=True)
    alerts = models.JSONField(blank=True, null=True)
    policy = models.CharField(
        max_length=8,
        choices=PushPolicy.choices,
        default="all",
    )

    def to_mastodon_json(self):
        return {
            "id": self.pk,
            "endpoint": self.endpoint,
            "alerts": self.alerts or {},
            "policy": self.policy,
            "server_key": settings.SETUP.VAPID_PUBLIC_KEY,
        }

    def update(self, alerts: dict, policy: str):
        """
        Updates this push subscription with the specified alerts and policy.
        """
        self.alerts = alerts
        self.policy = policy
        self.save()

    def notify(
        self,
        type: PushType,
        identity: "Identity",
        source: Optional["Identity"] = None,
        title: str | None = None,
        body: str | None = None,
    ) -> Optional["PushNotification"]:
        """
        Checks the PushType against the configured alerts, and the target and source identities to
        see if a push notification should be sent. If so, schedules it to be sent.
        """
        if not type.should_send(self.alerts):
            return None
        match self.policy:
            case PushPolicy.followed:
                if (
                    not identity.outbound_follows.active()
                    .filter(target=source)
                    .exists()
                ):
                    return None
            case PushPolicy.follower:
                if not identity.inbound_follows.active().filter(source=source).exists():
                    return None
            case PushPolicy.none:
                return None
        if title is None:
            title = type.get_title(name=source.name_or_handle if source else "Someone")
        if body is None:
            body = ""
        icon = source.local_icon_url().absolute if source else Config.system.site_icon
        return PushNotification.objects.create(
            token=self.token,
            type=type,
            icon=icon,
            title=title,
            body=body,
        )


class PushNotificationStates(StateGraph):
    sending = State(try_interval=60, force_initial=True)
    sent = State(delete_after=900)
    failed = State(delete_after=60 * 60 * 24)

    sending.transitions_to(sent)
    sending.transitions_to(failed)
    sending.times_out_to(failed, 600)

    @classmethod
    def handle_sending(cls, instance: "PushNotification"):
        if not settings.SETUP.VAPID_PRIVATE_KEY:
            # No VAPID key, no notifications.
            return cls.failed

        try:
            sub: PushSubscription = instance.token.push_subscription
        except PushSubscription.DoesNotExist:
            # Notifications are not configured.
            return cls.failed

        try:
            session = requests.Session()
            session.verify = False
            webpush(
                {"endpoint": sub.endpoint, "keys": sub.keys},
                json.dumps(instance.to_webpush_json()).encode("utf-8"),
                vapid_private_key=settings.SETUP.VAPID_PRIVATE_KEY,
                content_encoding="aesgcm",
                headers={
                    "content-type": "application/octet-stream",
                },
                requests_session=session,
            )
            return cls.sent
        except Exception:
            return


class PushNotification(StatorModel):
    token = models.ForeignKey(
        "api.Token",
        on_delete=models.CASCADE,
        related_name="push_notifications",
    )
    locale = models.CharField(max_length=2, default="en")
    type = models.CharField(max_length=20, choices=PushType.choices)
    icon = models.CharField(max_length=500)
    title = models.CharField(max_length=100)
    body = models.CharField(max_length=500)

    state = StateField(PushNotificationStates)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def to_webpush_json(self):
        return {
            "access_token": self.token.token,
            "preferred_locale": self.locale,
            "notification_id": self.pk,
            "notification_type": self.type,
            "icon": self.icon,
            "title": self.title,
            "body": self.body,
        }

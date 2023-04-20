import uuid
import django.contrib.postgres.fields as postgres
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.translation import gettext_lazy as _
from common.utils import GenerateDateUUIDMediaFilePath
from django.conf import settings
from management.models import Announcement
from mastodon.api import *
from django.urls import reverse


def report_image_path(instance, filename):
    return GenerateDateUUIDMediaFilePath(
        instance, filename, settings.REPORT_MEDIA_PATH_ROOT
    )


class User(AbstractUser):
    if settings.MASTODON_ALLOW_ANY_SITE:
        username = models.CharField(
            _("username"),
            max_length=150,
            unique=False,
            help_text=_(
                "Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only."
            ),
        )
    following = models.JSONField(default=list)
    mastodon_id = models.CharField(max_length=100, blank=False)
    # mastodon domain name, eg donotban.com
    mastodon_site = models.CharField(max_length=100, blank=False)
    mastodon_token = models.CharField(max_length=2048, default="")
    mastodon_refresh_token = models.CharField(max_length=2048, default="")
    mastodon_locked = models.BooleanField(default=False)
    mastodon_followers = models.JSONField(default=list)
    mastodon_following = models.JSONField(default=list)
    mastodon_mutes = models.JSONField(default=list)
    mastodon_blocks = models.JSONField(default=list)
    mastodon_domain_blocks = models.JSONField(default=list)
    mastodon_account = models.JSONField(default=dict)
    mastodon_last_refresh = models.DateTimeField(default=timezone.now)
    # store the latest read announcement id,
    # every time user read the announcement update this field
    read_announcement_index = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["username", "mastodon_site"], name="unique_user_identity"
            )
        ]

    # def save(self, *args, **kwargs):
    #     """ Automatically populate password field with settings.DEFAULT_PASSWORD before saving."""
    #     self.set_password(settings.DEFAULT_PASSWORD)
    #     return super().save(*args, **kwargs)

    @property
    def mastodon_username(self):
        return self.username + "@" + self.mastodon_site

    @property
    def display_name(self):
        return (
            self.mastodon_account["display_name"]
            if self.mastodon_account
            and "display_name" in self.mastodon_account
            and self.mastodon_account["display_name"]
            else self.mastodon_username
        )

    @property
    def url(self):
        return reverse("journal:user_profile", args=[self.mastodon_username])

    def __str__(self):
        return self.mastodon_username

    def get_preference(self):
        pref = Preference.objects.filter(user=self).first()  # self.preference
        if not pref:
            pref = Preference.objects.create(user=self)
        return pref

    def refresh_mastodon_data(self):
        """Try refresh account data from mastodon server, return true if refreshed successfully, note it will not save to db"""
        self.mastodon_last_refresh = timezone.now()
        code, mastodon_account = verify_account(self.mastodon_site, self.mastodon_token)
        if code == 401 and self.mastodon_refresh_token:
            self.mastodon_token = refresh_access_token(
                self.mastodon_site, self.mastodon_refresh_token
            )
            if self.mastodon_token:
                code, mastodon_account = verify_account(
                    self.mastodon_site, self.mastodon_token
                )
        updated = False
        if mastodon_account:
            self.mastodon_account = mastodon_account
            self.mastodon_locked = mastodon_account["locked"]
            if self.username != mastodon_account["username"]:
                print(f"username changed from {self} to {mastodon_account['username']}")
                self.username = mastodon_account["username"]
            # self.mastodon_token = token
            # user.mastodon_id  = mastodon_account['id']
            self.mastodon_followers = get_related_acct_list(
                self.mastodon_site,
                self.mastodon_token,
                f"/api/v1/accounts/{self.mastodon_id}/followers",
            )
            self.mastodon_following = get_related_acct_list(
                self.mastodon_site,
                self.mastodon_token,
                f"/api/v1/accounts/{self.mastodon_id}/following",
            )
            self.mastodon_mutes = get_related_acct_list(
                self.mastodon_site, self.mastodon_token, "/api/v1/mutes"
            )
            self.mastodon_blocks = get_related_acct_list(
                self.mastodon_site, self.mastodon_token, "/api/v1/blocks"
            )
            self.mastodon_domain_blocks = get_related_acct_list(
                self.mastodon_site, self.mastodon_token, "/api/v1/domain_blocks"
            )
            self.following = self.get_following_ids()
            updated = True
        elif code == 401:
            print(f"401 {self}")
            self.mastodon_token = ""
        return updated

    def get_following_ids(self):
        fl = []
        for m in self.mastodon_following:
            target = User.get(m)
            if target and (
                (not target.mastodon_locked)
                or self.mastodon_username in target.mastodon_followers
            ):
                fl.append(target.pk)
        return fl

    def is_blocking(self, target):
        return (
            (
                target.mastodon_username in self.mastodon_blocks
                or target.mastodon_site in self.mastodon_domain_blocks
            )
            if target.is_authenticated
            else self.preference.no_anonymous_view
        )

    def is_blocked_by(self, target):
        return target.is_authenticated and target.is_blocking(self)

    def is_muting(self, target):
        return target.mastodon_username in self.mastodon_mutes

    def is_following(self, target):
        return (
            self.mastodon_username in target.mastodon_followers
            if target.mastodon_locked
            else self.mastodon_username in target.mastodon_followers
            or target.mastodon_username in self.mastodon_following
        )

    def is_followed_by(self, target):
        return target.is_following(self)

    def get_mark_for_item(self, item):
        params = {item.__class__.__name__.lower() + "_id": item.id, "owner": self}
        mark = item.mark_class.objects.filter(**params).first()
        return mark

    def get_max_visibility(self, viewer):
        if not viewer.is_authenticated:
            return 0
        elif viewer == self:
            return 2
        elif viewer.is_blocked_by(self):
            return -1
        elif viewer.is_following(self):
            return 1
        else:
            return 0

    @property
    def unread_announcements(self):
        unread_announcements = Announcement.objects.filter(
            pk__gt=self.read_announcement_index
        ).order_by("-pk")
        try:
            self.read_announcement_index = Announcement.objects.latest("pk").pk
            self.save(update_fields=["read_announcement_index"])
        except ObjectDoesNotExist:
            # when there is no annoucenment
            pass
        return unread_announcements

    @classmethod
    def get(cls, id):
        if isinstance(id, str):
            try:
                username = id.split("@")[0]
                site = id.split("@")[1]
            except IndexError:
                return None
            query_kwargs = {"username": username, "mastodon_site": site}
        elif isinstance(id, int):
            query_kwargs = {"pk": id}
        else:
            return None
        return User.objects.filter(**query_kwargs).first()


class Preference(models.Model):
    user = models.OneToOneField(User, models.CASCADE, primary_key=True)
    profile_layout = models.JSONField(
        blank=True,
        default=list,
    )
    discover_layout = models.JSONField(
        blank=True,
        default=list,
    )
    export_status = models.JSONField(
        blank=True, null=True, encoder=DjangoJSONEncoder, default=dict
    )
    import_status = models.JSONField(
        blank=True, null=True, encoder=DjangoJSONEncoder, default=dict
    )
    default_no_share = models.BooleanField(default=False)
    default_visibility = models.PositiveSmallIntegerField(default=0)
    classic_homepage = models.PositiveSmallIntegerField(null=False, default=0)
    mastodon_publish_public = models.BooleanField(null=False, default=False)
    mastodon_append_tag = models.CharField(max_length=2048, default="")
    show_last_edit = models.PositiveSmallIntegerField(default=0)
    no_anonymous_view = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return str(self.user)


class Report(models.Model):
    submit_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="sumbitted_reports", null=True
    )
    reported_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="accused_reports", null=True
    )
    image = models.ImageField(
        upload_to=report_image_path,
        height_field=None,
        width_field=None,
        blank=True,
        default="",
    )
    is_read = models.BooleanField(default=False)
    submitted_time = models.DateTimeField(auto_now_add=True)
    message = models.CharField(max_length=1000)

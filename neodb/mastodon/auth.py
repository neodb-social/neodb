from django.contrib.auth.backends import ModelBackend
from django.http import HttpRequest

from mastodon.models.common import SocialAccount


class OAuth2Backend(ModelBackend):
    """Used to glue OAuth2 and Django User model"""

    # "authenticate() should check the credentials it gets and returns
    #  a user object that matches those credentials."
    # arg request is an interface specification, not used in this implementation

    def authenticate(
        self, request: HttpRequest | None, username=None, password=None, **kwargs
    ):
        """when username is provided, assume that token is newly obtained and valid"""
        account: SocialAccount | None = kwargs.get("social_account", None)
        if not account or not account.user:
            return None
        return account.user if self.user_can_authenticate(account.user) else None

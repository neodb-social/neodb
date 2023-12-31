from django.contrib.auth.backends import ModelBackend, UserModel

from .api import verify_account


class OAuth2Backend(ModelBackend):
    """Used to glue OAuth2 and Django User model"""

    # "authenticate() should check the credentials it gets and returns
    #  a user object that matches those credentials."
    # arg request is an interface specification, not used in this implementation

    def authenticate(self, request, username=None, password=None, **kwargs):
        """when username is provided, assume that token is newly obtained and valid"""
        token = kwargs.get("token", None)
        site = kwargs.get("site", None)
        if token is None or site is None:
            return
        mastodon_id = None
        if username is None:
            code, user_data = verify_account(site, token)
            if code == 200 and user_data:
                mastodon_id = user_data["id"]
        if not mastodon_id:
            return None
        try:
            user = UserModel._default_manager.get(
                mastodon_id__iexact=mastodon_id, mastodon_site__iexact=site
            )
            return user if self.user_can_authenticate(user) else None
        except UserModel.DoesNotExist:
            return None

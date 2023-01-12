from django.contrib.auth.backends import ModelBackend, UserModel
from .api import verify_account


class OAuth2Backend(ModelBackend):
    """Used to glue OAuth2 and Django User model"""

    # "authenticate() should check the credentials it gets and returns
    #  a user object that matches those credentials."
    # arg request is an interface specification, not used in this implementation

    def authenticate(self, request, token=None, username=None, site=None, **kwargs):
        """when username is provided, assume that token is newly obtained and valid"""
        if token is None or site is None:
            return

        if username is None:
            code, user_data = verify_account(site, token)
            if code == 200:
                userid = user_data["id"]
            else:
                # aquiring user data fail means token is invalid thus auth fail
                return None

        # when username is provided, assume that token is newly obtained and valid
        try:
            user = UserModel._default_manager.get(
                mastodon_id=userid, mastodon_site=site
            )
        except UserModel.DoesNotExist:
            return None
        else:
            if self.user_can_authenticate(user):
                return user
            return None

from catalog.common import jsondata

from .common import SocialAccount


class Bluesky:
    pass


class BlueskyAccount(SocialAccount):
    username = jsondata.CharField(json_field_name="access_data", default="")
    app_password = jsondata.EncryptedTextField(
        json_field_name="access_data", default=""
    )
    pass

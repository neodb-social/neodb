from os import environ

environ["NEODB_SECRET_KEY"] = "test"
environ["NEODB_SITE_NAME"] = "test"
environ["NEODB_SITE_DOMAIN"] = "example.org"
environ["SPOTIFY_API_KEY"] = "test"
environ["STEAM_API_KEY"] = ""
environ["INDEX_ALIASES"] = "catalog=test-catalog,journal=test-journal"
environ["NEODB_PREFERRED_LANGUAGES"] = "en"

from boofilsic.settings import *

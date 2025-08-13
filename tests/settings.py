from os import environ

environ["NEODB_SECRET_KEY"] = "test"
environ["NEODB_SITE_NAME"] = "test"
environ["NEODB_SITE_DOMAIN"] = "example.org"
environ["STEAM_API_KEY"] = ""
environ["INDEX_ALIASES"] = "catalog=test-catalog,journal=test-journal"

from boofilsic.settings import *

from os import environ
from urllib.parse import urlsplit, urlunsplit

environ["NEODB_SECRET_KEY"] = "test"
environ["NEODB_SITE_NAME"] = "test"
environ["NEODB_SITE_DOMAIN"] = "example.org"
environ["SPOTIFY_API_KEY"] = "test"
environ["STEAM_API_KEY"] = ""
environ["NEODB_PREFERRED_LANGUAGES"] = "en"

# When running under pytest-xdist, give each worker its own Typesense
# collections and Redis database so parallel workers don't clobber each
# other's shared state. (Postgres test databases are already isolated
# per worker by pytest-django.)
_xdist_worker = environ.get("PYTEST_XDIST_WORKER")
if _xdist_worker:
    _suffix = f"-{_xdist_worker}"
    _worker_num = int("".join(c for c in _xdist_worker if c.isdigit()) or "0")
    _redis_url = urlsplit(environ.get("NEODB_REDIS_URL", "redis://127.0.0.1:6379/0"))
    environ["NEODB_REDIS_URL"] = urlunsplit(
        _redis_url._replace(path=f"/{_worker_num % 16}")
    )
else:
    _suffix = ""

environ["INDEX_ALIASES"] = (
    f"catalog=test-catalog{_suffix},journal=test-journal{_suffix}"
)

from boofilsic.settings import *  # noqa: E402

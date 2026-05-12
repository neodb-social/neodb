"""Cron job configuration loaded by ``rq.cron.CronScheduler``.

Loaded either via ``rq cron boofilsic.cron_config`` (standalone) or via
``neodb-manage cron --start`` (Django already set up). Importing this module
triggers registration of every ``BaseJob`` subclass with ``rq.cron``.
"""

import os


def _ensure_django():
    import django
    from django.apps import apps

    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "boofilsic.settings")
        django.setup()


_ensure_django()

# Importing the app job modules registers their BaseJob subclasses via the
# @JobManager.register decorator. Each app's AppConfig.ready() does the same,
# but apps.ready may have already finished by the time this module loads, so
# we import explicitly to be safe.
from catalog import jobs as _catalog_jobs  # noqa: F401, E402
from common.models import JobManager, SiteConfig  # noqa: E402
from mastodon import jobs as _mastodon_jobs  # noqa: F401, E402
from takahe import jobs as _takahe_jobs  # noqa: F401, E402
from users import jobs as _users_jobs  # noqa: F401, E402

SiteConfig.ensure_loaded()
JobManager.register_with_rq_cron()

from datetime import timedelta

from loguru import logger

from common.models import BaseJob, JobManager
from users.registration_captcha import is_enabled, refresh_pools


@JobManager.register
class RegistrationCaptchaPool(BaseJob):
    """Rebuild the registration captcha's candidate item lists daily.

    The interval stays a flat day even when the captcha is off, rather than
    returning timedelta(0): BaseJob.schedule() reads a zero interval as "never
    schedule", so a conditional interval would leave the job unscheduled and
    enabling the captcha later would not bring it back. The run itself is what
    no-ops. Operators can still turn it off by name via disable_cron_jobs.
    """

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(days=1)

    def run(self) -> None:
        if not is_enabled():
            logger.debug("Registration captcha pool skipped (captcha disabled).")
            return
        sizes = refresh_pools()
        logger.info(f"Registration captcha pools rebuilt: {sizes}")

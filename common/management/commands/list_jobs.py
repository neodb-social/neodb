import pprint

from django.core.management.base import BaseCommand
from redis import Redis
from rq import Queue
from rq.job import Job


class Command(BaseCommand):
    help = "Show jobs in queue"

    def add_arguments(self, parser):
        parser.add_argument("queue", type=str, help="Queue")

    def handle(self, *args, **options):
        redis = Redis()
        queue = Queue(str(options["queue"]), connection=redis)
        for registry in [
            queue.started_job_registry,
            queue.deferred_job_registry,
            queue.finished_job_registry,
            queue.failed_job_registry,
            queue.scheduled_job_registry,
        ]:
            self.stdout.write(self.style.SUCCESS(f"Registry {registry}"))
            for job_id in registry.get_job_ids():
                try:
                    job = Job.fetch(job_id, connection=redis)
                    pprint.pp(job)
                except Exception as e:
                    print(f"Error fetching {job_id}")

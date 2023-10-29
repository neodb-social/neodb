import pprint

import django_rq
from django.conf import settings
from django.core.management.base import BaseCommand
from redis import Redis
from rq import Queue
from rq.job import Job


class Command(BaseCommand):
    help = "Show jobs in queue"

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="append")
        parser.add_argument("--list", action="store_true")

    def handle(self, *args, **options):
        if options["delete"]:
            for job_id in options["delete"]:
                job = Job.fetch(job_id, connection=django_rq.get_connection("fetch"))
                job.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted {job}"))
        if options["list"]:
            queues = settings.RQ_QUEUES.keys()
            for q in queues:
                queue = django_rq.get_queue(q)
                for registry in [
                    queue.scheduled_job_registry,
                    queue.started_job_registry,
                    queue.deferred_job_registry,
                    queue.finished_job_registry,
                    queue.failed_job_registry,
                    queue.canceled_job_registry,
                ]:
                    for job_id in registry.get_job_ids():
                        try:
                            job = Job.fetch(
                                job_id, connection=django_rq.get_connection(q)
                            )
                            self.stdout.write(
                                self.style.SUCCESS(f"{registry.key} {repr(job)}")
                            )
                        except Exception as e:
                            print(f"Error fetching {registry.key} {job_id}")

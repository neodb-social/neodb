import pprint

import django_rq
from django.conf import settings
from django.core.management.base import BaseCommand
from redis import Redis
from rq import Queue
from rq.job import Job


class Command(BaseCommand):
    help = "Manage jobs in RQ"

    def add_arguments(self, parser):
        parser.add_argument("--retry", action="append")
        parser.add_argument("--delete", action="append")
        parser.add_argument("--list", action="store_true")
        parser.add_argument("--prune", action="store_true")

    def handle(self, *args, **options):
        if options["retry"]:
            queue = Queue(connection=django_rq.get_connection("fetch"))
            registry = queue.failed_job_registry
            for job_id in options["retry"]:
                # registry.requeue(job_id)
                job = Job.fetch(job_id, connection=django_rq.get_connection("fetch"))
                job.requeue()
                self.stdout.write(self.style.SUCCESS(f"Retrying {job_id}"))
        if options["delete"]:
            for job_id in options["delete"]:
                job = Job.fetch(job_id, connection=django_rq.get_connection("fetch"))
                job.delete()
                self.stdout.write(self.style.SUCCESS(f"Deleted {job}"))
        if options["list"] or options["prune"]:
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
                    if options["prune"]:
                        registry.cleanup()
                    for job_id in registry.get_job_ids():
                        try:
                            job = Job.fetch(
                                job_id, connection=django_rq.get_connection(q)
                            )
                            if (
                                options["prune"]
                                and q != "cron"
                                and job.get_status()
                                in [
                                    "finished",
                                    "failed",
                                    "canceled",
                                ]
                            ):
                                job.delete()
                            if options["list"]:
                                self.stdout.write(
                                    registry.key.ljust(20)
                                    + str(job.get_status()).ljust(10)
                                    + job_id.ljust(40)
                                    + (
                                        job.enqueued_at.strftime("%Y-%m-%d %H:%M:%S")
                                        if job.enqueued_at
                                        else ""
                                    )
                                )
                        except Exception as e:
                            self.stdout.write(
                                registry.key.ljust(20)
                                + "error".ljust(10)
                                + job_id.ljust(40)
                                + str(e)
                            )

from django.core.management.base import BaseCommand
import pprint
from redis import Redis
from rq.job import Job
from rq import Queue


class Command(BaseCommand):
    help = "Delete a job"

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=str, help="Job ID")

    def handle(self, *args, **options):
        redis = Redis()
        job_id = str(options["job_id"])
        job = Job.fetch(job_id, connection=redis)
        job.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {job}"))

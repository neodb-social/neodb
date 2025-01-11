from django.core.management.base import BaseCommand
from tqdm import tqdm

from users.models import Task


class Command(BaseCommand):
    help = "Manage tasks"

    def add_arguments(self, parser):
        parser.add_argument("--id", action="append")
        parser.add_argument("--user", action="append")
        parser.add_argument("--type", action="append")
        parser.add_argument("--pending", action="store_true")
        parser.add_argument("--failed", action="store_true")
        parser.add_argument("--complete", action="store_true")
        parser.add_argument("--list", action="store_true")
        parser.add_argument("--prune", action="store_true")
        parser.add_argument("--rerun", action="store_true")
        parser.add_argument("--requeue", action="store_true")
        # parser.add_argument("--set-fail", action="store_true")
        parser.add_argument("--delete", action="store_true")

    def handle(self, *args, **options):
        tasks = Task.objects.all().order_by("id")
        states = []
        if options["pending"]:
            states += [Task.States.pending]
        if options["failed"]:
            states += [Task.States.failed]
        if options["complete"]:
            states += [Task.States.complete]
        if states:
            tasks = tasks.filter(state__in=states)
        if options["user"]:
            tasks = tasks.filter(user__username__in=options["user"])
        if options["id"]:
            tasks = tasks.filter(id__in=options["id"])
        if options["type"]:
            tasks = tasks.filter(type__in=options["type"])
        if options["list"]:
            for task in tasks.order_by("-created_time"):
                self.stdout.write(
                    str(task.pk).ljust(10)
                    + str(task.type).ljust(30)
                    + str(Task.States(task.state).label).ljust(10)
                    + task.created_time.strftime("%Y-%m-%d %H:%M  ")
                    + task.edited_time.strftime("%Y-%m-%d %H:%M  ")
                    + str(task.user)
                )
        if options["rerun"]:
            for task in tqdm(tasks):
                task.state = Task.States.pending
                task.save(update_fields=["state"])
                Task._run(task.pk)
        if options["requeue"]:
            for task in tqdm(tasks):
                task.state = Task.States.pending
                task.save(update_fields=["state"])
                task.enqueue()
        if options["delete"]:
            for task in tqdm(tasks):
                task.delete()

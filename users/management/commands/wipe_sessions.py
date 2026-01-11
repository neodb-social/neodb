from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError

from takahe.models import Token
from users.models import User


class Command(BaseCommand):
    help = "Wipe session data for a specific user, or all anonymous sessions if username is '-'"

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            help="Username to wipe sessions for, or '-' to wipe all anonymous sessions",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting",
        )

    def handle(self, *args, **options):
        username = options["username"]
        dry_run = options["dry_run"]

        if username == "-":
            self.wipe_anonymous_sessions(dry_run)
        else:
            self.wipe_user_sessions(username, dry_run)

    def wipe_anonymous_sessions(self, dry_run: bool):
        """Wipe all sessions that have no associated user."""
        sessions_to_delete = []

        for session in Session.objects.iterator():
            data = session.get_decoded()
            if "_auth_user_id" not in data:
                sessions_to_delete.append(session.pk)

        count = len(sessions_to_delete)
        if dry_run:
            self.stdout.write(f"Would delete {count} anonymous session(s)")
        else:
            Session.objects.filter(pk__in=sessions_to_delete).delete()
            self.stdout.write(
                self.style.SUCCESS(f"Deleted {count} anonymous session(s)")
            )

    def wipe_user_sessions(self, username: str, dry_run: bool):
        """Wipe all web sessions and API tokens for a specific user."""
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' not found")

        # Wipe web sessions
        user_id = str(user.pk)
        sessions_to_delete = []

        for session in Session.objects.iterator():
            data = session.get_decoded()
            if data.get("_auth_user_id") == user_id:
                sessions_to_delete.append(session.pk)

        session_count = len(sessions_to_delete)

        # Wipe API tokens
        identity = getattr(user, "identity", None)
        if identity:
            tokens = Token.objects.filter(identity_id=identity.pk)
            token_count = tokens.count()
        else:
            tokens = Token.objects.none()
            token_count = 0

        if dry_run:
            self.stdout.write(
                f"Would delete {session_count} web session(s) and "
                f"{token_count} API token(s) for user '{username}'"
            )
        else:
            Session.objects.filter(pk__in=sessions_to_delete).delete()
            tokens.delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted {session_count} web session(s) and "
                    f"{token_count} API token(s) for user '{username}'"
                )
            )

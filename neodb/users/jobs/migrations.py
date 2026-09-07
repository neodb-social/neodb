import logging

from django.db.models import CharField, F, Func

from takahe.models import Application, Authorization, Token
from takahe.utils import DEV_CONSOLE_CLIENT_ID, DEV_CONSOLE_SCOPES

logger = logging.getLogger(__name__)


def normalize_token_scopes_20260907() -> int:
    """Bring token scopes in line with what neodb now mints.

    Token.scopes stored as a space separated string become the list takahe's
    OAuth flow stores, so scope checks and `" ".join(scopes)` in the token
    endpoint behave the same for every token. The Dev Console application and
    its live tokens are upgraded to read write push, so existing test tokens
    can use webhooks without being regenerated. Returns rows changed."""
    rows = (
        Token.objects.annotate(
            scopes_type=Func(
                F("scopes"), function="jsonb_typeof", output_field=CharField()
            )
        )
        .filter(scopes_type="string")
        .values_list("pk", "scopes")
    )
    changed = 0
    for pk, scopes in rows.iterator(chunk_size=1000):
        Token.objects.filter(pk=pk).update(scopes=str(scopes).split())
        changed += 1
    logger.info(f"normalized scopes of {changed} tokens")

    scopes = list(DEV_CONSOLE_SCOPES)
    apps = Application.objects.filter(client_id=DEV_CONSOLE_CLIENT_ID)
    apps.exclude(scopes=" ".join(scopes)).update(scopes=" ".join(scopes))
    for model in (Token, Authorization):
        n = (
            model.objects.filter(application__in=apps)
            .exclude(scopes=scopes)
            .update(scopes=scopes)
        )
        logger.info(f"upgraded {n} dev console {model.__name__.lower()}s")
        changed += n
    return changed

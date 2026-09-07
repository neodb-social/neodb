import logging

from django.db.models import CharField, F, Func

from takahe.models import Token

logger = logging.getLogger(__name__)


def normalize_token_scopes_20260907() -> int:
    """Rewrite Token.scopes stored as a space separated string into the list
    takahe's OAuth flow stores, so scope checks and `" ".join(scopes)` in the
    token endpoint behave the same for every token. Returns rows changed."""
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
    return changed

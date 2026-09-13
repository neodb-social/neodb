from hatchway import ApiError, QueryOrBody, api_view

from api import schemas
from api.decorators import scope_required
from api.models import Application


@api_view.post
def add_app(
    request,
    client_name: QueryOrBody[str],
    redirect_uris: QueryOrBody[str | list[str]],
    scopes: QueryOrBody[None | str] = None,
    website: QueryOrBody[None | str] = None,
) -> schemas.Application:
    if isinstance(redirect_uris, list):
        uris = [uri.strip() for uri in redirect_uris if uri.strip()]
    else:
        # Only a newline separates URIs here, as the Mastodon API says. A comma
        # is legal inside a URI, so the string keeps it and the legacy comma
        # rule in parse_redirect_uris applies to older rows only.
        uris = [uri.strip() for uri in redirect_uris.splitlines() if uri.strip()]
    # Store newline separated for the same reason: a comma-joined registration
    # cannot be split back apart reliably.
    registration = "\n".join(uris)
    if not Application.parse_redirect_uris(registration):
        raise ApiError(422, "Validation failed: redirect_uris is required")
    application = Application.create(
        client_name=client_name,
        website=website,
        redirect_uris=registration,
        scopes=scopes,
    )
    return schemas.Application.from_application(application)


@scope_required("read")
@api_view.get
def verify_credentials(
    request,
) -> schemas.Application:
    return schemas.Application.from_application_no_keys(request.token.application)

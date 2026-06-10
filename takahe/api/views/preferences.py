from django.http import HttpRequest

from api import schemas
from api.decorators import scope_required
from hatchway import api_view


@scope_required("read:accounts")
@api_view.get(by_alias=True)
def preferences(request: HttpRequest) -> schemas.Preferences:
    return schemas.Preferences.from_identity(request.identity)

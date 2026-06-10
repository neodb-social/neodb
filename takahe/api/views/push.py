from django.conf import settings
from django.http import Http404
from api.views import get_object_or_404

from api import schemas
from api.decorators import scope_required
from api.models import PushSubscription
from hatchway import ApiError, QueryOrBody, api_view


@scope_required("push")
@api_view.post(by_alias=True)
def create_subscription(
    request,
    subscription: QueryOrBody[schemas.PushSubscriptionCreation],
    data: QueryOrBody[schemas.PushData],
) -> schemas.PushSubscription:
    # First, check the server is set up to do push notifications
    if not settings.SETUP.VAPID_PRIVATE_KEY:
        raise Http404("Push not available")
    # Then, register this with our token
    request.token.subscribe(
        subscription.endpoint,
        subscription.keys.model_dump(),
        data.alerts.model_dump(by_alias=True),
        data.policy,
    )
    # Then return the subscription
    return schemas.PushSubscription.from_token(request.token)  # type:ignore


@scope_required("push")
@api_view.get
def get_subscription(request) -> schemas.PushSubscription:
    # First, check the server is set up to do push notifications
    if not settings.SETUP.VAPID_PRIVATE_KEY:
        raise Http404("Push not available")
    # Get the subscription if it exists
    subscription = schemas.PushSubscription.from_token(request.token)
    if not subscription:
        raise ApiError(404, "Not Found")
    return subscription


@scope_required("push")
@api_view.put
def update_subscription(
    request, data: QueryOrBody[schemas.PushData]
) -> schemas.PushSubscription:
    # First, check the server is set up to do push notifications
    if not settings.SETUP.VAPID_PRIVATE_KEY:
        raise Http404("Push not available")
    # Get the subscription if it exists and update it
    sub = get_object_or_404(PushSubscription, token=request.token)
    sub.update(data.alerts.model_dump(by_alias=True), data.policy)
    # Then return the subscription
    return schemas.PushSubscription.from_token(request.token)  # type:ignore


@scope_required("push")
@api_view.delete
def delete_subscription(request) -> dict:
    # Unset the subscription
    PushSubscription.objects.filter(token=request.token).delete()
    return {}

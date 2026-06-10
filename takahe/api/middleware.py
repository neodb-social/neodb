from django.conf import settings
from django.http import HttpResponse

from api.models import Token
from hatchway.http import ApiResponse


class ApiTokenMiddleware:
    """
    Adds request.user and request.identity if an API token appears.
    Also nukes request.session so it can't be used accidentally.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token_value = None
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token_value = auth_header[7:]
        if token_value is None and settings.DEBUG:
            token_value = request.GET.get("token")
        request.token = None
        request.identity = None
        if token_value and token_value != "__app__":
            try:
                token = Token.objects.get(token=token_value, revoked=None)
            except Token.DoesNotExist:
                return HttpResponse("Invalid Bearer token", status=400)
            request.user = token.user
            request.identity = token.identity
            request.token = token
            request.session = None
        response = self.get_response(request)
        if settings.DEBUG and isinstance(response, ApiResponse):
            response.json_dumps_params.update({"indent": 2, "sort_keys": True})
        return response

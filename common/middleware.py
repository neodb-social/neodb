from django.utils.deprecation import MiddlewareMixin


class IdentityMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.identity = None
        if hasattr(request, "user") and request.user.is_authenticated:
            from users.models import APIdentity

            try:
                request.identity = APIdentity.objects.get(user=request.user)
            except APIdentity.DoesNotExist:
                pass

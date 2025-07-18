"""
ASGI

for MCP only
"""

import os

import django
from asgiref.sync import sync_to_async
from django.conf import settings
from django_mcp import mount_mcp_server
from loguru import logger
from starlette.middleware.cors import CORSMiddleware

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "boofilsic.settings")

django.setup()


class BearerTokenAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            if await self._authenticate_token(scope, token):
                logger.debug("ASGI auth: Bearer token authenticated successfully")
                return await self.app(scope, receive, send)
            else:
                logger.debug("ASGI auth: Bearer token authentication failed")
        else:
            logger.debug("ASGI auth: Bearer token not found")
        resource_metadata_url = (
            f"{settings.SITE_INFO['site_url']}/.well-known/oauth-protected-resource"
        )
        www_authenticate_header = f"Bearer resource_metadata={resource_metadata_url}"
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"www-authenticate", www_authenticate_header.encode("utf-8")],
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"detail": "Authentication failed"}',
            }
        )

    async def preflight(self, send):
        # Return a permissible CORS response directly for OPTIONS requests
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/plain"],
                    [b"access-control-allow-origin", b"*"],
                    [b"access-control-allow-methods", b"*"],
                    [b"access-control-allow-headers", b"*"],
                    [b"access-control-max-age", b"86400"],
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    @sync_to_async
    def get_token(self, token):
        from takahe.utils import Takahe

        return Takahe.get_token(token)

    async def _authenticate_token(self, scope, token):
        from users.models.apidentity import APIdentity, User

        if not token:
            logger.debug("ASGI auth: no access token provided")
            return False

        tk = await self.get_token(token)
        if not tk:
            logger.debug("ASGI auth: access token not found")
            return False

        if tk.revoked:
            logger.debug("ASGI auth: access token revoked")
            return False

        request_scope = "write"
        if request_scope not in tk.scopes:
            logger.debug("ASGI auth: scope not allowed")
            return False

        identity = await APIdentity.objects.filter(pk=tk.identity_id).afirst()
        if not identity:
            logger.debug("ASGI auth: identity not found")
            return False

        if identity.deleted:
            logger.debug("ASGI auth: identity deleted")
            return False

        user = await User.objects.filter(pk=identity.user_id).afirst()
        if not user:
            logger.debug("ASGI auth: user not found")
            return False

        # Store user info in scope for downstream use
        scope["user"] = user
        scope["identity_id"] = tk.identity_id
        scope["application_id"] = tk.application_id

        return True


# django_http_app = get_asgi_application()
async def django_http_app(scope, receive, send):
    return


app = mount_mcp_server(django_http_app=django_http_app, mcp_base_path="/mcp")
app = BearerTokenAuthMiddleware(app)
app = CORSMiddleware(app, allow_origins=["*"], allow_headers=["*"], allow_methods=["*"])

from django import template
from django.conf import settings
from django.utils.html import format_html

register = template.Library()


class OAuthTokenNode(template.Node):
    def render(self, context):
        request = context.get("request")
        oauth_token = request.user.mastodon_token
        return format_html(oauth_token)


@register.tag
def oauth_token(parser, token):
    return OAuthTokenNode()

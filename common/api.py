from ninja import NinjaAPI
from django.conf import settings

api = NinjaAPI(
    title=settings.SITE_INFO["site_name"],
    version="1.0.0",
    description=f"{settings.SITE_INFO['site_name']} API <hr/><a href='{settings.APP_WEBSITE}'>Learn more</a>",
)

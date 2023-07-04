from django.contrib import admin
from .models import *
from .api import create_app
from django.utils.translation import gettext_lazy as _
from requests.exceptions import Timeout
from django.core.exceptions import ObjectDoesNotExist


# Register your models here.
@admin.register(MastodonApplication)
class MastodonApplicationModelAdmin(admin.ModelAdmin):
    def add_view(self, request, form_url="", extra_context=None):
        """
        Dirty code here, use POST['domain_name'] to pass error message to user.
        """
        if request.method == "POST":
            if not request.POST.get("client_id") and not request.POST.get(
                "client_secret"
            ):
                # make the post data mutable
                request.POST = request.POST.copy()
                # (is_proxy xor proxy_to) or (proxy_to!=null and is_proxy=false)
                if (
                    (
                        bool(request.POST.get("is_proxy"))
                        or bool(request.POST.get("proxy_to"))
                    )
                    and not (
                        bool(request.POST.get("is_proxy"))
                        and bool(request.POST.get("proxy_to"))
                    )
                    or (
                        not bool(request.POST.get("is_proxy"))
                        and bool(request.POST.get("proxy_to"))
                    )
                ):
                    request.POST["domain_name"] = _("请同时填写is_proxy和proxy_to。")
                else:
                    if request.POST.get("is_proxy"):
                        try:
                            origin = MastodonApplication.objects.get(
                                domain_name=request.POST["proxy_to"]
                            )
                            # set proxy credentials to those of its original site
                            request.POST["app_id"] = origin.app_id
                            request.POST["client_id"] = origin.client_id
                            request.POST["client_secret"] = origin.client_secret
                            request.POST["vapid_key"] = origin.vapid_key
                        except ObjectDoesNotExist:
                            request.POST["domain_name"] = _("proxy_to所指域名不存在，请先添加原站点。")
                    else:
                        # create mastodon app
                        try:
                            response = create_app(request.POST.get("domain_name"))
                        except (Timeout, ConnectionError):
                            request.POST["domain_name"] = _("联邦宇宙请求超时。")
                        except Exception as e:
                            request.POST["domain_name"] = str(e)
                        else:
                            # fill the form with returned data
                            data = response.json()
                            if response.status_code != 200:
                                request.POST["domain_name"] = str(data)
                            else:
                                request.POST["app_id"] = data["id"]
                                request.POST["client_id"] = data["client_id"]
                                request.POST["client_secret"] = data["client_secret"]
                                request.POST["vapid_key"] = data["vapid_key"]

        return super().add_view(request, form_url=form_url, extra_context=extra_context)


admin.site.register(CrossSiteUserInfo)

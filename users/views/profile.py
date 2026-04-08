from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from common.models import SiteConfig
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models.task import Task
from users.models.webauthn import WebAuthnCredential


class ProfileForm(forms.ModelForm):
    class Meta:
        model = TakaheIdentity
        fields = [
            "name",
            "summary",
            "manually_approves_followers",
            "discoverable",
            "icon",
        ]

    def clean_summary(self):
        return Takahe.txt2html(self.cleaned_data["summary"])


@login_required
def account_info(request):
    profile_form = ProfileForm(
        instance=request.user.identity.takahe_identity,
        initial={
            "summary": Takahe.html2txt(request.user.identity.summary),
        },
    )
    has_pending_tasks = Task.pending_tasks(request.user).exists()
    identity = request.user.identity
    return render(
        request,
        "users/account.html",
        {
            "allow_any_site": len(SiteConfig.system.mastodon_login_whitelist) == 0,
            "enable_email": settings.ENABLE_LOGIN_EMAIL,
            "enable_threads": SiteConfig.system.enable_login_threads,
            "enable_bluesky": SiteConfig.system.enable_login_bluesky,
            "profile_form": profile_form,
            "has_pending_tasks": has_pending_tasks,
            "tokens": Takahe.get_tokens_for_identity(identity.pk),
            "counts": Takahe.get_follow_block_mute_counts(identity.pk),
            "passkeys": WebAuthnCredential.objects.filter(user=request.user),
        },
    )


@login_required
def account_profile(request):
    if request.method == "POST":
        form = ProfileForm(
            request.POST, request.FILES, instance=request.user.identity.takahe_identity
        )
        if form.is_valid():
            i = form.save()
            Takahe.update_state(i, "edited")
            u = request.user
            if u.mastodon and not u.preference.mastodon_skip_userinfo:
                u.preference.mastodon_skip_userinfo = True
                u.preference.save(update_fields=["mastodon_skip_userinfo"])
    return HttpResponseRedirect(reverse("users:info"))


@require_http_methods(["POST"])
@login_required
def account_relations(request, typ: str):
    match typ:
        case "follow":
            ids = request.user.identity.following_identities.all()
        case "follower":
            ids = request.user.identity.follower_identities.all()
        case "follow_request":
            ids = request.user.identity.requested_follower_identities.all()
        case "mute":
            ids = request.user.identity.muting_identities.all()
        case "block":
            ids = request.user.identity.blocking_identities.all()
        case _:
            ids = []
    return render(request, "users/relationship_list.html", {"id": typ, "list": ids})

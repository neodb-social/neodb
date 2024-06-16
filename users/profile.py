from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe


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
    return render(
        request,
        "users/account.html",
        {
            "allow_any_site": settings.MASTODON_ALLOW_ANY_SITE,
            "profile_form": profile_form,
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
            if u.mastodon_acct and not u.preference.mastodon_skip_userinfo:
                u.preference.mastodon_skip_userinfo = True
                u.preference.save(update_fields=["mastodon_skip_userinfo"])
    return HttpResponseRedirect(reverse("users:info"))

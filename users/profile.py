from datetime import timedelta
from typing import Any, Dict
from urllib.parse import quote

import django_rq
from django import forms
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import BadRequest, ObjectDoesNotExist
from django.core.mail import send_mail
from django.core.signing import TimestampSigner
from django.core.validators import EmailValidator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger

from common.config import *
from common.utils import AuthedHttpRequest
from journal.exporters.doufen import export_marks_task
from journal.importers.douban import DoubanImporter
from journal.importers.goodreads import GoodreadsImporter
from journal.importers.opml import OPMLImporter
from journal.models import remove_data_by_user, reset_journal_visibility_for_user
from mastodon import mastodon_request_included
from mastodon.api import *
from mastodon.api import verify_account
from social.models import reset_social_visibility_for_user
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe

from .models import Preference, User
from .tasks import *


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
    return HttpResponseRedirect(reverse("users:info"))

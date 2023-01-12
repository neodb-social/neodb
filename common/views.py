from django.shortcuts import redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required


@login_required
def home(request):
    if request.user.get_preference().classic_homepage:
        return redirect(
            reverse("journal:user_profile", args=[request.user.mastodon_username])
        )
    else:
        return redirect(reverse("social:feed"))

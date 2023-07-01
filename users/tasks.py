from django.conf import settings


def refresh_mastodon_data_task(user, token=None):
    if token:
        user.mastodon_token = token
    if user.refresh_mastodon_data():
        user.save()
        print(f"{user} mastodon data refreshed")
    else:
        print(f"{user} mastodon data refresh failed")

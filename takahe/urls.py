from django.urls import path

from .views import *

app_name = "takahe"
urlpatterns = [
    path("auth/login/", auth_login, name="auth_login"),
    path("auth/logout/", auth_logout, name="auth_logout"),
]

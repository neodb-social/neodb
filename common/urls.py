from django.urls import path
from .views import *

app_name = "common"
urlpatterns = [
    path("", home),
    path("developer/", developer, name="developer"),
    path("home/", home, name="home"),
    path("me/", me, name="me"),
]

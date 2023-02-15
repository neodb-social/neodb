from django.urls import path
from .views import *

app_name = "common"
urlpatterns = [
    path("", home),
    path("api_doc", api_doc),
    path("home/", home, name="home"),
]

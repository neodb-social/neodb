from django.urls import path

from .views import *

app_name = "management"
urlpatterns = [
    path("", AnnouncementListView.as_view(), name="list"),
    path("<int:pk>/", AnnouncementDetailView.as_view(), name="retrieve"),
    path("create/", AnnouncementCreateView.as_view(), name="create"),
    path("<str:slug>/", AnnouncementDetailView.as_view(), name="retrieve_slug"),
    path("<int:pk>/update/", AnnouncementUpdateView.as_view(), name="update"),
    path("<int:pk>/delete/", AnnouncementDeleteView.as_view(), name="delete"),
]

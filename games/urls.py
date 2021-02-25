from django.urls import path
from .views import *


app_name = 'games'
urlpatterns = [
    path('create/', create, name='create'),
    path('<int:id>/', retrieve, name='retrieve'),
    path('update/<int:id>/', update, name='update'),
    path('delete/<int:id>/', delete, name='delete'),
    path('mark/', create_update_mark, name='create_update_mark'),
    path('<int:game_id>/mark/list/',
         retrieve_mark_list, name='retrieve_mark_list'),
    path('mark/delete/<int:id>/', delete_mark, name='delete_mark'),
    path('<int:game_id>/review/create/', create_review, name='create_review'),
    path('review/update/<int:id>/', update_review, name='update_review'),
    path('review/delete/<int:id>/', delete_review, name='delete_review'),
    path('review/<int:id>/', retrieve_review, name='retrieve_review'),
    path('<int:game_id>/review/list/',
         retrieve_review_list, name='retrieve_review_list'),
    path('scrape/', scrape, name='scrape'),
    path('click_to_scrape/', click_to_scrape, name='click_to_scrape'),
]

from django.urls import path

from .views import *

app_name = "legacy"
urlpatterns = [
    path("books/<int:id>/", book, name="book"),
    path("movies/<int:id>/", movie, name="movie"),
    path("music/album/<int:id>/", album, name="album"),
    path("music/song/<int:id>/", song, name="song"),
    path("games/<int:id>/", game, name="game"),
    path("collections/<int:id>/", collection, name="collection"),
    path("books/review/<int:id>/", book_review, name="book_review"),
    path("movies/review/<int:id>/", movie_review, name="movie_review"),
    path("music/album/review/<int:id>/", album_review, name="album_review"),
    path("music/song/review/<int:id>/", song_review, name="song_review"),
    path("games/review/<int:id>/", game_review, name="game_review"),
]

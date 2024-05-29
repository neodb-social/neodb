from django.core.signing import b62_decode, b62_encode
from django.shortcuts import get_object_or_404, redirect, render

from catalog.collection.models import Collection
from catalog.models import Item

from .models import *


def book(request, id):
    link = get_object_or_404(BookLink, old_id=id)
    return redirect(f"/book/{b62_encode(link.new_uid.int)}", permanent=True)


def movie(request, id):
    link = get_object_or_404(MovieLink, old_id=id)
    return redirect(f"/movie/{b62_encode(link.new_uid.int)}", permanent=True)


def album(request, id):
    link = get_object_or_404(AlbumLink, old_id=id)
    return redirect(f"/album/{b62_encode(link.new_uid.int)}", permanent=True)


def song(request, id):
    link = get_object_or_404(SongLink, old_id=id)
    return redirect(f"/album/{b62_encode(link.new_uid.int)}", permanent=True)


def game(request, id):
    link = get_object_or_404(GameLink, old_id=id)
    return redirect(f"/game/{b62_encode(link.new_uid.int)}", permanent=True)


def collection(request, id):
    link = get_object_or_404(CollectionLink, old_id=id)
    return redirect(f"/collection/{b62_encode(link.new_uid.int)}", permanent=True)


def book_review(request, id):
    link = get_object_or_404(ReviewLink, module="book", old_id=id)
    return redirect(f"/review/{b62_encode(link.new_uid.int)}", permanent=True)


def movie_review(request, id):
    link = get_object_or_404(ReviewLink, module="movie", old_id=id)
    return redirect(f"/review/{b62_encode(link.new_uid.int)}", permanent=True)


def album_review(request, id):
    link = get_object_or_404(ReviewLink, module="album", old_id=id)
    return redirect(f"/review/{b62_encode(link.new_uid.int)}", permanent=True)


def song_review(request, id):
    link = get_object_or_404(ReviewLink, module="song", old_id=id)
    return redirect(f"/review/{b62_encode(link.new_uid.int)}", permanent=True)


def game_review(request, id):
    link = get_object_or_404(ReviewLink, module="game", old_id=id)
    return redirect(f"/review/{b62_encode(link.new_uid.int)}", permanent=True)

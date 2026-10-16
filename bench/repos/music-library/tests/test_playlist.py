import pytest
from music.library import Library
from music.playlist import Playlist


def test_a_new_playlist_starts_empty():
    first = Playlist("Morning")
    first.add("Sunrise")
    second = Playlist("Evening")
    assert second.tracks == []
    assert len(second) == 0


def test_playlists_do_not_share_tracks():
    road = Playlist("Road trip")
    rain = Playlist("Rainy day")
    road.add("Highway")
    rain.add("Drizzle")
    assert road.tracks == ["Highway"]
    assert rain.tracks == ["Drizzle"]


def test_starting_tracks_are_not_changed():
    starting = ["One", "Two"]
    playlist = Playlist("Mix", starting)
    playlist.add("Three")
    assert playlist.tracks == ["One", "Two", "Three"]
    assert starting == ["One", "Two"]


def test_adding_a_track_twice_is_rejected():
    playlist = Playlist("Focus", ["Calm"])
    with pytest.raises(ValueError):
        playlist.add("Calm")


def test_remove_and_move_up():
    playlist = Playlist("Party", ["A", "B", "C"])
    playlist.move_up("C")
    assert playlist.tracks == ["A", "C", "B"]
    playlist.remove("A")
    assert playlist.tracks == ["C", "B"]
    with pytest.raises(ValueError):
        playlist.remove("A")


def test_library_playlists_are_independent():
    library = Library()
    library.create("Chill").add("Waves")
    library.create("Workout").add("Sprint")
    assert library.playlists["Chill"].tracks == ["Waves"]
    assert library.playlists["Workout"].tracks == ["Sprint"]
    assert library.total_tracks() == 2

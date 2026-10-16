"""A library keeps playlists by name."""

from music.playlist import Playlist


class Library:
    def __init__(self) -> None:
        self.playlists: dict[str, Playlist] = {}

    def create(self, name: str) -> Playlist:
        if name in self.playlists:
            raise ValueError(f"there is already a playlist called {name!r}")
        self.playlists[name] = Playlist(name)
        return self.playlists[name]

    def total_tracks(self) -> int:
        return sum(len(playlist) for playlist in self.playlists.values())

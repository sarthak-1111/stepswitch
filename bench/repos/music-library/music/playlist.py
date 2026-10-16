"""Playlists of track titles."""


class Playlist:
    def __init__(self, name: str, tracks: list[str] = []) -> None:
        self.name = name
        self.tracks = tracks

    def add(self, track: str) -> None:
        if track in self.tracks:
            raise ValueError(f"{track!r} is already in {self.name!r}")
        self.tracks.append(track)

    def remove(self, track: str) -> None:
        if track not in self.tracks:
            raise ValueError(f"{track!r} is not in {self.name!r}")
        self.tracks.remove(track)

    def move_up(self, track: str) -> None:
        index = self.tracks.index(track)
        if index > 0:
            self.tracks[index - 1], self.tracks[index] = self.tracks[index], self.tracks[index - 1]

    def __len__(self) -> int:
        return len(self.tracks)

    def __repr__(self) -> str:
        return f"Playlist({self.name!r}, {self.tracks!r})"

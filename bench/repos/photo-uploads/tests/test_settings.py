import pytest
from gallery.settings import load_settings
from gallery.uploads import UploadRejected, check_upload

MB = 1024 * 1024


def test_limits_in_the_settings_file():
    settings = load_settings()
    assert settings.max_size_mb == 25
    assert settings.allowed_extensions == {".jpg", ".jpeg", ".png", ".webp"}


@pytest.mark.parametrize(
    "name", ["beach.webp", "beach.WEBP", "beach.jpg", "beach.jpeg", "beach.png"]
)
def test_photos_up_to_the_limit_are_accepted(name):
    check_upload(name, 25 * MB)


def test_photos_over_the_limit_are_rejected():
    with pytest.raises(UploadRejected):
        check_upload("beach.jpg", 25 * MB + 1)


def test_gifs_stay_blocked():
    with pytest.raises(UploadRejected):
        check_upload("dance.gif", MB)

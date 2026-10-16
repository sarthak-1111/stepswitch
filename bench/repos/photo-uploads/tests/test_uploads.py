import pytest
from gallery.settings import UploadSettings
from gallery.uploads import UploadRejected, check_upload

MB = 1024 * 1024
RULES = UploadSettings(max_size_mb=2, allowed_extensions=frozenset({".png"}))


def test_an_allowed_file_passes():
    check_upload("photo.png", 2 * MB, RULES)


def test_extensions_ignore_case():
    check_upload("PHOTO.PNG", MB, RULES)


def test_a_wrong_extension_is_rejected():
    with pytest.raises(UploadRejected, match="not allowed"):
        check_upload("photo.bmp", MB, RULES)


def test_a_file_without_extension_is_rejected():
    with pytest.raises(UploadRejected):
        check_upload("photo", MB, RULES)


def test_a_large_file_is_rejected():
    with pytest.raises(UploadRejected, match="larger than 2 MB"):
        check_upload("photo.png", 2 * MB + 1, RULES)

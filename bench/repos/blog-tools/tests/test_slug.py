import pytest
from blog.slug import slugify


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Hello World", "hello-world"),
        ("  Hello,   World!  ", "hello-world"),
        ("Crème Brûlée: A How-To", "creme-brulee-a-how-to"),
        ("Python 3.12 is out", "python-3-12-is-out"),
        ("C++ & Rust", "c-rust"),
        ("Smörgåsbord für Anfänger", "smorgasbord-fur-anfanger"),
        ("already-a-slug", "already-a-slug"),
        ("--Leading and trailing--", "leading-and-trailing"),
    ],
)
def test_slugify(title, slug):
    assert slugify(title) == slug


def test_a_title_without_letters_or_digits():
    assert slugify("!!! ???") == ""

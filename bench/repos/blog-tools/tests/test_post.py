from datetime import date

from blog.excerpt import excerpt
from blog.post import Post


def test_word_count_and_reading_time():
    post = Post("Notes", date(2024, 4, 2), "word " * 450)
    assert post.word_count() == 450
    assert post.reading_minutes() == 3
    assert Post("Short", date(2024, 4, 2), "hi").reading_minutes() == 1


def test_short_text_is_kept():
    assert excerpt("A  short   text.") == "A short text."


def test_long_text_is_cut_at_a_word():
    text = "one two three four five six"
    assert excerpt(text, limit=18) == "one two three..."

from tags import clean, tag_key, unique_tags


def test_clean():
    assert clean("  road   trip ") == "road trip"


def test_duplicates_that_differ_in_case_are_dropped():
    assert unique_tags(["Python", "python", " PYTHON "]) == ["Python"]


def test_sharp_s_matches_double_s():
    assert unique_tags(["Straße", "STRASSE", "strasse"]) == ["Straße"]


def test_greek_tags():
    assert unique_tags(["Σίσυφος", "ΣΊΣΥΦΟΣ"]) == ["Σίσυφος"]


def test_different_tags_keep_their_order():
    assert unique_tags(["work", "home", "Work", "garden"]) == ["work", "home", "garden"]


def test_empty_tags_are_dropped():
    assert unique_tags(["", "   ", "x"]) == ["x"]


def test_tag_key_ignores_case():
    assert tag_key("Maße") == tag_key("MASSE")

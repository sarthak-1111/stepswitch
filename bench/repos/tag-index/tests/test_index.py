from index import build_index, notes_tagged

NOTES = {
    "Trip to the coast": ["travel", "Straße"],
    "Walking routes": ["STRASSE", "walking"],
    "Packing list": ["Travel"],
}


def test_build_index():
    assert build_index(NOTES) == {
        "strasse": ["Trip to the coast", "Walking routes"],
        "travel": ["Packing list", "Trip to the coast"],
        "walking": ["Walking routes"],
    }


def test_lookup_ignores_case():
    index = build_index(NOTES)
    assert notes_tagged(index, "TRAVEL") == ["Packing list", "Trip to the coast"]
    assert notes_tagged(index, "straße") == ["Trip to the coast", "Walking routes"]
    assert notes_tagged(index, "cooking") == []

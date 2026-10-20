import inspect

import pytest
from desk import lookup, signup
from desk.members import Register


def test_normalize_email() -> None:
    from desk.emails import normalize_email

    assert normalize_email("  Ann@Example.COM ") == "ann@example.com"
    assert normalize_email("bo@example.com") == "bo@example.com"


def test_sign_up_and_find_with_any_spacing_and_case() -> None:
    register = Register()
    assert signup.sign_up(register, " Ann Lee ", "Ann@Example.com") == "ann@example.com"
    assert lookup.find_member(register, "  ANN@example.com\n") == "Ann Lee"
    assert lookup.is_member(register, "ann@example.com")
    assert lookup.find_member(register, "bo@example.com") is None
    assert register.count() == 1


def test_duplicates_and_bad_addresses_are_refused() -> None:
    register = Register()
    signup.sign_up(register, "Ann", "ann@example.com")
    with pytest.raises(ValueError, match="already"):
        signup.sign_up(register, "Ann again", " ANN@example.com")
    with pytest.raises(ValueError, match="not an e-mail"):
        signup.sign_up(register, "Nobody", "nobody")


def test_one_normalizer_for_both_modules() -> None:
    for module, own in ((signup, "_clean_email"), (lookup, "_canonical")):
        assert not hasattr(module, own), f"{module.__name__} still has {own}"
        assert "normalize_email" in inspect.getsource(module)

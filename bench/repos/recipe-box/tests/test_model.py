import pytest
from recipes.model import Ingredient, Recipe


def test_describe():
    assert Ingredient("flour", 200, "g").describe() == "200 g flour"
    assert Ingredient("milk", 0.25, "l").describe() == "0.25 l milk"


def test_shopping_list():
    recipe = Recipe("Toast", 1, (Ingredient("bread", 2, "slices"), Ingredient("butter", 10, "g")))
    assert recipe.shopping_list() == ["2 slices bread", "10 g butter"]


def test_a_recipe_serves_someone():
    with pytest.raises(ValueError):
        Recipe("Nothing", 0, ())

from recipes.model import Ingredient
from recipes.parse import parse_ingredient, parse_recipe


def test_parse_ingredient():
    assert parse_ingredient("200 g plain flour") == Ingredient("plain flour", 200.0, "g")


def test_parse_recipe():
    recipe = parse_recipe("Pancakes (serves 4)\n200 g flour\n0.5 l milk\n2 pcs egg\n")
    assert recipe.title == "Pancakes"
    assert recipe.servings == 4
    assert recipe.shopping_list() == ["200 g flour", "0.5 l milk", "2 pcs egg"]

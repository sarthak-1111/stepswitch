import pytest
from recipes.model import Ingredient, Recipe
from recipes.parse import parse_recipe

PANCAKES = Recipe(
    "Pancakes",
    4,
    (Ingredient("flour", 200, "g"), Ingredient("milk", 0.5, "l"), Ingredient("egg", 2, "pcs")),
)


def test_scaled_up():
    bigger = PANCAKES.scaled(6)
    assert bigger.title == "Pancakes"
    assert bigger.servings == 6
    assert [i.quantity for i in bigger.ingredients] == pytest.approx([300, 0.75, 3])
    assert [(i.name, i.unit) for i in bigger.ingredients] == [
        ("flour", "g"),
        ("milk", "l"),
        ("egg", "pcs"),
    ]


def test_scaled_down():
    smaller = PANCAKES.scaled(2)
    assert smaller.servings == 2
    assert [i.quantity for i in smaller.ingredients] == pytest.approx([100, 0.25, 1])


def test_the_original_recipe_is_unchanged():
    PANCAKES.scaled(8)
    assert PANCAKES.servings == 4
    assert [i.quantity for i in PANCAKES.ingredients] == [200, 0.5, 2]


def test_shopping_list_of_a_scaled_recipe():
    soup = parse_recipe("Soup (serves 2)\n1 l stock\n300 g carrots\n1 pcs onion")
    assert soup.scaled(3).shopping_list() == ["1.5 l stock", "450 g carrots", "1.5 pcs onion"]


@pytest.mark.parametrize("servings", [0, -2])
def test_servings_must_be_positive(servings):
    with pytest.raises(ValueError):
        PANCAKES.scaled(servings)

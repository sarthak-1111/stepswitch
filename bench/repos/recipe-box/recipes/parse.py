"""Read recipes from plain text."""

from recipes.model import Ingredient, Recipe


def parse_ingredient(line: str) -> Ingredient:
    """Parse a line such as '200 g flour' or '2 pcs egg'."""
    quantity, unit, name = line.split(maxsplit=2)
    return Ingredient(name, float(quantity), unit)


def parse_recipe(text: str) -> Recipe:
    """The first line is 'Title (serves N)'; every other line is an ingredient."""
    header, *lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    title, _, serves = header.rpartition("(serves ")
    ingredients = tuple(parse_ingredient(line) for line in lines)
    return Recipe(title.strip(), int(serves.rstrip(")")), ingredients)

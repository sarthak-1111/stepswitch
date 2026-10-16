"""Recipes made of ingredients."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Ingredient:
    name: str
    quantity: float
    unit: str

    def describe(self) -> str:
        return f"{self.quantity:g} {self.unit} {self.name}"


@dataclass(frozen=True)
class Recipe:
    title: str
    servings: int
    ingredients: tuple[Ingredient, ...]

    def __post_init__(self) -> None:
        if self.servings < 1:
            raise ValueError("a recipe serves at least one person")

    def shopping_list(self) -> list[str]:
        return [ingredient.describe() for ingredient in self.ingredients]

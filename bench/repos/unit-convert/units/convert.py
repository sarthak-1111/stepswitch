"""Conversions between units of the same kind."""

from units.tables import FACTORS


def kind_of(unit: str) -> str:
    """The kind of quantity a unit measures, such as 'length'."""
    for kind, table in FACTORS.items():
        if unit in table:
            return kind
    raise ValueError(f"unknown unit: {unit!r}")


def convert(value: float, source: str, target: str) -> float:
    """`value` in `source` units, expressed in `target` units."""
    kind = kind_of(source)
    if kind_of(target) != kind:
        raise ValueError(f"cannot convert {source} ({kind}) to {target} ({kind_of(target)})")
    table = FACTORS[kind]
    return value * table[source] / table[target]


def describe(value: float, source: str, target: str) -> str:
    """A sentence such as '5 km = 3.10686 mi'."""
    return f"{value:g} {source} = {convert(value, source, target):g} {target}"

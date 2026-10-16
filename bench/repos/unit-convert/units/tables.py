"""The size of each unit in the base unit of its kind: metre, kilogram or litre."""

FACTORS: dict[str, dict[str, float]] = {
    "length": {
        "mm": 0.001,
        "cm": 0.01,
        "m": 1.0,
        "km": 1000.0,
        "in": 0.0254,
        "ft": 0.3048,
        "mi": 1609.344,
    },
    "mass": {"g": 0.001, "kg": 1.0, "oz": 0.028349523125, "lb": 0.45359237},
    "volume": {"ml": 0.001, "l": 1.0, "cup": 0.2365882365, "gal": 3.785411784},
}

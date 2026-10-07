"""Parsing des lignes d'ingrédients et agrégation de la liste de courses.

Une ligne d'ingrédient s'écrit librement, par exemple :
    200 g pâtes
    1,5 kg pommes de terre
    2 c. à soupe huile d'olive
    3 oignons
    sel
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Unité normalisée -> (unité de base, facteur de conversion)
_UNIT_ALIASES: dict[str, tuple[str, float]] = {
    "mg": ("g", 0.001),
    "g": ("g", 1),
    "gr": ("g", 1),
    "kg": ("g", 1000),
    "ml": ("ml", 1),
    "cl": ("ml", 10),
    "dl": ("ml", 100),
    "l": ("ml", 1000),
    "c. à soupe": ("c. à soupe", 1),
    "c.à.s": ("c. à soupe", 1),
    "c.a.s": ("c. à soupe", 1),
    "càs": ("c. à soupe", 1),
    "cas": ("c. à soupe", 1),
    "cs": ("c. à soupe", 1),
    "cuillère à soupe": ("c. à soupe", 1),
    "cuillères à soupe": ("c. à soupe", 1),
    "c. à café": ("c. à café", 1),
    "c.à.c": ("c. à café", 1),
    "c.a.c": ("c. à café", 1),
    "càc": ("c. à café", 1),
    "cac": ("c. à café", 1),
    "cc": ("c. à café", 1),
    "cuillère à café": ("c. à café", 1),
    "cuillères à café": ("c. à café", 1),
}
for _unit in (
    "pincée", "tranche", "gousse", "botte", "boîte", "sachet",
    "paquet", "pot", "verre", "filet", "brin", "feuille", "tasse",
):
    _UNIT_ALIASES[_unit] = (_unit, 1)
    _UNIT_ALIASES[_unit + "s"] = (_unit, 1)

# Les alias les plus longs d'abord pour que "c. à soupe" passe avant "cs".
_UNIT_PATTERN = "|".join(
    re.escape(u) for u in sorted(_UNIT_ALIASES, key=len, reverse=True)
)
_LINE_RE = re.compile(
    rf"^\s*(?P<qty>\d+/\d+|\d+(?:[.,]\d+)?)\s*"
    rf"(?:(?P<unit>{_UNIT_PATTERN})\.?(?=\s|$))?\s*"
    rf"(?:de\s+|d['’]\s*)?(?P<name>.+?)\s*$",
    re.IGNORECASE,
)


@dataclass
class Ingredient:
    name: str
    quantity: float | None = None
    unit: str | None = None


def _parse_qty(raw: str) -> float:
    if "/" in raw:
        num, den = raw.split("/")
        return float(num) / float(den)
    return float(raw.replace(",", "."))


def parse_ingredient(line: str) -> Ingredient | None:
    """Transforme une ligne libre en Ingredient (None si la ligne est vide)."""
    line = line.strip().lstrip("-•*").strip()
    if not line:
        return None
    match = _LINE_RE.match(line)
    if not match:
        return Ingredient(name=line)
    quantity = _parse_qty(match.group("qty"))
    unit = None
    if match.group("unit"):
        unit, factor = _UNIT_ALIASES[match.group("unit").lower()]
        quantity *= factor
    return Ingredient(name=match.group("name"), quantity=quantity, unit=unit)


def parse_ingredients(text: str) -> list[Ingredient]:
    return [i for i in (parse_ingredient(l) for l in text.splitlines()) if i]


@dataclass
class _Line:
    name: str
    unit: str | None
    quantity: float | None = 0.0
    dishes: list[str] = field(default_factory=list)


def _key(name: str) -> str:
    """Clé de regroupement tolérante au pluriel ("oignons" == "oignon")."""
    words = name.lower().split()
    return " ".join(w[:-1] if len(w) > 3 and w[-1] in "sx" else w for w in words)


def _round(quantity: float, unit: str | None) -> float:
    if unit in ("g", "ml"):
        return round(quantity / 10) * 10 if quantity >= 50 else math.ceil(quantity)
    if unit is None:
        # Ingrédients comptés à la pièce : on arrondit au supérieur.
        return math.ceil(quantity - 1e-9)
    return math.ceil(quantity * 2 - 1e-9) / 2


def _format_quantity(quantity: float | None, unit: str | None) -> str:
    if quantity is None:
        return "selon besoin"
    if unit == "g" and quantity >= 1000:
        quantity, unit = quantity / 1000, "kg"
    elif unit == "ml" and quantity >= 1000:
        quantity, unit = quantity / 1000, "l"
    text = f"{quantity:g}".replace(".", ",")
    return f"{text} {unit}" if unit else text


def aggregate(dishes: list[tuple[str, str, float]]) -> list[dict]:
    """Agrège les ingrédients de plusieurs plats.

    `dishes` contient des tuples (nom du plat, texte des ingrédients, facteur
    d'échelle). Retourne une liste triée de lignes de courses.
    """
    lines: dict[tuple[str, str | None], _Line] = {}
    for dish_name, ingredients_text, factor in dishes:
        for ing in parse_ingredients(ingredients_text):
            key = (_key(ing.name), ing.unit)
            line = lines.setdefault(key, _Line(name=ing.name, unit=ing.unit))
            if ing.quantity is None or line.quantity is None:
                line.quantity = None
            else:
                line.quantity += ing.quantity * factor
            if dish_name not in line.dishes:
                line.dishes.append(dish_name)

    result = []
    for line in sorted(lines.values(), key=lambda l: l.name.lower()):
        qty = None if line.quantity is None else _round(line.quantity, line.unit)
        result.append({
            "ingrédient": line.name,
            "quantité": _format_quantity(qty, line.unit),
            "plats": line.dishes,
        })
    return result

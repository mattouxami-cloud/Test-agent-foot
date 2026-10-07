from meal_agent.shopping import aggregate, parse_ingredient


def test_parse_units_and_conversions():
    assert parse_ingredient("1,5 kg pommes de terre").quantity == 1500
    assert parse_ingredient("20 cl crème").unit == "ml"
    ing = parse_ingredient("2 c. à soupe huile d'olive")
    assert (ing.quantity, ing.unit, ing.name) == (2, "c. à soupe", "huile d'olive")
    ing = parse_ingredient("1 boîte de tomates concassées")
    assert (ing.unit, ing.name) == ("boîte", "tomates concassées")
    assert parse_ingredient("1/2 citron").quantity == 0.5


def test_parse_without_quantity():
    ing = parse_ingredient("sel")
    assert ing.quantity is None and ing.name == "sel"
    assert parse_ingredient("   ") is None


def test_aggregate_scales_and_merges_plurals():
    result = aggregate([
        ("Pâtes carbo", "200 g pâtes\n1 oignon\nsel", 2),
        ("Bolo", "500 g Pâtes\n1,5 oignons", 1),
    ])
    by_name = {r["ingrédient"].lower(): r for r in result}
    assert by_name["pâtes"]["quantité"] == "900 g"
    assert by_name["oignon"]["quantité"] == "4"
    assert by_name["oignon"]["plats"] == ["Pâtes carbo", "Bolo"]
    assert by_name["sel"]["quantité"] == "selon besoin"


def test_aggregate_displays_kilos():
    result = aggregate([("Purée", "800 g pommes de terre", 2)])
    assert result[0]["quantité"] == "1,6 kg"

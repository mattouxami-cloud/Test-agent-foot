"""Stockage des plats, préférences et menus dans Notion.

Sous la page Notion parente partagée avec l'intégration, l'agent crée (une
seule fois) :
  - une base de données « Mes plats »
  - une page « Préférences alimentaires » (une puce par préférence)
  - une page « Menus de la semaine » qui contient un sous-page par menu
"""

from __future__ import annotations

from datetime import date

from notion_client import Client
from notion_client.helpers import collect_paginated_api

DISHES_DB_TITLE = "Mes plats"
PREFERENCES_PAGE_TITLE = "Préférences alimentaires"
PLANS_PAGE_TITLE = "Menus de la semaine"

MEAL_TYPES = ["Petit-déjeuner", "Déjeuner", "Dîner", "Snack"]

DISH_PROPERTIES = {
    "Nom": {"title": {}},
    "Repas": {"multi_select": {"options": [{"name": m} for m in MEAL_TYPES]}},
    "Ingrédients": {"rich_text": {}},
    "Portions": {"number": {}},
    "Temps (min)": {"number": {}},
    "Appréciation": {"number": {}},
    "Tags": {"multi_select": {}},
    "Dernière planification": {"date": {}},
    "Notes": {"rich_text": {}},
}

_NOTION_TEXT_LIMIT = 2000
_NOTION_BLOCKS_PER_REQUEST = 100


def _rich_text(text: str) -> list[dict]:
    chunks = [text[i:i + _NOTION_TEXT_LIMIT] for i in range(0, len(text), _NOTION_TEXT_LIMIT)]
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def _plain(rich_text: list[dict]) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def _block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": _rich_text(text), **extra}}


class NotionStore:
    def __init__(self, token: str, parent_page_id: str):
        self.client = Client(auth=token)
        self.parent_page_id = parent_page_id
        self._data_source_id: str | None = None
        self._preferences_page_id: str | None = None
        self._plans_page_id: str | None = None

    # ------------------------------------------------------------------ setup

    def ensure_setup(self) -> None:
        """Retrouve (ou crée) la base des plats et les pages de travail."""
        children = collect_paginated_api(
            self.client.blocks.children.list, block_id=self.parent_page_id
        )
        database_id = None
        for block in children:
            if block["type"] == "child_database" and block["child_database"]["title"] == DISHES_DB_TITLE:
                database_id = block["id"]
            elif block["type"] == "child_page":
                title = block["child_page"]["title"]
                if title == PREFERENCES_PAGE_TITLE:
                    self._preferences_page_id = block["id"]
                elif title == PLANS_PAGE_TITLE:
                    self._plans_page_id = block["id"]

        if database_id:
            database = self.client.databases.retrieve(database_id=database_id)
            self._data_source_id = database["data_sources"][0]["id"]
        else:
            database = self.client.databases.create(
                parent={"type": "page_id", "page_id": self.parent_page_id},
                title=_rich_text(DISHES_DB_TITLE),
                icon={"type": "emoji", "emoji": "🍽️"},
                initial_data_source={"properties": DISH_PROPERTIES},
            )
            self._data_source_id = database["data_sources"][0]["id"]

        if not self._preferences_page_id:
            self._preferences_page_id = self._create_child_page(PREFERENCES_PAGE_TITLE, "❤️")
        if not self._plans_page_id:
            self._plans_page_id = self._create_child_page(PLANS_PAGE_TITLE, "📅")

    def _create_child_page(self, title: str, emoji: str) -> str:
        page = self.client.pages.create(
            parent={"type": "page_id", "page_id": self.parent_page_id},
            icon={"type": "emoji", "emoji": emoji},
            properties={"title": {"title": _rich_text(title)}},
        )
        return page["id"]

    # ------------------------------------------------------------ préférences

    def get_preferences(self) -> list[dict]:
        blocks = collect_paginated_api(
            self.client.blocks.children.list, block_id=self._preferences_page_id
        )
        return [
            {"id": b["id"], "texte": _plain(b["bulleted_list_item"]["rich_text"])}
            for b in blocks
            if b["type"] == "bulleted_list_item"
        ]

    def add_preference(self, text: str) -> str:
        result = self.client.blocks.children.append(
            block_id=self._preferences_page_id,
            children=[_block("bulleted_list_item", text)],
        )
        return result["results"][0]["id"]

    def remove_preference(self, preference_id: str) -> None:
        self.client.blocks.delete(block_id=preference_id)

    # ------------------------------------------------------------------ plats

    def list_dishes(self) -> list[dict]:
        pages = collect_paginated_api(
            self.client.data_sources.query, data_source_id=self._data_source_id
        )
        return [self._dish_from_page(p) for p in pages]

    def get_dish(self, dish_id: str) -> dict:
        return self._dish_from_page(self.client.pages.retrieve(page_id=dish_id))

    @staticmethod
    def _dish_from_page(page: dict) -> dict:
        props = page["properties"]
        last = props["Dernière planification"]["date"]
        return {
            "id": page["id"],
            "nom": _plain(props["Nom"]["title"]),
            "repas": [o["name"] for o in props["Repas"]["multi_select"]],
            "ingrédients": _plain(props["Ingrédients"]["rich_text"]),
            "portions": props["Portions"]["number"],
            "temps_min": props["Temps (min)"]["number"],
            "appréciation": props["Appréciation"]["number"],
            "tags": [o["name"] for o in props["Tags"]["multi_select"]],
            "dernière_planification": last["start"] if last else None,
            "notes": _plain(props["Notes"]["rich_text"]),
        }

    @staticmethod
    def _dish_properties(fields: dict) -> dict:
        props: dict = {}
        if fields.get("nom") is not None:
            props["Nom"] = {"title": _rich_text(fields["nom"])}
        if fields.get("repas") is not None:
            props["Repas"] = {"multi_select": [{"name": m} for m in fields["repas"]]}
        if fields.get("ingrédients") is not None:
            props["Ingrédients"] = {"rich_text": _rich_text("\n".join(fields["ingrédients"]))}
        for key, prop in (("portions", "Portions"), ("temps_min", "Temps (min)"), ("appréciation", "Appréciation")):
            if fields.get(key) is not None:
                props[prop] = {"number": fields[key]}
        if fields.get("tags") is not None:
            props["Tags"] = {"multi_select": [{"name": t.replace(",", " ")} for t in fields["tags"]]}
        if fields.get("notes") is not None:
            props["Notes"] = {"rich_text": _rich_text(fields["notes"])}
        return props

    def add_dish(self, fields: dict) -> str:
        page = self.client.pages.create(
            parent={"type": "data_source_id", "data_source_id": self._data_source_id},
            properties=self._dish_properties(fields),
        )
        return page["id"]

    def update_dish(self, dish_id: str, fields: dict) -> None:
        self.client.pages.update(page_id=dish_id, properties=self._dish_properties(fields))

    def delete_dish(self, dish_id: str) -> None:
        self.client.pages.update(page_id=dish_id, in_trash=True)

    def mark_planned(self, dish_ids: set[str], day: date) -> None:
        for dish_id in dish_ids:
            self.client.pages.update(
                page_id=dish_id,
                properties={"Dernière planification": {"date": {"start": day.isoformat()}}},
            )

    # ------------------------------------------------------------------ menus

    def save_plan(self, title: str, days: list[dict], shopping_list: list[dict], notes: str | None) -> str:
        blocks: list[dict] = []
        if notes:
            blocks.append(_block("callout", notes, icon={"type": "emoji", "emoji": "💡"}))
        blocks.append(_block("heading_1", "🗓️ Menu"))
        for day in days:
            blocks.append(_block("heading_3", day["jour"]))
            for meal in day["repas"]:
                line = f"{meal['type']} : {meal['plat']}"
                if meal.get("portions"):
                    line += f" ({meal['portions']} pers.)"
                blocks.append(_block("bulleted_list_item", line))
        blocks.append(_block("heading_1", "🛒 Liste de courses"))
        for section in shopping_list:
            blocks.append(_block("heading_3", section["rayon"]))
            for item in section["articles"]:
                blocks.append(_block("to_do", item, checked=False))

        page = self.client.pages.create(
            parent={"type": "page_id", "page_id": self._plans_page_id},
            icon={"type": "emoji", "emoji": "🥗"},
            properties={"title": {"title": _rich_text(title)}},
            children=blocks[:_NOTION_BLOCKS_PER_REQUEST],
        )
        for start in range(_NOTION_BLOCKS_PER_REQUEST, len(blocks), _NOTION_BLOCKS_PER_REQUEST):
            self.client.blocks.children.append(
                block_id=page["id"],
                children=blocks[start:start + _NOTION_BLOCKS_PER_REQUEST],
            )
        return page["url"]

"""Test de bout en bout hors-ligne : faux Notion en mémoire + réponses Claude scriptées."""

import itertools
from types import SimpleNamespace

from meal_agent.agent import MealAgent
from meal_agent.notion_store import NotionStore

_ids = (f"id-{n}" for n in itertools.count())


class FakeNotion:
    """Imite le sous-ensemble de notion_client.Client utilisé par NotionStore."""

    def __init__(self):
        self.children: dict[str, list[dict]] = {"root": []}
        self.pages: dict[str, dict] = {}
        self.blocks = SimpleNamespace(children=SimpleNamespace(list=self._list, append=self._append), delete=self._delete)
        self.databases = SimpleNamespace(create=self._create_db, retrieve=lambda database_id: {"data_sources": [{"id": "ds"}]})
        self.data_sources = SimpleNamespace(query=self._query)
        self.pages_ns = SimpleNamespace(create=self._create_page, update=self._update_page, retrieve=lambda page_id: self.pages[page_id])

    def _list(self, block_id, **_):
        return {"results": self.children.get(block_id, []), "has_more": False, "next_cursor": None}

    def _append(self, block_id, children):
        for c in children:
            c["id"] = next(_ids)
            for rt in c[c["type"]]["rich_text"]:
                rt["plain_text"] = rt["text"]["content"]
        self.children.setdefault(block_id, []).extend(children)
        return {"results": children}

    def _delete(self, block_id):
        for blocks in self.children.values():
            blocks[:] = [b for b in blocks if b["id"] != block_id]

    def _create_db(self, parent, title, **_):
        block = {"id": next(_ids), "type": "child_database", "child_database": {"title": title[0]["text"]["content"]}}
        self.children[parent["page_id"]].append(block)
        return {"data_sources": [{"id": "ds"}]}

    def _create_page(self, parent, properties, children=(), **_):
        page_id = next(_ids)
        if parent["type"] == "data_source_id":
            props = {
                "Nom": {"title": []}, "Repas": {"multi_select": []}, "Ingrédients": {"rich_text": []},
                "Portions": {"number": None}, "Temps (min)": {"number": None}, "Appréciation": {"number": None},
                "Tags": {"multi_select": []}, "Dernière planification": {"date": None}, "Notes": {"rich_text": []},
            }
            self.pages[page_id] = {"id": page_id, "properties": props, "in_trash": False}
            self._update_page(page_id, properties)
        else:
            title = properties["title"]["title"][0]["text"]["content"]
            self.children[parent["page_id"]].append({"id": page_id, "type": "child_page", "child_page": {"title": title}})
            self.children[page_id] = list(children)
        return {"id": page_id, "url": f"https://notion.so/{page_id}"}

    def _update_page(self, page_id, properties=None, in_trash=None):
        page = self.pages[page_id]
        if in_trash is not None:
            page["in_trash"] = in_trash
        for name, value in (properties or {}).items():
            for kind in ("title", "rich_text"):
                if kind in value:
                    value = {kind: [{"plain_text": rt["text"]["content"]} for rt in value[kind]]}
            page["properties"][name] = value

    def _query(self, data_source_id, **_):
        live = [p for p in self.pages.values() if not p["in_trash"]]
        return {"results": live, "has_more": False, "next_cursor": None}


def make_store():
    fake = FakeNotion()
    store = NotionStore.__new__(NotionStore)
    store.client = SimpleNamespace(
        blocks=fake.blocks, databases=fake.databases, data_sources=fake.data_sources, pages=fake.pages_ns
    )
    store.parent_page_id = "root"
    store._data_source_id = store._preferences_page_id = store._plans_page_id = None
    store.ensure_setup()
    return store, fake


def tool_use(name, tool_input, id_="t1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=tool_input)


class ScriptedClaude:
    """Renvoie une suite de réponses ; chaque élément peut être une fonction du state."""

    def __init__(self, script):
        self.script = iter(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        kwargs = {**kwargs, "messages": list(kwargs["messages"])}  # l'agent continue d'ajouter à la liste
        self.requests.append(kwargs)
        stop_reason, content = next(self.script)(kwargs)
        return SimpleNamespace(stop_reason=stop_reason, content=content)


def test_setup_is_idempotent():
    store, fake = make_store()
    store.ensure_setup()
    titles = [b.get("child_page", b.get("child_database"))["title"] for b in fake.children["root"]]
    assert sorted(titles) == ["Menus de la semaine", "Mes plats", "Préférences alimentaires"]


def test_full_flow_add_dish_then_plan():
    store, fake = make_store()
    dish = {
        "nom": "Pâtes carbonara", "repas": ["Dîner"], "portions": 2, "appréciation": 5,
        "ingrédients": ["200 g spaghetti", "100 g lardons", "2 œufs", "parmesan"],
    }

    def last_result(req):
        return req["messages"][-1]["content"][0]["content"]

    captured = {}
    claude = ScriptedClaude([
        lambda req: ("tool_use", [tool_use("add_preference", {"texte": "Je cuisine pour 2"}, "a"),
                                  tool_use("add_dish", dish, "b")]),
        lambda req: ("end_turn", [SimpleNamespace(type="text", text="C'est noté !")]),
        lambda req: ("tool_use", [tool_use("list_dishes", {})]),
        lambda req: captured.setdefault("dish_id", __import__("json").loads(last_result(req))[0]["id"]) and (
            "tool_use", [tool_use("compute_shopping_list", {"repas": [
                {"dish_id": captured["dish_id"], "portions": 2},
                {"dish_id": captured["dish_id"], "portions": 4},
            ]})]),
        lambda req: captured.setdefault("courses", last_result(req)) and (
            "tool_use", [tool_use("save_weekly_plan", {
                "titre": "Semaine test",
                "jours": [{"jour": "Lundi", "repas": [{"type": "Dîner", "plat": "Pâtes carbonara", "dish_id": captured["dish_id"], "portions": 2}]}],
                "liste_de_courses": [{"rayon": "Épicerie", "articles": ["600 g spaghetti"]}],
            })]),
        lambda req: ("end_turn", [SimpleNamespace(type="text", text="Voici ton menu")]),
    ])
    agent = MealAgent(store, client=claude)

    assert agent.chat("J'adore les pâtes carbo, on est 2") == "C'est noté !"
    assert store.get_preferences()[0]["texte"] == "Je cuisine pour 2"
    assert agent.chat("Que manger cette semaine ?") == "Voici ton menu"

    assert '"600 g"' in captured["courses"] and '"6"' in captured["courses"]  # 2 + 4 portions -> x3
    assert store.list_dishes()[0]["dernière_planification"] is not None
    plan_page = [b for b in fake.children[store._plans_page_id]][0]
    plan_blocks = fake.children[plan_page["id"]]
    assert any(b["type"] == "to_do" for b in plan_blocks)

    # Les réponses de l'assistant et les résultats d'outils sont bien rejoués à chaque tour.
    req = claude.requests[-1]
    assert req["model"] == "claude-opus-5-5" and req["thinking"] == {"type": "adaptive"}
    assert all(not r["content"][0].get("is_error") for r in [m for m in req["messages"] if m["role"] == "user" and isinstance(m["content"], list)])


def test_tool_errors_are_reported_to_claude():
    store, _ = make_store()
    claude = ScriptedClaude([
        lambda req: ("tool_use", [tool_use("delete_dish", {"dish_id": "inexistant"})]),
        lambda req: ("end_turn", [SimpleNamespace(type="text", text="oups")]),
    ])
    MealAgent(store, client=claude).chat("supprime ça")
    result = claude.requests[-1]["messages"][-1]["content"][0]
    assert result["is_error"] is True

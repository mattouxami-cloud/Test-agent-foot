"""Agent Claude : discute avec l'utilisateur et pilote le stockage Notion."""

from __future__ import annotations

import json
from datetime import date
from typing import Callable

import anthropic

from .notion_store import MEAL_TYPES, NotionStore
from .shopping import aggregate

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
Tu es un assistant culinaire personnel qui parle français. Tu aides l'utilisateur \
à garder une trace de ce qu'il aime manger au quotidien et à planifier ses repas.

Toutes les données vivent dans Notion et tu y accèdes uniquement via tes outils : \
une base « Mes plats » (un plat par ligne, avec ses ingrédients), une page de \
préférences générales (allergies, aliments détestés, nombre de personnes, budget, \
placards déjà remplis, etc.) et une page de menus hebdomadaires.

Quand l'utilisateur te parle de ce qu'il aime :
- Enregistre chaque plat avec add_dish. Écris les ingrédients un par ligne au \
format « quantité unité ingrédient » (ex. « 200 g pâtes », « 2 oignons », \
« 1 c. à soupe huile d'olive », « sel ») pour le nombre de portions indiqué : \
c'est ce format qui permet de calculer la liste de courses.
- Si l'utilisateur ne donne pas les ingrédients, propose une recette classique \
raisonnable, enregistre-la et dis-lui qu'il peut la corriger.
- Avant d'ajouter un plat, vérifie avec list_dishes qu'il n'existe pas déjà \
(sinon mets-le à jour avec update_dish).
- Les informations générales (allergies, « je cuisine pour 2 », « pas de \
poisson le lundi », « j'ai toujours du riz et des épices ») vont dans les \
préférences avec add_preference.

Quand l'utilisateur te demande quoi manger / un menu de la semaine :
1. Lis get_preferences et list_dishes.
2. Compose une rotation sur 7 jours (ou la période demandée) pour les repas \
demandés (par défaut déjeuner et dîner). Privilégie les plats bien notés, varie \
les protéines et les types de cuisine, évite de répéter un plat dans la semaine \
sauf pour des restes assumés, et favorise les plats dont la dernière \
planification est la plus ancienne. Respecte strictement les allergies et dégoûts.
3. Tu peux suggérer 1 ou 2 nouveaux plats dans l'esprit de ses goûts : \
enregistre-les d'abord avec add_dish (tag « suggestion ») pour que leurs \
ingrédients soient comptés.
4. Appelle compute_shopping_list avec tous les repas retenus, puis range les \
articles par rayon de supermarché (Fruits & légumes, Boucherie / Poissonnerie, \
Crèmerie, Épicerie, Surgelés, Boulangerie, etc.). Retire ce que les préférences \
indiquent comme déjà en stock.
5. Enregistre le tout avec save_weekly_plan, puis présente le menu et la liste \
de courses de façon concise avec le lien Notion.

Sois chaleureux mais bref. N'invente jamais d'identifiants : utilise ceux \
renvoyés par les outils.
"""

_DISH_FIELDS = {
    "nom": {"type": "string", "description": "Nom du plat."},
    "repas": {
        "type": "array",
        "items": {"type": "string", "enum": MEAL_TYPES},
        "description": "Moments de la journée où ce plat convient.",
    },
    "ingrédients": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Un ingrédient par élément, format « quantité unité ingrédient » (ex. « 200 g pâtes »).",
    },
    "portions": {"type": "integer", "description": "Nombre de portions pour les quantités données."},
    "temps_min": {"type": "integer", "description": "Temps de préparation total en minutes."},
    "appréciation": {"type": "integer", "minimum": 1, "maximum": 5, "description": "À quel point l'utilisateur aime ce plat (1 à 5)."},
    "tags": {"type": "array", "items": {"type": "string"}, "description": "Ex. végétarien, rapide, italien, hiver, suggestion."},
    "notes": {"type": "string", "description": "Remarques libres (variante, astuce, source)."},
}

TOOLS = [
    {
        "name": "get_preferences",
        "description": "Lit les préférences alimentaires générales de l'utilisateur stockées dans Notion.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_preference",
        "description": "Ajoute une préférence générale (allergie, dégoût, nombre de personnes, budget, produits toujours en stock...).",
        "input_schema": {
            "type": "object",
            "properties": {"texte": {"type": "string"}},
            "required": ["texte"],
        },
    },
    {
        "name": "remove_preference",
        "description": "Supprime une préférence devenue fausse, à partir de son id renvoyé par get_preferences.",
        "input_schema": {
            "type": "object",
            "properties": {"preference_id": {"type": "string"}},
            "required": ["preference_id"],
        },
    },
    {
        "name": "list_dishes",
        "description": "Liste tous les plats enregistrés avec leurs ingrédients, note, tags et date de dernière planification.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_dish",
        "description": "Enregistre un nouveau plat que l'utilisateur aime.",
        "input_schema": {
            "type": "object",
            "properties": _DISH_FIELDS,
            "required": ["nom", "repas", "ingrédients", "portions"],
        },
    },
    {
        "name": "update_dish",
        "description": "Modifie un plat existant. Seuls les champs fournis sont modifiés ; les ingrédients fournis remplacent la liste entière.",
        "input_schema": {
            "type": "object",
            "properties": {"dish_id": {"type": "string"}, **_DISH_FIELDS},
            "required": ["dish_id"],
        },
    },
    {
        "name": "delete_dish",
        "description": "Supprime (met à la corbeille Notion) un plat que l'utilisateur n'aime plus.",
        "input_schema": {
            "type": "object",
            "properties": {"dish_id": {"type": "string"}},
            "required": ["dish_id"],
        },
    },
    {
        "name": "compute_shopping_list",
        "description": "Calcule la liste de courses agrégée (quantités additionnées et mises à l'échelle) pour une liste de repas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repas": {
                    "type": "array",
                    "description": "Un élément par repas planifié (un plat cuisiné deux fois apparaît deux fois).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "dish_id": {"type": "string"},
                            "portions": {"type": "number", "description": "Nombre de portions à cuisiner pour ce repas."},
                        },
                        "required": ["dish_id", "portions"],
                    },
                }
            },
            "required": ["repas"],
        },
    },
    {
        "name": "save_weekly_plan",
        "description": "Crée dans Notion une page avec le menu de la semaine et la liste de courses (cases à cocher). Renvoie le lien de la page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "titre": {"type": "string", "description": "Ex. « Semaine du 12 octobre »."},
                "jours": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "jour": {"type": "string", "description": "Ex. « Lundi 12/10 »."},
                            "repas": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string", "description": "Ex. Déjeuner, Dîner."},
                                        "plat": {"type": "string", "description": "Nom du plat (ou « Restes de ... »)."},
                                        "dish_id": {"type": "string", "description": "Id du plat s'il est dans la base."},
                                        "portions": {"type": "number"},
                                    },
                                    "required": ["type", "plat"],
                                },
                            },
                        },
                        "required": ["jour", "repas"],
                    },
                },
                "liste_de_courses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rayon": {"type": "string"},
                            "articles": {"type": "array", "items": {"type": "string"}, "description": "Ex. « 900 g pâtes »."},
                        },
                        "required": ["rayon", "articles"],
                    },
                },
                "notes": {"type": "string", "description": "Conseils de préparation (batch cooking, restes...)."},
            },
            "required": ["titre", "jours", "liste_de_courses"],
        },
    },
]


class MealAgent:
    def __init__(self, store: NotionStore, client: anthropic.Anthropic | None = None):
        self.store = store
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict] = []
        self._handlers: dict[str, Callable[[dict], object]] = {
            "get_preferences": lambda _: store.get_preferences(),
            "add_preference": lambda i: {"id": store.add_preference(i["texte"])},
            "remove_preference": lambda i: store.remove_preference(i["preference_id"]) or "ok",
            "list_dishes": lambda _: store.list_dishes(),
            "add_dish": lambda i: {"id": store.add_dish(i)},
            "update_dish": lambda i: store.update_dish(i["dish_id"], i) or "ok",
            "delete_dish": lambda i: store.delete_dish(i["dish_id"]) or "ok",
            "compute_shopping_list": self._compute_shopping_list,
            "save_weekly_plan": self._save_weekly_plan,
        }

    # ------------------------------------------------------------ outils

    def _compute_shopping_list(self, tool_input: dict) -> dict:
        dishes = {d["id"]: d for d in self.store.list_dishes()}
        to_aggregate, unknown = [], []
        for meal in tool_input["repas"]:
            dish = dishes.get(meal["dish_id"])
            if dish is None:
                unknown.append(meal["dish_id"])
                continue
            factor = meal["portions"] / dish["portions"] if dish["portions"] else 1
            to_aggregate.append((dish["nom"], dish["ingrédients"], factor))
        result: dict = {"articles": aggregate(to_aggregate)}
        if unknown:
            result["dish_ids_inconnus"] = unknown
        return result

    def _save_weekly_plan(self, tool_input: dict) -> dict:
        url = self.store.save_plan(
            title=tool_input["titre"],
            days=tool_input["jours"],
            shopping_list=tool_input["liste_de_courses"],
            notes=tool_input.get("notes"),
        )
        planned = {m["dish_id"] for d in tool_input["jours"] for m in d["repas"] if m.get("dish_id")}
        self.store.mark_planned(planned, date.today())
        return {"url": url}

    def _run_tool(self, block) -> dict:
        try:
            output = self._handlers[block.name](block.input)
            content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            return {"type": "tool_result", "tool_use_id": block.id, "content": content}
        except Exception as exc:  # renvoyé à Claude pour qu'il corrige ou prévienne l'utilisateur
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"Erreur : {exc}",
                "is_error": True,
            }

    # ------------------------------------------------------------ boucle

    def chat(self, user_text: str, on_tool: Callable[[str], None] | None = None) -> str:
        """Envoie un message de l'utilisateur et renvoie la réponse finale de Claude."""
        self.messages.append({"role": "user", "content": user_text})
        system = SYSTEM_PROMPT + f"\nDate du jour : {date.today().isoformat()}."

        while True:
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=system,
                tools=TOOLS,
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return "Désolé, je ne peux pas répondre à cette demande."
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason == "tool_use":
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if on_tool:
                    for block in tool_uses:
                        on_tool(block.name)
                results = [self._run_tool(b) for b in tool_uses]
                self.messages.append({"role": "user", "content": results})
                continue

            text = "".join(b.text for b in response.content if b.type == "text")
            if response.stop_reason == "max_tokens":
                text += "\n[Réponse tronquée : limite de longueur atteinte]"
            return text

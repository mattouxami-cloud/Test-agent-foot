"""Point d'entrée : `python -m meal_agent`."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from .agent import MealAgent
from .notion_store import NotionStore

_TOOL_LABELS = {
    "get_preferences": "lecture des préférences",
    "add_preference": "ajout d'une préférence",
    "remove_preference": "suppression d'une préférence",
    "list_dishes": "lecture des plats",
    "add_dish": "ajout d'un plat",
    "update_dish": "mise à jour d'un plat",
    "delete_dish": "suppression d'un plat",
    "compute_shopping_list": "calcul de la liste de courses",
    "save_weekly_plan": "enregistrement du menu dans Notion",
}


def main() -> None:
    load_dotenv()
    missing = [v for v in ("NOTION_TOKEN", "NOTION_PARENT_PAGE_ID") if not os.environ.get(v)]
    if missing:
        sys.exit(f"Variables manquantes dans .env : {', '.join(missing)} (voir .env.example)")

    store = NotionStore(os.environ["NOTION_TOKEN"], os.environ["NOTION_PARENT_PAGE_ID"])
    print("Connexion à Notion...")
    store.ensure_setup()
    agent = MealAgent(store)

    print("🍽️  Agent repas prêt. Dis-moi ce que tu aimes manger, ou demande « que manger cette semaine ? ».")
    print("   (tape « quitter » pour sortir)\n")
    while True:
        try:
            user_text = input("toi > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_text.lower() in {"quitter", "exit", "quit"}:
            break
        if not user_text:
            continue
        reply = agent.chat(user_text, on_tool=lambda name: print(f"   … {_TOOL_LABELS.get(name, name)}"))
        print(f"\nagent > {reply}\n")


if __name__ == "__main__":
    main()

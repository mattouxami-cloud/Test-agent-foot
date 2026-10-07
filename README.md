# 🍽️ Agent repas (Claude + Notion)

Un agent conversationnel à qui tu racontes ce que tu aimes manger. Il range tout
dans Notion, puis, quand tu lui demandes quoi manger, il te propose une
**rotation de plats sur la semaine** et la **liste de courses** correspondante
(cases à cocher dans Notion, triées par rayon).

## Ce qui est créé dans Notion

Sous la page que tu choisis, l'agent crée automatiquement au premier lancement :

| Élément | Contenu |
|---|---|
| 🍽️ **Mes plats** (base de données) | Nom, repas (petit-déj / déjeuner / dîner / snack), ingrédients, portions, temps, appréciation 1-5, tags, date de dernière planification, notes |
| ❤️ **Préférences alimentaires** (page) | Une puce par préférence : allergies, dégoûts, nombre de personnes, produits toujours en stock… |
| 📅 **Menus de la semaine** (page) | Une sous-page par menu généré : le planning jour par jour + la liste de courses à cocher |

Tu peux aussi modifier tes plats directement dans Notion : l'agent relit tout à
chaque demande. Pour les ingrédients, garde le format `quantité unité ingrédient`
(un par ligne), par exemple `200 g pâtes`, `2 oignons`, `1 c. à soupe huile d'olive`, `sel`.

## Installation

1. **Python 3.10+**, puis :
   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   ```
2. **Clé Claude** : crée une clé sur https://platform.claude.com/settings/keys et
   mets-la dans `ANTHROPIC_API_KEY`.
3. **Intégration Notion** :
   - va sur https://www.notion.so/profile/integrations → *Nouvelle intégration*
     (type interne) et copie le jeton dans `NOTION_TOKEN` ;
   - crée une page Notion vide (ex. « Repas »), puis `•••` → *Connexions* →
     ajoute ton intégration ;
   - copie l'id de la page (les 32 caractères à la fin de son URL) dans
     `NOTION_PARENT_PAGE_ID`.

## Utilisation

```bash
python -m meal_agent
```

Exemples :

```
toi > On est 2 à la maison, je suis allergique aux noix et j'ai toujours du riz, des pâtes et des épices.
toi > J'adore le poulet curry coco, les lasagnes de ma mère (5/5) et les salades César le midi.
toi > Je n'aime plus trop le chili, supprime-le.
toi > Qu'est-ce que je mange cette semaine ? Juste les dîners, et un truc rapide le mercredi.
```

Pour la dernière demande, l'agent :
1. lit tes préférences et tes plats ;
2. compose une rotation variée, en privilégiant les plats bien notés et ceux
   que tu n'as pas mangés depuis longtemps (il peut glisser 1-2 suggestions
   nouvelles dans ton style, ajoutées avec le tag `suggestion`) ;
3. calcule les quantités de courses (additionnées et mises à l'échelle du
   nombre de portions) ;
4. retire ce que tu as déjà en stock, trie par rayon ;
5. crée la page du menu dans Notion et te donne le lien.

## Fonctionnement technique

- `meal_agent/agent.py` : boucle d'agent avec l'API Claude (`claude-opus-5-5`,
  réflexion adaptative) et les outils exposés au modèle. Le repli automatique
  en cas de refus (`fallbacks: "default"`) est activé.
- `meal_agent/notion_store.py` : lecture/écriture Notion (API `2025-09-03`, data sources).
- `meal_agent/shopping.py` : lecture des lignes d'ingrédients, conversions
  (kg→g, cl→ml…) et agrégation de la liste de courses, en Python pour que les
  quantités soient exactes.

Tests (hors-ligne, sans clé) :

```bash
pip install pytest && python -m pytest
```

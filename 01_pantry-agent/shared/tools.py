"""Domain tool implementations (pantry, recipes) shared by both agents.

Plain Python only: no anthropic / agent-SDK imports. Every tool returns a
compact string (at most MAX_OUT chars); domain errors raise shared.errors.*.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from shared.errors import InvalidInput, KashrutConflict, RecipeNotFound

DATA_DIR = Path(__file__).parent / "data"
RECIPES_DIR = DATA_DIR / "recipes"
PRODUCTS_STUB = DATA_DIR / "products_stub.json"
MAX_OUT = 500

LIST_PREF_KEYS = {"dislikes", "allergies", "likes"}
STATE_FILES = {
    "preferences.json": {},
    "pantry.json": [],
    "meal_plan.json": {},
    "shopping_list.json": [],
}

OFF_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_FIELDS = ["energy-kcal_100g", "proteins_100g", "fat_100g", "carbohydrates_100g", "sugars_100g"]


# ---------- helpers ----------

def runtime_dir() -> Path:
    """Runtime state directory; PANTRY_DATA_DIR overrides it (used by tests)."""
    return Path(os.environ.get("PANTRY_DATA_DIR") or DATA_DIR / "runtime")


def _compact(text: str, limit: int = MAX_OUT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _num(q: float) -> str:
    q = round(float(q), 2)
    return str(int(q)) if q == int(q) else str(q)


def _load(name: str):
    path = runtime_dir() / name
    if not path.exists():
        return json.loads(json.dumps(STATE_FILES[name]))  # fresh copy of the default
    return json.loads(path.read_text(encoding="utf-8"))


def _save(name: str, data) -> None:
    path = runtime_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _norm(s: str) -> str:
    return str(s).strip().lower()


def reset_state() -> None:
    """Wipe the runtime state files and re-create them empty."""
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    for name, empty in STATE_FILES.items():
        (d / name).unlink(missing_ok=True)
        _save(name, empty)


def _all_recipes() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECIPES_DIR.glob("*.json"))]


def _recipe(recipe_id: str) -> dict:
    path = RECIPES_DIR / f"{_norm(recipe_id)}.json"
    if not path.exists():
        raise RecipeNotFound(f"No recipe with id '{recipe_id}'. Use search_recipes to find valid ids.")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- recipes ----------

def search_recipes(query: str = "", kind: str | None = None,
                   exclude_ingredients: list[str] | None = None, max_results: int = 5) -> str:
    q = _norm(query)
    excl = [_norm(x) for x in (exclude_ingredients or []) if str(x).strip()]
    if kind and _norm(kind) not in ("meat", "dairy", "parve"):
        raise InvalidInput("kind must be one of: meat, dairy, parve.")
    hits = []
    for r in _all_recipes():
        ingr = [_norm(x["name"]) for x in r["ingredients"]]
        if kind and r["kind"] != _norm(kind):
            continue
        if any(e in name for e in excl for name in ingr):
            continue
        haystack = " ".join([_norm(r["name"]), *map(_norm, r["tags"]), *ingr])
        if q and not all(word in haystack for word in q.split()):
            continue
        hits.append(f"{r['id']} {r['name']} ({r['kind']})")
    if not hits:
        return "No recipes found."
    return _compact("\n".join(hits[: max(1, int(max_results))]))


def get_recipe(recipe_id: str) -> str:
    r = _recipe(recipe_id)
    ingr = "; ".join(f"{x['name']} {_num(x['qty'])} {x['unit']}" for x in r["ingredients"])
    return _compact(f"{r['id']} {r['name']} ({r['kind']}, serves {r['servings']}): {ingr}")


# ---------- meal plan ----------
# meal_plan.json: {"<day>": {"<meal>": ["r01", "r12", ...]}}

def save_meal_plan(day: str, meal: str, recipe_id: str) -> str:
    r = _recipe(recipe_id)
    day, meal = _norm(day), _norm(meal)
    plan = _load("meal_plan.json")
    slot = plan.setdefault(day, {}).setdefault(meal, [])
    kinds = {_recipe(rid)["kind"] for rid in slot} | {r["kind"]}
    if {"meat", "dairy"} <= kinds:
        raise KashrutConflict(
            f"Cannot add {r['id']} {r['name']} ({r['kind']}) to {day} {meal}: "
            f"it already has {'dairy' if r['kind'] == 'meat' else 'meat'}. Meat and dairy can't share a meal.")
    if r["id"] not in slot:
        slot.append(r["id"])
    _save("meal_plan.json", plan)
    return _compact(f"Saved {r['id']} {r['name']} to {day} {meal}. Slot now: {', '.join(slot)}")


def remove_from_meal_plan(day: str, meal: str) -> str:
    day, meal = _norm(day), _norm(meal)
    plan = _load("meal_plan.json")
    removed = plan.get(day, {}).pop(meal, None)
    if removed is None:
        return f"Nothing planned for {day} {meal}."
    if not plan[day]:
        del plan[day]
    _save("meal_plan.json", plan)
    return _compact(f"Removed {day} {meal} ({', '.join(removed)}).")


# ---------- pantry ----------
# pantry.json: [{"name", "qty", "unit"}]

def update_pantry(item: str, qty: float, unit: str) -> str:
    name, unit = _norm(item), _norm(unit)
    qty = float(qty)
    if qty < 0:
        raise InvalidInput("qty must be >= 0 (use 0 to remove the item).")
    pantry = [p for p in _load("pantry.json") if not (p["name"] == name and p["unit"] == unit)]
    if qty > 0:
        pantry.append({"name": name, "qty": qty, "unit": unit})
    _save("pantry.json", pantry)
    msg = f"Pantry: {name} = {_num(qty)} {unit}." if qty > 0 else f"Pantry: removed {name} ({unit})."
    return _compact(f"{msg} {len(pantry)} items in pantry.")


def build_shopping_list(servings: int) -> str:
    servings = int(servings)
    if servings <= 0:
        raise InvalidInput("servings must be a positive integer.")
    plan = _load("meal_plan.json")
    planned = [rid for meals in plan.values() for slot in meals.values() for rid in slot]
    if not planned:
        return "Meal plan is empty; nothing to shop for."

    need: dict[tuple[str, str], float] = {}
    for rid in planned:
        r = _recipe(rid)
        factor = servings / r["servings"]
        for x in r["ingredients"]:
            key = (_norm(x["name"]), _norm(x["unit"]))
            need[key] = need.get(key, 0.0) + x["qty"] * factor
    for p in _load("pantry.json"):
        key = (p["name"], p["unit"])
        if key in need:
            need[key] -= p["qty"]

    items = [{"name": n, "qty": round(q, 2), "unit": u} for (n, u), q in sorted(need.items()) if q > 0]
    _save("shopping_list.json", items)
    body = "; ".join(f"{x['name']} {_num(x['qty'])} {x['unit']}" for x in items)
    return _compact(f"Shopping list ({len(items)} items, {len(planned)} recipes, {servings} servings each): {body}")


# ---------- preferences ----------

def remember(key: str, value) -> str:
    key = _norm(key)
    prefs = _load("preferences.json")
    current = prefs.get(key)
    if isinstance(current, list) or (current is None and key in LIST_PREF_KEYS):
        lst = current or []
        if value not in lst:
            lst.append(value)
        prefs[key] = lst
    else:
        prefs[key] = value
    _save("preferences.json", prefs)
    return _compact(f"Remembered {key} = {json.dumps(prefs[key], ensure_ascii=False)}")


def forget(key: str, value=None) -> str:
    key = _norm(key)
    prefs = _load("preferences.json")
    if key not in prefs:
        return f"No preference '{key}' stored."
    if value is None:
        del prefs[key]
        msg = f"Forgot {key}."
    elif isinstance(prefs[key], list) and value in prefs[key]:
        prefs[key].remove(value)
        msg = f"Removed {json.dumps(value, ensure_ascii=False)} from {key}."
    else:
        return f"'{value}' is not in {key}."
    _save("preferences.json", prefs)
    return _compact(msg)


def get_preferences() -> str:
    prefs = _load("preferences.json")
    if not prefs:
        return "No preferences stored."
    return _compact(json.dumps(prefs, ensure_ascii=False, separators=(",", ":")))


# ---------- external ----------

def _format_product(p: dict, offline: bool) -> str:
    nutr = p.get("nutriments", p)
    facts = ", ".join(f"{f.replace('_100g', '')}={nutr[f]}" for f in OFF_FIELDS if nutr.get(f) is not None)
    text = f"{p.get('product_name') or '?'} — {p.get('brands') or '?'}; per 100g: {facts or 'n/a'}"
    return _compact(("(offline) " if offline else "") + text)


def _stub_product(name: str) -> str:
    stub = json.loads(PRODUCTS_STUB.read_text(encoding="utf-8"))
    n = _norm(name)
    key = next((k for k in stub if k != "default" and (k in n or n in k)), "default")
    return _format_product(stub[key], offline=True)


def lookup_product(name: str) -> str:
    if os.environ.get("PANTRY_OFFLINE") == "1":
        return _stub_product(name)
    try:
        resp = requests.get(
            OFF_URL,
            params={"search_terms": name, "search_simple": 1, "action": "process", "json": 1,
                    "page_size": 1, "fields": "product_name,brands,nutriments"},
            headers={"User-Agent": "pantry-agent-tamar/0.1 (university assignment)"},
            timeout=5,
        )
        resp.raise_for_status()
        products = resp.json().get("products") or []
    except (requests.RequestException, ValueError):
        return _stub_product(name)
    if not products:
        return f"No product found for '{name}'."
    return _format_product(products[0], offline=False)


# name -> callable, for harness dispatch; keys match shared.tool_specs.TOOL_SPECS
TOOLS = {
    "search_recipes": search_recipes,
    "get_recipe": get_recipe,
    "save_meal_plan": save_meal_plan,
    "remove_from_meal_plan": remove_from_meal_plan,
    "update_pantry": update_pantry,
    "build_shopping_list": build_shopping_list,
    "remember": remember,
    "forget": forget,
    "get_preferences": get_preferences,
    "lookup_product": lookup_product,
}

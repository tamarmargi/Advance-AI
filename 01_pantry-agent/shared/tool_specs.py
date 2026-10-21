"""Single source of tool names, descriptions and input JSON schemas for both harnesses."""

_PREF_VALUE = {"type": ["string", "number", "boolean"]}

TOOL_SPECS = [
    {
        "name": "search_recipes",
        "description": "Search the recipe collection. Matches all query words against name, tags and ingredients. "
                       "Returns one 'id name (kind)' line per match. Use exclude_ingredients for dislikes/allergies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text words, e.g. 'soup winter'. Empty = all."},
                "kind": {"type": "string", "enum": ["meat", "dairy", "parve"]},
                "exclude_ingredients": {"type": "array", "items": {"type": "string"},
                                        "description": "Skip recipes with an ingredient containing any of these."},
                "max_results": {"type": "integer", "minimum": 1, "default": 5},
            },
        },
    },
    {
        "name": "get_recipe",
        "description": "Get a recipe's kind, base servings and ingredient list by id (e.g. 'r03').",
        "input_schema": {
            "type": "object",
            "properties": {"recipe_id": {"type": "string"}},
            "required": ["recipe_id"],
        },
    },
    {
        "name": "save_meal_plan",
        "description": "Add a recipe to a day+meal slot (a slot can hold several recipes). "
                       "Fails if it would mix meat and dairy in that slot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day": {"type": "string", "description": "e.g. 'sunday'"},
                "meal": {"type": "string", "description": "e.g. 'breakfast', 'lunch', 'dinner'"},
                "recipe_id": {"type": "string"},
            },
            "required": ["day", "meal", "recipe_id"],
        },
    },
    {
        "name": "remove_from_meal_plan",
        "description": "Clear a day+meal slot from the meal plan.",
        "input_schema": {
            "type": "object",
            "properties": {"day": {"type": "string"}, "meal": {"type": "string"}},
            "required": ["day", "meal"],
        },
    },
    {
        "name": "update_pantry",
        "description": "Set how much of an item is in the pantry (replaces the previous amount for that "
                       "item+unit; qty 0 removes it). Use the same units as recipes (g, ml, unit, tbsp, tsp, clove...).",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string"},
                "qty": {"type": "number", "minimum": 0},
                "unit": {"type": "string"},
            },
            "required": ["item", "qty", "unit"],
        },
    },
    {
        "name": "build_shopping_list",
        "description": "Build the shopping list from every planned recipe, scaled to the given servings per recipe, "
                       "minus what is in the pantry. Saves it and returns a summary.",
        "input_schema": {
            "type": "object",
            "properties": {"servings": {"type": "integer", "minimum": 1,
                                        "description": "People to cook for, usually the household size."}},
            "required": ["servings"],
        },
    },
    {
        "name": "remember",
        "description": "Store a lasting user preference, e.g. household_size=4, kashrut='strict', dislikes='mushrooms'. "
                       "List keys (dislikes, allergies, likes) get the value appended.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": _PREF_VALUE},
            "required": ["key", "value"],
        },
    },
    {
        "name": "forget",
        "description": "Delete a preference key, or remove one value from a list preference if value is given.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": _PREF_VALUE},
            "required": ["key"],
        },
    },
    {
        "name": "get_preferences",
        "description": "Return all stored user preferences. Check this before planning meals.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lookup_product",
        "description": "Look up a store product on Open Food Facts: name, brand and nutrition per 100g.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Product name, e.g. 'tahini'"}},
            "required": ["name"],
        },
    },
]

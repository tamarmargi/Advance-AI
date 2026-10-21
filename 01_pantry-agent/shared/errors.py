"""Domain exceptions raised by shared/tools.py."""


class PantryError(Exception):
    """Base class for all domain errors; the message is meant to be shown to the model."""


class RecipeNotFound(PantryError):
    """The given recipe id does not exist."""


class KashrutConflict(PantryError):
    """Saving the recipe would mix meat and dairy in the same day+meal."""


class InvalidInput(PantryError):
    """An argument is out of range or malformed."""

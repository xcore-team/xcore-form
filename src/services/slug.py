"""Génération de slugs uniques pour les formulaires."""
from __future__ import annotations

import re
import unicodedata


def slugify(text: str) -> str:
    """Convertit un titre en slug URL-safe."""
    # Normalise les caractères unicode (supprime les accents)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    # Remplace tout ce qui n'est pas alphanumérique par un tiret
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text or "form"


async def unique_slug(base_title: str, exists_fn, max_attempts: int = 10) -> str:
    """
    Génère un slug unique en ajoutant un suffixe si nécessaire.
    exists_fn(slug) → bool : vérifie si le slug est déjà pris.
    """
    import random
    import string

    base = slugify(base_title)[:80]  # limite la longueur

    # Essai sans suffixe
    if not await exists_fn(base):
        return base

    # Essai avec suffixe numérique
    for i in range(2, max_attempts + 2):
        candidate = f"{base}-{i}"
        if not await exists_fn(candidate):
            return candidate

    # Fallback : suffixe aléatoire
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{base}-{suffix}"
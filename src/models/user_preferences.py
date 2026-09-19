"""Per-account display preferences (Theming + Typography release).

One row per signed-in account, keyed by the same normalized email as
user_accounts, holding only the chosen color theme — System (follow the
operating system), Dark, or Light. Stored in its own table rather than
as a column on user_accounts so adding it never alters an existing
table. No other preference, profile, or tracking field lives here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ThemePreference = Literal["system", "dark", "light"]
THEME_PREFERENCES: tuple[ThemePreference, ...] = ("system", "dark", "light")


def is_theme_preference(value: object) -> bool:
    return value in THEME_PREFERENCES


@dataclass(frozen=True)
class UserPreferences:
    email: str
    theme_preference: ThemePreference
    updated_at: str

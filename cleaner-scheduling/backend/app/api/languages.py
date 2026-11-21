from typing import Annotated, Literal

from pydantic import AfterValidator, Field


def unique_reading_languages(value: list[Literal["en", "es", "pt"]]) -> list[Literal["en", "es", "pt"]]:
    if len(value) != len(set(value)):
        raise ValueError("Select each reading language only once.")
    return sorted(value)


ReadingLanguages = Annotated[
    list[Literal["en", "es", "pt"]], Field(max_length=3), AfterValidator(unique_reading_languages)
]

PreferredLanguage = Literal["en", "es", "pt"]

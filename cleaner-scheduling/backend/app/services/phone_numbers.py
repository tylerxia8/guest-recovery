"""Normalize common US entry formats and explicit international numbers."""

import re

from app.domain.errors import AppError


def normalize_phone(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"\+?[0-9 ().-]+", value):
        raise AppError("validation_error", "Enter a phone number, for example (704) 555-0123.", 422)
    digits = re.sub(r"\D", "", value)
    if not value.startswith("+"):
        if len(digits) == 10:
            digits = "1" + digits
        elif len(digits) != 11 or not digits.startswith("1"):
            raise AppError("validation_error", "Use a 10-digit US number or include the country code with +.", 422)
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        raise AppError("validation_error", "Enter a valid phone number including its country code.", 422)
    return "+" + digits

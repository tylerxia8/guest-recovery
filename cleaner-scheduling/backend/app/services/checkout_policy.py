from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, time
from typing import Literal

from sqlalchemy.orm import Session

from app.models import Reservation
from app.services.weekly_availability import NY
from app.workspaces import LEGACY_WORKSPACE_ID


def baseline_checkout(session: Session, reservation: Reservation) -> datetime:
    if session.info.get("workspace_id", LEGACY_WORKSPACE_ID) == LEGACY_WORKSPACE_ID:
        return reservation.checkout_at
    morning = datetime.combine(reservation.checkout_at.astimezone(NY).date(), time(10), tzinfo=NY)
    return max(reservation.checkout_at, morning.astimezone(UTC))


def departure_message(body: str) -> Literal["departed", "still_present", "unknown"]:
    if not body or len(body) > 4000:
        return "unknown"
    normalized = unicodedata.normalize("NFKD", body.casefold()).replace("’", "'")
    value = " ".join("".join(c for c in normalized if not unicodedata.combining(c)).split())
    if re.search(r"[?¿<>\"“”]|(?:^|\s)>|\b(?:forwarded|quoted|said|says|dijo|disse|message from)\b", value):
        return "unknown"
    if re.search(
        r"\b(?:we are|we're|i am|i'm) still (?:here|inside|in the (?:house|unit|apartment))\b"
        r"|\b(?:we are|we're|i am|i'm|the kids are|the children are|my family is|our family is) "
        r"(?:still )?(?:inside|in the (?:house|unit|apartment))\b"
        r"|\b(?:aun|todavia) (?:estamos|estoy) (?:aqui|dentro)\b"
        r"|\b(?:ainda estamos|ainda estou) (?:aqui|dentro)\b",
        value,
    ):
        return "still_present"
    if re.search(
        r"\b(?:not|never|haven't|havent|hasn't|hasnt|didn't|didnt|don't|dont|no|nao|nunca|if|si|se|"
        r"will|would|could|can|should|going to|plan|planning|tomorrow|later|soon|when|once|"
        r"think|guess|maybe|might|perhaps|probably|but|except|pero|aunque|mas|ainda|todavia|"
        r"back|return|returned|volver|volvemos|voltar|voltamos|"
        r"vamos|iremos|saldremos|saldre|sair|salir|quando|cuando|bags|luggage|maletas|bagagem|"
        r"friend|friends|companion|companions|partner|husband|wife|amigo|amigos|esposa|esposo|"
        r"someone|somebody|anyone|anybody|others|remains|remaining|staying|still|"
        r"alguien|otros|queda|quedan|alguem|outros|ficou|ficaram)\b",
        value,
    ):
        return "unknown"
    value = re.sub(
        r"^(?:hello|hi|hey|good morning|good afternoon|good evening|hola|buenos dias|buenas tardes|"
        r"buenas noches|bom dia|boa tarde|boa noite)(?:[!,.]\s*|\s+)",
        "",
        value,
        count=1,
    )
    matched = re.match(
        r"(?:(?:we|i) (?:have )?(?:(?:already|just|now) ){0,2}(?:checked out|departed)"
        r"|(?:we've|i've) (?:(?:already|just|now) ){0,2}(?:checked out|departed))"
        r"(?: early| now| already)?(?=[.!;,]|$| and (?:left|departed)\b)"
        r"|(?:we|i) (?:have )?(?:(?:already|just|now) ){0,2}left"
        r"(?: the (?:house|unit|apartment|property))?(?: early| now)?(?=[.!;,]|$)"
        r"|(?:we've|i've) (?:(?:already|just|now) ){0,2}left"
        r"(?: the (?:house|unit|apartment|property))?(?: early| now)?(?=[.!;,]|$)"
        r"|(?:ya )?(?:salimos|sali|nos fuimos|me fui)(?: del (?:alojamiento|apartamento))?(?=[.!;,]|$)"
        r"|(?:ya )?(?:hemos|he) (?:salido|dejado el alojamiento)(?=[.!;,]|$)"
        r"|(?:ja )?(?:saimos|sai|fomos embora|fui embora)(?: do (?:apartamento|imovel))?(?=[.!;,]|$)"
        r"|(?:nos )?(?:ja )?(?:fizemos|fiz) (?:o )?check[ -]?out(?=[.!;,]|$)"
        r"|(?:ya )?(?:hicimos|hice) (?:el )?check[ -]?out(?=[.!;,]|$)",
        value,
    )
    if matched is not None and re.fullmatch(
        r"(?: and (?:left|departed))?[.!;, ]*"
        r"(?:(?:thanks|thank you|thank you for everything|thanks for a great stay|have a great day|"
        r"gracias|muchas gracias|obrigado|obrigada|muito obrigado|muito obrigada)[.!;, ]*)?",
        value[matched.end() :],
    ):
        return "departed"
    return "unknown"

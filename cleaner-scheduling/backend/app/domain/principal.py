from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: tuple[str, ...]

    def has_role(self, role: str) -> bool:
        return role in self.roles

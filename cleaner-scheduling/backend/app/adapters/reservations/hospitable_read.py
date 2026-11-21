from __future__ import annotations

import math
import os
import stat
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import httpx

from app.adapters.errors import AdapterError, ProviderUnavailable
from app.adapters.types import NormalizedReservation


@dataclass(frozen=True)
class SourceMessage:
    external_id: str
    body: str
    sent_at: datetime


@dataclass(frozen=True)
class SourceProperty:
    external_id: str
    name: str
    bedrooms: int
    coordinates: tuple[float, float] | None = None
    display_address: str | None = None
    unit: str | None = None


class HospitableReadClient:
    def __init__(self, token_file: str, *, access_token: str | None = None) -> None:
        if access_token is None:
            path = Path(token_file).expanduser()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise AdapterError("Hospitable token must be an owner-only regular file.")
                token = stream.read(16385).strip()
        else:
            token = access_token
        if not token or len(token) > 16384 or not token.isascii() or any(char.isspace() for char in token):
            raise AdapterError("Hospitable token file is invalid.")
        self._client = httpx.Client(
            base_url="https://public.api.hospitable.com/v2/",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            trust_env=False,
        )
        self._deadline = time.monotonic() + 600
        self._message_reads: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: list[tuple[str, str]]) -> dict[str, object]:
        if path not in {"properties", "reservations"}:
            if not path.startswith("reservations/"):
                raise AdapterError("Unsupported read endpoint.")
            identity = path.removeprefix("reservations/").removesuffix("/messages")
            if path not in {f"reservations/{uuid.UUID(identity)}", f"reservations/{uuid.UUID(identity)}/messages"}:
                raise AdapterError("Unsupported read endpoint.")
        for attempt in range(3):
            if time.monotonic() >= self._deadline:
                raise ProviderUnavailable("Hospitable read exceeded its time budget.")
            if path.endswith("/messages"):
                delay = self._message_reads.get(path, 0) + 31 - time.monotonic()
                if delay > 0:
                    if time.monotonic() + delay >= self._deadline:
                        raise ProviderUnavailable("Hospitable messages exceeded their time budget.")
                    time.sleep(delay)
                self._message_reads[path] = time.monotonic()
            try:
                with self._client.stream("GET", path, params=tuple(params)) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt == 2:
                            raise ProviderUnavailable("Hospitable is temporarily unavailable.")
                        retry = response.headers.get("Retry-After", "")
                        if retry.isdigit() and int(retry) > 10:
                            raise ProviderUnavailable("Hospitable requested a longer retry delay.")
                        delay = max(2**attempt, int(retry) if retry.isdigit() else 0)
                    elif response.status_code != 200:
                        raise AdapterError(f"Hospitable read failed with HTTP {response.status_code}.")
                    else:
                        content = bytearray()
                        for chunk in response.iter_bytes():
                            content.extend(chunk)
                            if len(content) > 8_000_000 or time.monotonic() >= self._deadline:
                                raise AdapterError("Hospitable response exceeded the read limit.")
                        import json

                        try:
                            result = json.loads(content)
                        except ValueError:
                            raise AdapterError("Hospitable returned invalid JSON.") from None
                        if not isinstance(result, dict):
                            raise AdapterError("Hospitable response is not an object.")
                        return cast(dict[str, object], result)
            except httpx.TransportError:
                if attempt == 2:
                    raise ProviderUnavailable("Hospitable connection failed.") from None
                delay = 2**attempt
            time.sleep(delay)
        raise AssertionError("unreachable")

    def guest_messages(self, reservation_id: str) -> list[SourceMessage]:
        identity = str(uuid.UUID(reservation_id))
        response = self._get(f"reservations/{identity}/messages", [])
        rows = response.get("data")
        if not isinstance(rows, list) or len(rows) > 10000:
            raise AdapterError("Hospitable messages exceeded the read limit or have an invalid schema.")
        messages: list[SourceMessage] = []
        identities: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("sender_type") not in ("guest", "host"):
                raise AdapterError("Hospitable message sender is invalid.")
            if row["sender_type"] != "guest":
                continue
            if row.get("reservation_id") != identity or row.get("source") != "platform":
                continue
            if row.get("content_type") != "text/plain":
                continue
            external_id, body, created_at = row.get("platform_id"), row.get("body"), row.get("created_at")
            attachments = row.get("attachments")
            if (
                body in (None, "")
                and isinstance(attachments, list)
                and attachments
                and all(isinstance(item, dict) and item.get("type") in ("image", "video") for item in attachments)
            ):
                continue
            if type(external_id) is int and external_id >= 0:
                external_id = str(external_id)
            if not isinstance(external_id, str) or not 1 <= len(external_id) <= 256:
                raise AdapterError("Hospitable guest message identity is invalid.")
            if body is None:
                raise AdapterError("Hospitable guest message text is missing.")
            if not isinstance(body, str):
                raise AdapterError("Hospitable guest message text format is invalid.")
            if len(body) > 100000:
                raise AdapterError("Hospitable guest message text exceeded the read limit.")
            if not isinstance(created_at, str):
                raise AdapterError("Hospitable guest message timestamp is invalid.")
            try:
                sent_at = datetime.fromisoformat(created_at)
            except ValueError:
                raise AdapterError("Hospitable guest message timestamp is invalid.") from None
            if sent_at.utcoffset() is None:
                raise AdapterError("Hospitable guest message timestamp is ambiguous.")
            if external_id in identities:
                raise AdapterError("Hospitable returned duplicate guest messages.")
            identities.add(external_id)
            messages.append(SourceMessage(external_id, body, sent_at.astimezone(UTC)))
        return messages

    def _pages(self, path: str, params: list[tuple[str, str]]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        identities: set[str] = set()
        expected_total: int | None = None
        for page in range(1, 101):
            body = self._get(path, [*params, ("page", str(page)), ("per_page", "100")])
            data, meta = body.get("data"), body.get("meta")
            if not isinstance(data, list) or not isinstance(meta, dict):
                raise AdapterError("Hospitable pagination is incomplete.")
            last_page = meta.get("last_page")
            if type(last_page) is not int or not 1 <= last_page <= 100 or meta.get("current_page") != page:
                raise AdapterError("Hospitable pagination is invalid.")
            total = meta.get("total")
            if type(total) is not int or not 0 <= total <= 10000:
                raise AdapterError("Hospitable pagination total is invalid.")
            if expected_total is not None and total != expected_total:
                raise AdapterError("Hospitable changed during pagination. Retry the sync.")
            expected_total = total
            for item in data:
                if not isinstance(item, dict):
                    raise AdapterError("Hospitable returned an invalid row.")
                identity = str(uuid.UUID(str(item.get("id", ""))))
                if identity in identities:
                    raise AdapterError("Hospitable returned duplicate paginated identities.")
                identities.add(identity)
                rows.append(cast(dict[str, object], item))
            if page == last_page:
                if len(rows) != total:
                    raise AdapterError("Hospitable pagination did not retrieve every row.")
                return rows
        raise AdapterError("Hospitable pagination exceeded the read limit.")

    def snapshot(
        self, business_date: date, tracked_ids: list[str]
    ) -> tuple[list[SourceProperty], list[NormalizedReservation], list[tuple[str, str]], set[str]]:
        observed = datetime.now(UTC)
        houses: list[SourceProperty] = []
        rejected: list[tuple[str, str]] = []
        unbooked: set[str] = set()
        tracked = frozenset(tracked_ids)
        for raw in self._pages("properties", []):
            identity = str(uuid.UUID(str(raw.get("id", ""))))
            name, capacity = raw.get("name"), raw.get("capacity")
            bedrooms = capacity.get("bedrooms") if isinstance(capacity, dict) else None
            if (
                not isinstance(name, str)
                or not 1 <= len(name.strip()) <= 120
                or type(bedrooms) is not int
                or not 0 <= bedrooms <= 32767
                or raw.get("timezone")
                not in {"America/New_York", observed.astimezone(ZoneInfo("America/New_York")).strftime("%z")}
                or raw.get("parent_child") is not None
            ):
                rejected.append(("HOSPITABLE_PROPERTY_REVIEW", identity))
                continue
            address = raw.get("address")
            point = address.get("coordinates") if isinstance(address, dict) else None
            coordinates = None
            if isinstance(point, dict):
                try:
                    latitude, longitude = float(str(point.get("latitude"))), float(str(point.get("longitude")))
                    if (
                        math.isfinite(latitude)
                        and math.isfinite(longitude)
                        and -90 < latitude < 90
                        and -180 < longitude < 180
                        and (latitude, longitude) != (0, 0)
                    ):
                        coordinates = (longitude, latitude)
                except (ValueError, TypeError):
                    pass
            display = address.get("display") if isinstance(address, dict) else None
            unit = address.get("number") if isinstance(address, dict) else None
            display = " ".join(display.split()) if isinstance(display, str) and 0 < len(display) <= 400 else None
            unit = " ".join(unit.split()) if isinstance(unit, str) and 0 < len(unit) <= 80 else None
            houses.append(SourceProperty(identity, name.strip(), bedrooms, coordinates, display or None, unit or None))
        records: dict[str, NormalizedReservation] = {}
        seen: set[str] = set()
        start = (business_date - timedelta(days=2)).isoformat()
        end = (business_date + timedelta(days=93)).isoformat()
        house_ids = {house.external_id for house in houses}
        for start_index in range(0, len(houses), 10):
            for query in ("checkin", "checkout"):
                params = [
                    *(("properties[]", house.external_id) for house in houses[start_index : start_index + 10]),
                    ("start_date", start),
                    ("end_date", end),
                    ("date_query", query),
                    ("include", "properties"),
                ]
                for raw in self._pages("reservations", params):
                    identity = str(uuid.UUID(str(raw.get("id", ""))))
                    seen.add(identity)
                    record = self._normalize(raw, house_ids, observed, rejected, tracked, unbooked)
                    if (record is not None and identity in unbooked) or (identity in unbooked and identity in records):
                        raise AdapterError("A reservation changed during pagination. Retry the sync.")
                    if record is not None:
                        previous = records.get(identity)
                        if previous is not None and previous != record:
                            raise AdapterError("A reservation changed during pagination. Retry the sync.")
                        records[identity] = record
        for identity in tracked_ids:
            if identity in seen:
                continue
            body = self._get(f"reservations/{uuid.UUID(identity)}", [("include", "properties")])
            detail = body.get("data")
            if not isinstance(detail, dict):
                raise AdapterError("A tracked reservation could not be reconciled.")
            record = self._normalize(detail, house_ids, observed, rejected, tracked, unbooked)
            if record is not None:
                records[identity] = record
        return houses, list(records.values()), rejected, unbooked

    def _normalize(
        self,
        raw: dict[str, object],
        house_ids: set[str],
        observed: datetime,
        rejected: list[tuple[str, str]],
        tracked: frozenset[str],
        unbooked: set[str],
    ) -> NormalizedReservation | None:
        identity = str(uuid.UUID(str(raw.get("id", ""))))
        try:
            state = raw["reservation_status"]
            if not isinstance(state, dict) or not isinstance(state.get("current"), dict):
                raise ValueError
            category = state["current"].get("category")
            if category == "not accepted" and identity not in tracked:
                unbooked.add(identity)
                return None
            if category not in {"accepted", "cancelled"}:
                raise ValueError
            properties = raw.get("properties")
            if not isinstance(properties, list) or len(properties) != 1 or not isinstance(properties[0], dict):
                raise ValueError
            house = str(uuid.UUID(str(properties[0].get("id", ""))))
            if house not in house_ids:
                raise ValueError
            arrival, departure = raw.get("check_in"), raw.get("check_out")
            if (
                not isinstance(arrival, str)
                or not isinstance(departure, str)
                or "T" not in arrival
                or "T" not in departure
            ):
                raise ValueError
            checkin, checkout = datetime.fromisoformat(arrival), datetime.fromisoformat(departure)
            if checkin.utcoffset() is None or checkout.utcoffset() is None or checkout <= checkin:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            rejected.append(("HOSPITABLE_RESERVATION_REVIEW", identity))
            return None
        return NormalizedReservation(
            provider="hospitable",
            external_id=identity,
            property_external_id=house,
            status="active" if category == "accepted" else "canceled",
            checkin_at=checkin.astimezone(UTC),
            checkout_at=checkout.astimezone(UTC),
            source_updated_at=None,
            source_revision=None,
            timestamp_provenance="hospitable:check_in/check_out:authoritative-read",
            observed_at=observed,
        )

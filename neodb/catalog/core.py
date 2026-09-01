"""Typed Product-side client for the Vinyl Catalog Core HTTP API.

Core remains the authority for shared catalog facts; these dataclasses are an
immutable Product representation of one HTTP response, not persisted state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

import httpx
from django.conf import settings


class CoreErrorKind(StrEnum):
    NOT_FOUND = "not_found"
    CONNECT_TIMEOUT = "connect_timeout"
    READ_TIMEOUT = "read_timeout"
    CONNECTION = "connection"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    CONFIGURATION = "configuration"


class CoreClientError(RuntimeError):
    """A deterministic failure while consuming the supported Core API."""

    def __init__(
        self,
        kind: CoreErrorKind,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


class CoreNotFoundError(CoreClientError):
    """Core explicitly reported that the requested release does not exist."""

    def __init__(self, message: str = "Core release was not found") -> None:
        super().__init__(CoreErrorKind.NOT_FOUND, message, status_code=404)


class CoreDegradedError(CoreClientError):
    """Core could not provide a usable response for this read."""


@dataclass(frozen=True, slots=True)
class CatalogRef:
    """Immutable stable identity crossing the Product/Core boundary."""

    namespace: str
    entity_type: str
    source_id: int

    def __post_init__(self) -> None:
        if self.namespace != "discogs":
            raise ValueError("Unsupported CatalogRef namespace")
        if self.entity_type not in {"release", "artist", "label", "master"}:
            raise ValueError("Unsupported CatalogRef entity type")
        if isinstance(self.source_id, bool) or self.source_id < 1:
            raise ValueError("CatalogRef source_id must be positive")

    def __str__(self) -> str:
        return f"{self.namespace}:{self.entity_type}:{self.source_id}"

    @classmethod
    def parse(cls, value: str) -> CatalogRef:
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError(
                "CatalogRef must have namespace:entity_type:source_id form"
            )
        try:
            source_id = int(parts[2])
        except (TypeError, ValueError) as error:
            raise ValueError("CatalogRef source_id must be an integer") from error
        return cls(parts[0], parts[1], source_id)

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> CatalogRef:
        ref = value.get("ref")
        namespace = value.get("namespace")
        entity_type = value.get("entity_type")
        source_id = value.get("source_id")
        if (
            not isinstance(ref, str)
            or not isinstance(namespace, str)
            or not isinstance(entity_type, str)
        ):
            raise TypeError("Core CatalogRef fields are invalid")
        if isinstance(source_id, bool) or not isinstance(source_id, int):
            raise TypeError("Core CatalogRef source_id is invalid")
        parsed = cls(namespace, entity_type, source_id)
        if str(parsed) != ref:
            raise ValueError("Core CatalogRef fields do not agree")
        return parsed


@dataclass(frozen=True, slots=True)
class CatalogArtistCredit:
    ref: CatalogRef | None
    display_name: str | None
    anv: str | None
    join_text: str | None


@dataclass(frozen=True, slots=True)
class CatalogFormat:
    name: str
    quantity_text: str | None
    text: str | None
    descriptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogIdentifier:
    type: str | None
    value: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class CatalogProviderOccurrence:
    kind: str
    ref: CatalogRef | None
    display_name: str | None
    catalog_number: str | None
    entity_type: str | None
    entity_type_name: str | None


@dataclass(frozen=True, slots=True)
class CatalogExtraCredit:
    ref: CatalogRef | None
    display_name: str | None
    anv: str | None
    role: str | None
    track_selector: str | None


@dataclass(frozen=True, slots=True)
class CatalogTrack:
    path: tuple[int, ...]
    position: str | None
    title: str | None
    duration: str | None
    type_text: str | None
    artists: tuple[CatalogArtistCredit, ...]
    extra_credits: tuple[CatalogExtraCredit, ...]


@dataclass(frozen=True, slots=True)
class CatalogArtworkStrategy:
    identity: str
    revision: str
    priority: int
    label: str
    applicability: str
    evidence_rule: str
    provenance: str
    generator_identity: str | None
    generator_revision: str | None
    generator_configuration: str | None


@dataclass(frozen=True, slots=True)
class CatalogArtwork:
    association: str | None
    status: str
    strategy: CatalogArtworkStrategy | None
    provider: str | None
    content_url: str | None
    master_ref: CatalogRef | None


@dataclass(frozen=True, slots=True)
class CatalogVideo:
    source_url: str | None
    duration_text: str | None
    embed_text: str | None
    title: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class CatalogReleaseSearchResult:
    ref: CatalogRef
    title: str
    artists: tuple[CatalogArtistCredit, ...]
    released: str | None
    country: str | None


@dataclass(frozen=True, slots=True)
class CatalogReleaseDetail:
    ref: CatalogRef
    title: str
    country: str | None
    released: str | None
    data_quality: str
    master_ref: CatalogRef | None
    is_main_release_text: str | None
    genres: tuple[str, ...]
    styles: tuple[str, ...]
    artists: tuple[CatalogArtistCredit, ...]
    formats: tuple[CatalogFormat, ...]
    identifiers: tuple[CatalogIdentifier, ...]
    provider_occurrences: tuple[CatalogProviderOccurrence, ...]
    tracks: tuple[CatalogTrack, ...]
    notes: str | None
    extra_credits: tuple[CatalogExtraCredit, ...]
    videos: tuple[CatalogVideo, ...]
    artwork: CatalogArtwork


class CoreClient:
    """Read Releases from Core through its supported HTTP interface."""

    MAX_SEARCH_LIMIT = 100

    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not base_url.strip():
            raise CoreClientError(
                CoreErrorKind.CONFIGURATION,
                "VINYL_CATALOG_CORE_URL must be configured",
            )
        parsed_url = httpx.URL(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise CoreClientError(
                CoreErrorKind.CONFIGURATION,
                "VINYL_CATALOG_CORE_URL must be an HTTP(S) URL",
            )
        if connect_timeout <= 0 or read_timeout <= 0:
            raise CoreClientError(
                CoreErrorKind.CONFIGURATION,
                "Core timeouts must be positive",
            )
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=connect_timeout,
            ),
            follow_redirects=False,
        )

    @classmethod
    def from_settings(cls) -> CoreClient:
        return cls(
            settings.VINYL_CATALOG_CORE_URL,
            connect_timeout=settings.VINYL_CATALOG_CORE_CONNECT_TIMEOUT,
            read_timeout=settings.VINYL_CATALOG_CORE_READ_TIMEOUT,
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search_releases(
        self, query: str, *, limit: int = 20
    ) -> tuple[CatalogReleaseSearchResult, ...]:
        payload = self._get_json(
            "/api/search/releases/artist-title",
            params={"q": query, "limit": self._validate_limit(limit)},
        )
        try:
            return tuple(
                _map_search_result(item) for item in _required_list(payload, "results")
            )
        except (TypeError, ValueError) as error:
            raise CoreDegradedError(
                CoreErrorKind.INVALID_RESPONSE,
                "Core returned an invalid search response",
            ) from error

    def get_release(self, ref: CatalogRef) -> CatalogReleaseDetail:
        if ref.entity_type != "release":
            raise ValueError("Release detail requires a release CatalogRef")
        payload = self._get_json(f"/api/releases/{ref.source_id}")
        try:
            detail = _map_release_detail(payload)
            if detail.ref != ref:
                raise ValueError("Core release ref does not match the requested ref")
            return detail
        except (TypeError, ValueError) as error:
            raise CoreDegradedError(
                CoreErrorKind.INVALID_RESPONSE,
                "Core returned an invalid release response",
            ) from error

    def _validate_limit(self, limit: int) -> int:
        if isinstance(limit, bool) or not 1 <= limit <= self.MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {self.MAX_SEARCH_LIMIT}")
        return limit

    def _get_json(
        self, path: str, *, params: Mapping[str, str | int] | None = None
    ) -> Mapping[str, Any]:
        try:
            response = self._http_client.get(path, params=params)
        except httpx.ConnectTimeout as error:
            raise CoreDegradedError(
                CoreErrorKind.CONNECT_TIMEOUT, "Core connection timed out"
            ) from error
        except httpx.ReadTimeout as error:
            raise CoreDegradedError(
                CoreErrorKind.READ_TIMEOUT, "Core response timed out"
            ) from error
        except httpx.ConnectError as error:
            raise CoreDegradedError(
                CoreErrorKind.CONNECTION, "Core connection failed"
            ) from error
        except httpx.RequestError as error:
            raise CoreDegradedError(
                CoreErrorKind.CONNECTION, "Core request failed"
            ) from error

        if response.status_code == 404:
            raise CoreNotFoundError()
        if response.status_code >= 500:
            raise CoreDegradedError(
                CoreErrorKind.UNAVAILABLE,
                f"Core returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise CoreDegradedError(
                CoreErrorKind.INVALID_RESPONSE,
                f"Core returned unexpected HTTP {response.status_code}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise CoreDegradedError(
                CoreErrorKind.INVALID_RESPONSE, "Core returned invalid JSON"
            ) from error
        if not isinstance(payload, Mapping):
            raise CoreDegradedError(
                CoreErrorKind.INVALID_RESPONSE,
                "Core returned a non-object JSON response",
            )
        return payload


def _required_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"Core field {field!r} must be an object")
    return value


def _required_list(value: Mapping[str, Any], field: str) -> list[Any]:
    result = value.get(field)
    if not isinstance(result, list):
        raise TypeError(f"Core field {field!r} must be a list")
    return result


def _required_str(value: Mapping[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise TypeError(f"Core field {field!r} must be a string")
    return result


def _optional_str(value: Mapping[str, Any], field: str) -> str | None:
    result = value.get(field)
    if result is not None and not isinstance(result, str):
        raise ValueError(f"Core field {field!r} must be a string or null")
    return result


def _optional_ref(value: Any) -> CatalogRef | None:
    if value is None:
        return None
    return CatalogRef.from_payload(_required_mapping(value, "ref"))


def _map_artist(value: Any) -> CatalogArtistCredit:
    item = _required_mapping(value, "artist")
    return CatalogArtistCredit(
        ref=_optional_ref(item.get("ref")),
        display_name=_optional_str(item, "display_name"),
        anv=_optional_str(item, "anv"),
        join_text=_optional_str(item, "join_text"),
    )


def _map_search_result(value: Any) -> CatalogReleaseSearchResult:
    item = _required_mapping(value, "result")
    ref = CatalogRef.from_payload(_required_mapping(item.get("ref"), "ref"))
    if ref.entity_type != "release":
        raise ValueError("Core search result ref is not a release")
    return CatalogReleaseSearchResult(
        ref=ref,
        title=_required_str(item, "title"),
        artists=tuple(
            _map_artist(artist) for artist in _required_list(item, "artists")
        ),
        released=_optional_str(item, "released"),
        country=_optional_str(item, "country"),
    )


def _map_format(value: Any) -> CatalogFormat:
    item = _required_mapping(value, "format")
    descriptions = _required_list(item, "descriptions")
    if not all(isinstance(description, str) for description in descriptions):
        raise ValueError("Core format descriptions must be strings")
    return CatalogFormat(
        name=_required_str(item, "name"),
        quantity_text=_optional_str(item, "quantity_text"),
        text=_optional_str(item, "text"),
        descriptions=tuple(descriptions),
    )


def _map_identifier(value: Any) -> CatalogIdentifier:
    item = _required_mapping(value, "identifier")
    return CatalogIdentifier(
        type=_optional_str(item, "type"),
        value=_optional_str(item, "value"),
        description=_optional_str(item, "description"),
    )


def _map_provider_occurrence(value: Any) -> CatalogProviderOccurrence:
    item = _required_mapping(value, "provider_occurrence")
    return CatalogProviderOccurrence(
        kind=_required_str(item, "kind"),
        ref=_optional_ref(item.get("ref")),
        display_name=_optional_str(item, "display_name"),
        catalog_number=_optional_str(item, "catalog_number"),
        entity_type=_optional_str(item, "entity_type"),
        entity_type_name=_optional_str(item, "entity_type_name"),
    )


def _map_track(value: Any) -> CatalogTrack:
    item = _required_mapping(value, "track")
    path = item.get("path")
    if not isinstance(path, list) or not all(
        isinstance(part, int) and not isinstance(part, bool) for part in path
    ):
        raise ValueError("Core track path must be a list of integers")
    extra_credits = []
    for credit in _required_list(item, "extra_credits"):
        credit_item = _required_mapping(credit, "extra_credit")
        extra_credits.append(
            CatalogExtraCredit(
                ref=_optional_ref(credit_item.get("ref")),
                display_name=_optional_str(credit_item, "display_name"),
                anv=_optional_str(credit_item, "anv"),
                role=_optional_str(credit_item, "role"),
                track_selector=_optional_str(credit_item, "track_selector"),
            )
        )
    return CatalogTrack(
        path=tuple(path),
        position=_optional_str(item, "position"),
        title=_optional_str(item, "title"),
        duration=_optional_str(item, "duration"),
        type_text=_optional_str(item, "type_text"),
        artists=tuple(
            _map_artist(artist) for artist in _required_list(item, "artists")
        ),
        extra_credits=tuple(extra_credits),
    )


def _map_strategy(value: Any) -> CatalogArtworkStrategy:
    item = _required_mapping(value, "artwork strategy")
    priority = item.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise TypeError("Core artwork strategy priority must be an integer")
    return CatalogArtworkStrategy(
        identity=_required_str(item, "identity"),
        revision=_required_str(item, "revision"),
        priority=priority,
        label=_required_str(item, "label"),
        applicability=_required_str(item, "applicability"),
        evidence_rule=_required_str(item, "evidence_rule"),
        provenance=_required_str(item, "provenance"),
        generator_identity=_optional_str(item, "generator_identity"),
        generator_revision=_optional_str(item, "generator_revision"),
        generator_configuration=_optional_str(item, "generator_configuration"),
    )


def _map_artwork(value: Any) -> CatalogArtwork:
    item = _required_mapping(value, "artwork")
    display = _required_mapping(item.get("display"), "artwork.display")
    strategy = display.get("strategy")
    return CatalogArtwork(
        association=_optional_str(item, "association"),
        status=_required_str(display, "status"),
        strategy=_map_strategy(strategy) if strategy is not None else None,
        provider=_optional_str(display, "provider"),
        content_url=_optional_str(display, "content_url"),
        master_ref=_optional_ref(display.get("master_ref")),
    )


def _map_extra_credit(value: Any) -> CatalogExtraCredit:
    item = _required_mapping(value, "extra_credit")
    return CatalogExtraCredit(
        ref=_optional_ref(item.get("ref")),
        display_name=_optional_str(item, "display_name"),
        anv=_optional_str(item, "anv"),
        role=_optional_str(item, "role"),
        track_selector=_optional_str(item, "track_selector"),
    )


def _map_video(value: Any) -> CatalogVideo:
    item = _required_mapping(value, "video")
    return CatalogVideo(
        source_url=_optional_str(item, "source_url"),
        duration_text=_optional_str(item, "duration_text"),
        embed_text=_optional_str(item, "embed_text"),
        title=_optional_str(item, "title"),
        description=_optional_str(item, "description"),
    )


def _map_release_detail(value: Mapping[str, Any]) -> CatalogReleaseDetail:
    return CatalogReleaseDetail(
        ref=CatalogRef.from_payload(_required_mapping(value.get("ref"), "ref")),
        title=_required_str(value, "title"),
        country=_optional_str(value, "country"),
        released=_optional_str(value, "released"),
        data_quality=_required_str(value, "data_quality"),
        master_ref=_optional_ref(value.get("master_ref")),
        is_main_release_text=_optional_str(value, "is_main_release_text"),
        genres=tuple(_required_string_list(value, "genres")),
        styles=tuple(_required_string_list(value, "styles")),
        artists=tuple(
            _map_artist(artist) for artist in _required_list(value, "artists")
        ),
        formats=tuple(_map_format(item) for item in _required_list(value, "formats")),
        identifiers=tuple(
            _map_identifier(item) for item in _required_list(value, "identifiers")
        ),
        provider_occurrences=tuple(
            _map_provider_occurrence(item)
            for item in _required_list(value, "provider_occurrences")
        ),
        tracks=tuple(_map_track(item) for item in _required_list(value, "tracks")),
        notes=_optional_str(value, "notes"),
        extra_credits=tuple(
            _map_extra_credit(item) for item in _required_list(value, "extra_credits")
        ),
        videos=tuple(_map_video(item) for item in _required_list(value, "videos")),
        artwork=_map_artwork(value.get("artwork")),
    )


def _required_string_list(value: Mapping[str, Any], field: str) -> list[str]:
    result = _required_list(value, field)
    if not all(isinstance(item, str) for item in result):
        raise ValueError(f"Core field {field!r} must contain strings")
    return result

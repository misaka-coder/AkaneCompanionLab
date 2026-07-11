from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .normalizers import normalize_market_code
from .types import MarketDataValidationError


PUBLIC_INSTRUMENT_ROUTES = frozenset({"yahoo", "akshare_etf"})
PUBLIC_INSTRUMENT_TYPES = frozenset({"index", "etf"})
PUBLIC_QUOTE_DELAY_KINDS = frozenset({"delayed", "end_of_day", "unknown"})
PUBLIC_INSTRUMENT_REGISTRY_AS_OF = 1_783_699_200

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MARKET_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,19}$")


@dataclass(frozen=True)
class PublicInstrument:
    canonical_code: str
    display_name: str
    instrument_type: str
    market: str
    exchange_timezone: str
    currency: str
    route: str
    vendor_symbol: str
    quote_delay_kind: str
    aliases: tuple[str, ...] = ()
    tracking_target: str = ""
    active: bool = True

    def __post_init__(self) -> None:
        canonical_code = normalize_market_code(
            self.canonical_code,
            field="canonical_code",
            provider="public_market",
        )
        display_name = _bounded_required_text(self.display_name, field="display_name", max_length=200)
        instrument_type = str(self.instrument_type or "").strip().lower()
        if instrument_type not in PUBLIC_INSTRUMENT_TYPES:
            raise _invalid_field("instrument_type", "unsupported public instrument type")
        market = str(self.market or "").strip().upper()
        if not _MARKET_RE.fullmatch(market):
            raise _invalid_field("market", "market must be a stable uppercase market code")
        exchange_timezone = str(self.exchange_timezone or "").strip()
        try:
            ZoneInfo(exchange_timezone)
        except ZoneInfoNotFoundError as exc:
            raise _invalid_field("exchange_timezone", "unknown exchange timezone") from exc
        currency = str(self.currency or "").strip().upper()
        if not _CURRENCY_RE.fullmatch(currency):
            raise _invalid_field("currency", "currency must be a three-letter ISO code")
        route = str(self.route or "").strip().lower()
        if route not in PUBLIC_INSTRUMENT_ROUTES:
            raise _invalid_field("route", "unsupported public market route")
        vendor_symbol = _bounded_required_text(self.vendor_symbol, field="vendor_symbol", max_length=80)
        quote_delay_kind = str(self.quote_delay_kind or "").strip().lower()
        if quote_delay_kind not in PUBLIC_QUOTE_DELAY_KINDS:
            raise _invalid_field("quote_delay_kind", "unsupported quote delay kind")
        aliases = _unique_aliases(self.aliases)
        tracking_target = str(self.tracking_target or "").strip()
        if tracking_target:
            tracking_target = normalize_market_code(
                tracking_target,
                field="tracking_target",
                provider="public_market",
            )

        object.__setattr__(self, "canonical_code", canonical_code)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "instrument_type", instrument_type)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "exchange_timezone", exchange_timezone)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "route", route)
        object.__setattr__(self, "vendor_symbol", vendor_symbol)
        object.__setattr__(self, "quote_delay_kind", quote_delay_kind)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "tracking_target", tracking_target)
        object.__setattr__(self, "active", bool(self.active))

    def to_public_dict(self) -> dict[str, object]:
        """Return model/UI-safe identity metadata without upstream vendor symbols."""

        return {
            "code": self.canonical_code,
            "display_name": self.display_name,
            "instrument_type": self.instrument_type,
            "market": self.market,
            "exchange_timezone": self.exchange_timezone,
            "currency": self.currency,
            "quote_delay_kind": self.quote_delay_kind,
            "aliases": list(self.aliases),
            "tracking_target": self.tracking_target,
            "active": bool(self.active),
        }


@dataclass(frozen=True)
class PublicInstrumentResolution:
    query: str
    status: str
    candidates: tuple[PublicInstrument, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "resolved" and len(self.candidates) == 1

    @property
    def instrument(self) -> PublicInstrument | None:
        return self.candidates[0] if self.ok else None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "status": self.status,
            "query": self.query,
            "candidates": [candidate.to_public_dict() for candidate in self.candidates],
        }


class PublicInstrumentRegistry:
    """Immutable canonical-code and exact-alias registry for public market adapters."""

    def __init__(self, instruments: tuple[PublicInstrument, ...] | list[PublicInstrument]) -> None:
        records = tuple(instruments or ())
        if not records:
            raise ValueError("at least one public instrument is required")
        by_code: dict[str, PublicInstrument] = {}
        vendor_keys: set[tuple[str, str]] = set()
        aliases: dict[str, list[str]] = {}
        for instrument in records:
            if not isinstance(instrument, PublicInstrument):
                raise TypeError("public instrument registry only accepts PublicInstrument values")
            if instrument.canonical_code in by_code:
                raise ValueError(f"duplicate public instrument code: {instrument.canonical_code}")
            vendor_key = (instrument.route, instrument.vendor_symbol.casefold())
            if vendor_key in vendor_keys:
                raise ValueError(
                    f"duplicate public vendor symbol for route {instrument.route}: {instrument.vendor_symbol}"
                )
            vendor_keys.add(vendor_key)
            by_code[instrument.canonical_code] = instrument
            for alias in (instrument.canonical_code, instrument.display_name, *instrument.aliases):
                normalized = normalize_public_instrument_alias(alias)
                if normalized:
                    aliases.setdefault(normalized, []).append(instrument.canonical_code)

        for instrument in records:
            if not instrument.tracking_target:
                continue
            target = by_code.get(instrument.tracking_target)
            if target is None:
                raise ValueError(
                    f"unknown tracking target for {instrument.canonical_code}: {instrument.tracking_target}"
                )
            if target.instrument_type != "index":
                raise ValueError(
                    f"tracking target must be an index for {instrument.canonical_code}: {instrument.tracking_target}"
                )

        self._records = tuple(sorted(records, key=lambda item: item.canonical_code))
        self._by_code = dict(by_code)
        self._aliases = {key: tuple(dict.fromkeys(codes)) for key, codes in aliases.items()}

    def all(self, *, active_only: bool = True) -> tuple[PublicInstrument, ...]:
        if not active_only:
            return self._records
        return tuple(instrument for instrument in self._records if instrument.active)

    def get(self, canonical_code: str, *, include_inactive: bool = False) -> PublicInstrument | None:
        try:
            code = normalize_market_code(
                canonical_code,
                field="code",
                provider="public_market",
                status="invalid_arguments",
                error_code="invalid_arguments",
            )
        except MarketDataValidationError:
            return None
        instrument = self._by_code.get(code)
        if instrument is None or (not include_inactive and not instrument.active):
            return None
        return instrument

    def require(self, canonical_code: str, *, include_inactive: bool = False) -> PublicInstrument:
        instrument = self.get(canonical_code, include_inactive=include_inactive)
        if instrument is None:
            raise MarketDataValidationError(
                field="code",
                reason=f"unknown public market instrument: {str(canonical_code or '').strip()}",
                code="unknown_instrument",
                status="invalid_arguments",
                provider="public_market",
            )
        return instrument

    def resolve(self, query: str, *, include_inactive: bool = False) -> PublicInstrumentResolution:
        clean_query = str(query or "").strip()
        if not clean_query:
            return PublicInstrumentResolution(query="", status="not_found")
        direct = self.get(clean_query, include_inactive=include_inactive)
        if direct is not None:
            return PublicInstrumentResolution(query=clean_query, status="resolved", candidates=(direct,))
        normalized = normalize_public_instrument_alias(clean_query)
        codes = self._aliases.get(normalized, ()) if normalized else ()
        candidates = tuple(
            self._by_code[code]
            for code in codes
            if include_inactive or self._by_code[code].active
        )
        status = "resolved" if len(candidates) == 1 else "ambiguous" if candidates else "not_found"
        return PublicInstrumentResolution(query=clean_query, status=status, candidates=candidates)

    def for_route(self, route: str, *, active_only: bool = True) -> tuple[PublicInstrument, ...]:
        clean_route = str(route or "").strip().lower()
        if clean_route not in PUBLIC_INSTRUMENT_ROUTES:
            return ()
        return tuple(instrument for instrument in self.all(active_only=active_only) if instrument.route == clean_route)


def normalize_public_instrument_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^\w\u3400-\u9fff]+", "", text)


def build_default_public_instrument_registry() -> PublicInstrumentRegistry:
    return PublicInstrumentRegistry(
        [
            PublicInstrument(
                canonical_code="NIKKEI225.INDEX",
                display_name="日经225指数",
                instrument_type="index",
                market="JP",
                exchange_timezone="Asia/Tokyo",
                currency="JPY",
                route="yahoo",
                vendor_symbol="^N225",
                quote_delay_kind="delayed",
                aliases=("日经225", "日经指数", "Nikkei 225", "Nikkei225"),
            ),
            PublicInstrument(
                canonical_code="SP500.INDEX",
                display_name="标普500指数",
                instrument_type="index",
                market="US",
                exchange_timezone="America/New_York",
                currency="USD",
                route="yahoo",
                vendor_symbol="^GSPC",
                quote_delay_kind="delayed",
                aliases=("标普500", "标准普尔500指数", "S&P 500", "S&P500"),
            ),
            PublicInstrument(
                canonical_code="NASDAQCOMPOSITE.INDEX",
                display_name="纳斯达克综合指数",
                instrument_type="index",
                market="US",
                exchange_timezone="America/New_York",
                currency="USD",
                route="yahoo",
                vendor_symbol="^IXIC",
                quote_delay_kind="delayed",
                aliases=("纳指综合", "纳斯达克综合", "Nasdaq Composite"),
            ),
            PublicInstrument(
                canonical_code="HSI.INDEX",
                display_name="恒生指数",
                instrument_type="index",
                market="HK",
                exchange_timezone="Asia/Hong_Kong",
                currency="HKD",
                route="yahoo",
                vendor_symbol="^HSI",
                quote_delay_kind="delayed",
                aliases=("恒指", "Hang Seng Index", "Hang Seng"),
            ),
            PublicInstrument(
                canonical_code="513000.SH",
                display_name="日经225ETF易方达",
                instrument_type="etf",
                market="SH",
                exchange_timezone="Asia/Shanghai",
                currency="CNY",
                route="akshare_etf",
                vendor_symbol="513000",
                quote_delay_kind="unknown",
                aliases=(
                    "513000",
                    "易方达奥明日经225ETF",
                    "易方达奥明日经225交易型开放式指数证券投资基金QDII",
                    "225ETF",
                ),
                tracking_target="NIKKEI225.INDEX",
            ),
            PublicInstrument(
                canonical_code="513520.SH",
                display_name="日经ETF华夏",
                instrument_type="etf",
                market="SH",
                exchange_timezone="Asia/Shanghai",
                currency="CNY",
                route="akshare_etf",
                vendor_symbol="513520",
                quote_delay_kind="unknown",
                aliases=("513520", "华夏野村日经225ETF", "日经225ETF华夏"),
                tracking_target="NIKKEI225.INDEX",
            ),
        ]
    )


def _bounded_required_text(value: str, *, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise _invalid_field(field, "value is required")
    if len(text) > max_length:
        raise _invalid_field(field, f"value exceeds maximum length of {max_length}")
    return text


def _unique_aliases(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values or ())
        except TypeError as exc:
            raise _invalid_field("aliases", "aliases must be a list") from exc
    if len(raw_values) > 64:
        raise _invalid_field("aliases", "at most 64 aliases are allowed")
    aliases: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        alias = _bounded_required_text(value, field="aliases", max_length=200)
        normalized = normalize_public_instrument_alias(alias)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(alias)
    return tuple(aliases)


def _invalid_field(field: str, reason: str) -> MarketDataValidationError:
    return MarketDataValidationError(
        field=field,
        reason=reason,
        code="invalid_instrument",
        status="invalid_data",
        provider="public_market",
    )


__all__ = [
    "PUBLIC_INSTRUMENT_ROUTES",
    "PUBLIC_INSTRUMENT_REGISTRY_AS_OF",
    "PUBLIC_INSTRUMENT_TYPES",
    "PUBLIC_QUOTE_DELAY_KINDS",
    "PublicInstrument",
    "PublicInstrumentRegistry",
    "PublicInstrumentResolution",
    "build_default_public_instrument_registry",
    "normalize_public_instrument_alias",
]

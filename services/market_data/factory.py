from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .disabled import DisabledMarketDataProvider
from .emquant_bridge_client import EmQuantBridgeMarketDataProvider
from .provider import MarketDataProvider
from .public_akshare import AkShareETFAdapter
from .public_cache import TTLMarketDataCache
from .public_instruments import build_default_public_instrument_registry
from .public_provider import PublicMarketProvider
from .public_yahoo import YahooFinanceAdapter
from .types import MarketDataValidationError


MarketDataProviderBuilder = Callable[["MarketDataProviderSettings"], MarketDataProvider]


@dataclass(frozen=True)
class MarketDataProviderSettings:
    provider: str = "disabled"
    emquant_bridge_url: str = "http://127.0.0.1:9910"
    emquant_bridge_token: str = ""
    emquant_timeout_seconds: float = 15.0
    public_yahoo_enabled: bool = True
    public_akshare_enabled: bool = True
    public_timeout_seconds: float = 8.0
    public_cache_max_entries: int = 256
    public_yahoo_series_ttl_seconds: float = 900.0
    public_yahoo_quote_ttl_seconds: float = 60.0
    public_akshare_series_ttl_seconds: float = 300.0
    public_akshare_quote_ttl_seconds: float = 15.0
    public_failure_ttl_seconds: float = 15.0

    @property
    def provider_id(self) -> str:
        return str(self.provider or "disabled").strip().lower() or "disabled"


class MarketDataProviderRegistry:
    """Small provider factory registry; production Mock registration is intentionally absent."""

    def __init__(self) -> None:
        self._builders: dict[str, MarketDataProviderBuilder] = {}

    def register(self, provider_id: str, builder: MarketDataProviderBuilder) -> None:
        clean_id = _normalize_provider_id(provider_id)
        if clean_id in self._builders:
            raise ValueError(f"market data provider is already registered: {clean_id}")
        if not callable(builder):
            raise TypeError("market data provider builder must be callable")
        self._builders[clean_id] = builder

    def create(self, settings: MarketDataProviderSettings) -> MarketDataProvider:
        if not isinstance(settings, MarketDataProviderSettings):
            raise TypeError("MarketDataProviderSettings is required")
        provider_id = settings.provider_id
        builder = self._builders.get(provider_id)
        if builder is None:
            raise MarketDataValidationError(
                field="FINANCE_MARKET_PROVIDER",
                reason=f"unsupported market data provider: {provider_id}",
                code="unsupported_provider",
                status="invalid_arguments",
                provider=provider_id,
            )
        provider = builder(settings)
        if not isinstance(provider, MarketDataProvider):
            raise TypeError(f"market data provider builder returned an invalid adapter: {provider_id}")
        return provider

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))


def build_default_market_data_provider_registry() -> MarketDataProviderRegistry:
    registry = MarketDataProviderRegistry()
    registry.register(
        "disabled",
        lambda _settings: DisabledMarketDataProvider(
            reason="market data provider is disabled by FINANCE_MARKET_PROVIDER"
        ),
    )
    registry.register(
        "emquant",
        lambda settings: EmQuantBridgeMarketDataProvider(
            settings.emquant_bridge_url,
            access_token=settings.emquant_bridge_token,
            timeout_seconds=settings.emquant_timeout_seconds,
        ),
    )
    registry.register("public_market", _build_public_market_provider)
    return registry


def _build_public_market_provider(settings: MarketDataProviderSettings) -> MarketDataProvider:
    cache = TTLMarketDataCache(max_entries=settings.public_cache_max_entries)
    instruments = build_default_public_instrument_registry()
    yahoo = YahooFinanceAdapter(registry=instruments, cache=cache, timeout_seconds=settings.public_timeout_seconds, series_ttl_seconds=settings.public_yahoo_series_ttl_seconds, quote_ttl_seconds=settings.public_yahoo_quote_ttl_seconds, failure_ttl_seconds=settings.public_failure_ttl_seconds)
    akshare = AkShareETFAdapter(registry=instruments, cache=cache, series_ttl_seconds=settings.public_akshare_series_ttl_seconds, quote_ttl_seconds=settings.public_akshare_quote_ttl_seconds, failure_ttl_seconds=settings.public_failure_ttl_seconds)
    return PublicMarketProvider(registry=instruments, yahoo=yahoo, akshare=akshare, yahoo_enabled=settings.public_yahoo_enabled, akshare_enabled=settings.public_akshare_enabled)


def build_market_data_provider(
    settings: MarketDataProviderSettings,
    *,
    registry: MarketDataProviderRegistry | None = None,
) -> MarketDataProvider:
    return (registry or build_default_market_data_provider_registry()).create(settings)


def _normalize_provider_id(value: str) -> str:
    provider_id = str(value or "").strip().lower()
    if not provider_id or not all(character.isalnum() or character in {"_", "-"} for character in provider_id):
        raise ValueError("market data provider id must contain only letters, numbers, '_' or '-'")
    return provider_id


__all__ = [
    "MarketDataProviderRegistry",
    "MarketDataProviderSettings",
    "build_default_market_data_provider_registry",
    "build_market_data_provider",
]

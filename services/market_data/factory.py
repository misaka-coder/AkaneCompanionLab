from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .disabled import DisabledMarketDataProvider
from .emquant_bridge_client import EmQuantBridgeMarketDataProvider
from .provider import MarketDataProvider
from .types import MarketDataValidationError


MarketDataProviderBuilder = Callable[["MarketDataProviderSettings"], MarketDataProvider]


@dataclass(frozen=True)
class MarketDataProviderSettings:
    provider: str = "disabled"
    emquant_bridge_url: str = "http://127.0.0.1:9910"
    emquant_bridge_token: str = ""
    emquant_timeout_seconds: float = 15.0

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
    return registry


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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.market_data import MarketDataValidationError, MarketSeries

try:
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
except ImportError:  # pragma: no cover - exercised through the structured dependency failure
    Image = None
    ImageDraw = None
    ImageFont = None
    PngImagePlugin = None


SUPPORTED_CHART_TYPES = ("candlestick_volume",)
SUPPORTED_CHART_INTERVALS = ("1d",)
SUPPORTED_MOVING_AVERAGES = (5, 10, 20, 60)


@dataclass(frozen=True)
class ChartRequest:
    code: str
    chart_type: str = "candlestick_volume"
    interval: str = "1d"
    adjusted: str = "none"
    lookback: int = 120
    moving_averages: tuple[int, ...] = (5, 20)
    title: str = ""

    def __post_init__(self) -> None:
        code = str(self.code or "").strip().upper()
        if not code or len(code) > 40 or not re.fullmatch(r"[A-Z0-9_.:-]+", code):
            raise _invalid("code", "code must be an explicit provider security code")
        chart_type = str(self.chart_type or "candlestick_volume").strip().lower()
        if chart_type not in SUPPORTED_CHART_TYPES:
            raise _invalid("chart_type", "only candlestick_volume is supported")
        interval = str(self.interval or "1d").strip().lower()
        if interval not in SUPPORTED_CHART_INTERVALS:
            raise _invalid("interval", "local market charts currently support only 1d")
        adjusted = str(self.adjusted or "none").strip().lower()
        if adjusted not in {"none", "forward", "backward"}:
            raise _invalid("adjusted", "adjusted must be none, forward, or backward")
        if isinstance(self.lookback, bool):
            raise _invalid("lookback", "lookback must be an integer from 20 to 250")
        try:
            lookback = int(self.lookback)
        except (TypeError, ValueError) as exc:
            raise _invalid("lookback", "lookback must be an integer from 20 to 250") from exc
        if not 20 <= lookback <= 250:
            raise _invalid("lookback", "lookback must be between 20 and 250")

        moving_averages: list[int] = []
        for raw_window in tuple(self.moving_averages or ()):
            if isinstance(raw_window, bool):
                raise _invalid("moving_averages", "moving-average windows must use the fixed enum")
            try:
                window = int(raw_window)
            except (TypeError, ValueError) as exc:
                raise _invalid("moving_averages", "moving-average windows must use the fixed enum") from exc
            if window not in SUPPORTED_MOVING_AVERAGES:
                raise _invalid("moving_averages", "moving-average windows must be 5, 10, 20, or 60")
            if window not in moving_averages:
                moving_averages.append(window)
        if len(moving_averages) > 4:
            raise _invalid("moving_averages", "at most four moving averages are supported")

        title = re.sub(r"\s+", " ", str(self.title or "")).strip()
        if len(title) > 80:
            raise _invalid("title", "title must not exceed 80 characters")
        if any(ord(char) < 32 for char in title):
            raise _invalid("title", "title contains unsupported control characters")

        object.__setattr__(self, "code", code)
        object.__setattr__(self, "chart_type", chart_type)
        object.__setattr__(self, "interval", interval)
        object.__setattr__(self, "adjusted", adjusted)
        object.__setattr__(self, "lookback", lookback)
        object.__setattr__(self, "moving_averages", tuple(moving_averages))
        object.__setattr__(self, "title", title)


@dataclass(frozen=True)
class ChartArtifactResult:
    output_path: Path
    width: int
    height: int
    title: str
    code: str
    chart_type: str
    interval: str
    adjusted: str
    moving_averages: tuple[int, ...]
    point_count: int
    date_from: str
    date_to: str
    as_of: str
    provider: str
    source: str
    series_sha256: str
    latest_bar: tuple[int, float, float, float, float, float | None]
    moving_average_latest: tuple[tuple[int, float | None], ...]

    def evidence_metadata(self) -> dict[str, Any]:
        return {
            "chart_type": self.chart_type,
            "code": self.code,
            "interval": self.interval,
            "adjusted": self.adjusted,
            "moving_averages": list(self.moving_averages),
            "point_count": self.point_count,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "as_of": self.as_of,
            "provider": self.provider,
            "source": self.source,
            "series_sha256": self.series_sha256,
            "latest_bar": {
                "timestamp": self.latest_bar[0],
                "open": self.latest_bar[1],
                "high": self.latest_bar[2],
                "low": self.latest_bar[3],
                "close": self.latest_bar[4],
                "volume": self.latest_bar[5],
            },
            "moving_average_latest": {f"ma{window}": value for window, value in self.moving_average_latest},
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class _ValidatedBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None


class LocalChartProvider:
    """Render trusted market series with a fixed, non-programmable chart grammar."""

    provider_id = "local_chart_v1"
    width = 1280
    height = 720

    _COLORS = {
        "background": "#F6F8FB",
        "panel": "#FFFFFF",
        "border": "#D7DEE8",
        "grid": "#E8EDF3",
        "text": "#1D2733",
        "muted": "#647386",
        "up": "#D93F3F",
        "down": "#169B62",
        "flat": "#78879A",
        "ma5": "#E69F00",
        "ma10": "#7B61FF",
        "ma20": "#2F80ED",
        "ma60": "#B14AED",
    }

    def render(
        self,
        *,
        series: MarketSeries,
        request: ChartRequest,
        output_path: Path,
        source: str,
    ) -> ChartArtifactResult:
        if Image is None or ImageDraw is None or ImageFont is None or PngImagePlugin is None:
            raise MarketDataValidationError(
                field="chart_renderer",
                reason="Pillow is required for local market chart rendering",
                code="dependency_unavailable",
                status="unavailable",
                provider=self.provider_id,
            )
        bars, zone = self._validate_series(series=series, request=request)
        target = Path(output_path)
        if target.suffix.lower() != ".png":
            raise _invalid("output_format", "local market charts must be written as PNG", self.provider_id)
        market_provider = re.sub(r"\s+", " ", str(series.provider or "")).strip()[:80]
        if not market_provider:
            raise _invalid("series.provider", "series provider is required", self.provider_id)
        clean_source = re.sub(r"\s+", " ", str(source or "")).strip()[:160] or market_provider
        display_title = request.title or f"{request.code} 日线 K 线与成交量"
        first_date = datetime.fromtimestamp(bars[0].timestamp, tz=zone).date().isoformat()
        last_date = datetime.fromtimestamp(bars[-1].timestamp, tz=zone).date().isoformat()
        as_of = datetime.fromtimestamp(int(series.as_of), tz=zone).isoformat()
        series_payload = [
            {
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]
        series_sha256 = hashlib.sha256(
            json.dumps(series_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        closes = [bar.close for bar in bars]
        moving_average_latest = tuple(
            (window, _moving_average(closes, window)[-1]) for window in request.moving_averages
        )

        image = Image.new("RGB", (self.width, self.height), self._COLORS["background"])
        draw = ImageDraw.Draw(image)
        fonts = {
            "title": self._load_font(30, bold=True),
            "body": self._load_font(18),
            "small": self._load_font(15),
            "tiny": self._load_font(13),
        }
        self._draw_chart(
            draw=draw,
            bars=bars,
            request=request,
            title=display_title,
            source=clean_source,
            provider=market_provider,
            as_of=as_of,
            first_date=first_date,
            last_date=last_date,
            zone=zone,
            fonts=fonts,
        )

        metadata = PngImagePlugin.PngInfo()
        chart_metadata = {
            "schema_version": "akane.market_chart.v1",
            "title": display_title,
            "code": request.code,
            "chart_type": request.chart_type,
            "interval": request.interval,
            "adjusted": request.adjusted,
            "moving_averages": list(request.moving_averages),
            "point_count": len(bars),
            "date_from": first_date,
            "date_to": last_date,
            "as_of": as_of,
            "provider": market_provider,
            "source": clean_source,
            "series_sha256": series_sha256,
            "latest_bar": series_payload[-1],
            "moving_average_latest": {f"ma{window}": value for window, value in moving_average_latest},
        }
        metadata.add_text("akane_chart", json.dumps(chart_metadata, ensure_ascii=False, sort_keys=True))
        metadata.add_text("Title", display_title)
        metadata.add_text("Description", f"{request.code} {first_date} to {last_date}; as_of {as_of}")

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            image.save(temporary, format="PNG", pnginfo=metadata, optimize=False, compress_level=6)
            if not temporary.exists() or temporary.stat().st_size <= 0:
                raise RuntimeError("chart renderer produced an empty PNG")
            temporary.replace(target)
        finally:
            image.close()
            if temporary.exists():
                temporary.unlink(missing_ok=True)

        return ChartArtifactResult(
            output_path=target,
            width=self.width,
            height=self.height,
            title=display_title,
            code=request.code,
            chart_type=request.chart_type,
            interval=request.interval,
            adjusted=request.adjusted,
            moving_averages=request.moving_averages,
            point_count=len(bars),
            date_from=first_date,
            date_to=last_date,
            as_of=as_of,
            provider=market_provider,
            source=clean_source,
            series_sha256=series_sha256,
            latest_bar=(
                bars[-1].timestamp,
                bars[-1].open,
                bars[-1].high,
                bars[-1].low,
                bars[-1].close,
                bars[-1].volume,
            ),
            moving_average_latest=moving_average_latest,
        )

    def _validate_series(self, *, series: MarketSeries, request: ChartRequest) -> tuple[list[_ValidatedBar], ZoneInfo]:
        if not isinstance(series, MarketSeries):
            raise _invalid("series", "provider did not return a normalized MarketSeries", self.provider_id)
        if str(series.code or "").strip().upper() != request.code:
            raise _invalid("series.code", "series code does not match the chart request", self.provider_id)
        if str(series.interval or "").strip().lower() != request.interval:
            raise _invalid("series.interval", "series interval does not match the chart request", self.provider_id)
        if str(series.adjusted or "").strip().lower() != request.adjusted:
            raise _invalid("series.adjusted", "series adjustment does not match the chart request", self.provider_id)
        try:
            zone = ZoneInfo(str(series.timezone or "Asia/Shanghai"))
        except ZoneInfoNotFoundError as exc:
            raise _invalid("series.timezone", "series timezone is unknown", self.provider_id) from exc
        if len(series.points) < 2:
            raise _invalid("series.points", "at least two OHLCV bars are required", self.provider_id)
        if len(series.points) > 5000:
            raise _invalid("series.points", "series contains too many bars", self.provider_id)

        selected = tuple(series.points[-request.lookback :])
        bars: list[_ValidatedBar] = []
        previous_timestamp = 0
        for index, point in enumerate(selected):
            timestamp = _positive_int(point.timestamp, field=f"series.points[{index}].timestamp")
            if timestamp <= previous_timestamp:
                raise _invalid(
                    f"series.points[{index}].timestamp",
                    "timestamps must be unique and strictly increasing",
                    self.provider_id,
                )
            previous_timestamp = timestamp
            open_price = _finite_number(point.open, field=f"series.points[{index}].open", positive=True)
            high = _finite_number(point.high, field=f"series.points[{index}].high", positive=True)
            low = _finite_number(point.low, field=f"series.points[{index}].low", positive=True)
            close = _finite_number(point.close, field=f"series.points[{index}].close", positive=True)
            if low > min(open_price, close) or high < max(open_price, close) or high < low:
                raise _invalid(
                    f"series.points[{index}]",
                    "OHLC values must satisfy low <= open/close <= high",
                    self.provider_id,
                )
            volume = None
            if point.volume is not None:
                volume = _finite_number(point.volume, field=f"series.points[{index}].volume", nonnegative=True)
            bars.append(
                _ValidatedBar(
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
        as_of = _positive_int(series.as_of, field="series.as_of")
        if as_of < bars[-1].timestamp:
            raise _invalid("series.as_of", "as_of cannot be earlier than the latest bar", self.provider_id)
        return bars, zone

    def _draw_chart(
        self,
        *,
        draw,
        bars: list[_ValidatedBar],
        request: ChartRequest,
        title: str,
        source: str,
        provider: str,
        as_of: str,
        first_date: str,
        last_date: str,
        zone: ZoneInfo,
        fonts: dict[str, Any],
    ) -> None:
        colors = self._COLORS
        draw.rounded_rectangle((24, 20, self.width - 24, self.height - 20), radius=18, fill=colors["panel"])
        draw.text(
            (52, 40),
            _fit_text(draw, title, font=fonts["title"], max_width=self.width - 104),
            fill=colors["text"],
            font=fonts["title"],
        )
        subtitle = (
            f"{request.code}  |  {request.interval}  |  {request.adjusted}  |  "
            f"{first_date} ~ {last_date}  |  {len(bars)} bars"
        )
        draw.text(
            (54, 84),
            _fit_text(draw, subtitle, font=fonts["small"], max_width=self.width - 108),
            fill=colors["muted"],
            font=fonts["small"],
        )

        left, right = 78, self.width - 72
        price_top, price_bottom = 126, 482
        volume_top, volume_bottom = 520, 642
        plot_width = right - left
        price_height = price_bottom - price_top
        volume_height = volume_bottom - volume_top
        draw.rectangle((left, price_top, right, price_bottom), outline=colors["border"], width=1)
        draw.rectangle((left, volume_top, right, volume_bottom), outline=colors["border"], width=1)

        min_price = min(bar.low for bar in bars)
        max_price = max(bar.high for bar in bars)
        spread = max_price - min_price
        padding = spread * 0.08 if spread > 0 else max(abs(max_price) * 0.02, 1.0)
        display_min = min_price - padding
        display_max = max_price + padding

        def price_y(value: float) -> float:
            return price_bottom - ((value - display_min) / (display_max - display_min)) * price_height

        for step in range(6):
            ratio = step / 5
            y = price_top + ratio * price_height
            value = display_max - ratio * (display_max - display_min)
            draw.line((left, y, right, y), fill=colors["grid"], width=1)
            label = f"{value:,.2f}"
            bbox = draw.textbbox((0, 0), label, font=fonts["tiny"])
            draw.text((left - (bbox[2] - bbox[0]) - 8, y - 7), label, fill=colors["muted"], font=fonts["tiny"])

        slot = plot_width / len(bars)
        body_width = max(3, min(15, int(slot * 0.58)))
        maximum_volume = max((bar.volume or 0.0 for bar in bars), default=0.0)
        closes = [bar.close for bar in bars]
        moving_average_values = {window: _moving_average(closes, window) for window in request.moving_averages}
        x_positions: list[float] = []
        for index, bar in enumerate(bars):
            x = left + (index + 0.5) * slot
            x_positions.append(x)
            color = colors["up"] if bar.close > bar.open else colors["down"] if bar.close < bar.open else colors["flat"]
            draw.line((x, price_y(bar.high), x, price_y(bar.low)), fill=color, width=max(1, body_width // 5))
            open_y = price_y(bar.open)
            close_y = price_y(bar.close)
            top = min(open_y, close_y)
            bottom = max(open_y, close_y)
            if bottom - top < 2:
                top -= 1
                bottom += 1
            draw.rectangle((x - body_width / 2, top, x + body_width / 2, bottom), fill=color, outline=color)
            if maximum_volume > 0 and bar.volume is not None:
                volume_bar_height = (bar.volume / maximum_volume) * (volume_height - 10)
                draw.rectangle(
                    (x - body_width / 2, volume_bottom - volume_bar_height, x + body_width / 2, volume_bottom),
                    fill=color,
                )

        for window, values in moving_average_values.items():
            points = [(x_positions[index], price_y(value)) for index, value in enumerate(values) if value is not None]
            if len(points) >= 2:
                draw.line(points, fill=colors[f"ma{window}"], width=2, joint="curve")

        legend_x = right - 360
        for index, window in enumerate(request.moving_averages):
            x = legend_x + index * 88
            color = colors[f"ma{window}"]
            draw.line((x, 99, x + 18, 99), fill=color, width=3)
            draw.text((x + 23, 90), f"MA{window}", fill=colors["muted"], font=fonts["tiny"])

        draw.text((left, volume_top - 25), "Volume", fill=colors["muted"], font=fonts["small"])
        if maximum_volume <= 0:
            draw.text((left + 12, volume_top + 44), "Volume unavailable", fill=colors["muted"], font=fonts["small"])
        else:
            draw.text(
                (right - 95, volume_top + 4), _compact_number(maximum_volume), fill=colors["muted"], font=fonts["tiny"]
            )

        label_indexes = _label_indexes(len(bars), maximum=6)
        for index in label_indexes:
            label = datetime.fromtimestamp(bars[index].timestamp, tz=zone).strftime("%m-%d")
            bbox = draw.textbbox((0, 0), label, font=fonts["tiny"])
            x = x_positions[index] - (bbox[2] - bbox[0]) / 2
            draw.text((x, volume_bottom + 7), label, fill=colors["muted"], font=fonts["tiny"])

        footer = f"Data source: {source}  |  Provider: {provider}  |  As of: {as_of}"
        draw.text(
            (52, 680),
            _fit_text(draw, footer, font=fonts["tiny"], max_width=self.width - 104),
            fill=colors["muted"],
            font=fonts["tiny"],
        )

    def _load_font(self, size: int, *, bold: bool = False):
        candidates = _font_candidates(bold=bold)
        for candidate in candidates:
            try:
                if candidate.exists():
                    return ImageFont.truetype(str(candidate), size=size)
            except (OSError, ValueError):
                continue
        return ImageFont.load_default()


def _font_candidates(*, bold: bool) -> tuple[Path, ...]:
    windows_name = "msyhbd.ttc" if bold else "msyh.ttc"
    noto_name = "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc"
    dejavu_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return (
        Path("C:/Windows/Fonts") / windows_name,
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto") / noto_name,
        Path("/usr/share/fonts/truetype/noto") / noto_name,
        Path("/usr/share/fonts/truetype/dejavu") / dejavu_name,
        Path("/System/Library/Fonts/PingFang.ttc"),
    )


def _moving_average(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        if index + 1 >= window:
            result[index] = running / window
    return result


def _label_indexes(length: int, *, maximum: int) -> tuple[int, ...]:
    if length <= maximum:
        return tuple(range(length))
    indexes = {round(index * (length - 1) / (maximum - 1)) for index in range(maximum)}
    return tuple(sorted(indexes))


def _compact_number(value: float) -> str:
    amount = float(value)
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(amount) >= divisor:
            return f"{amount / divisor:.2f}{suffix}"
    return f"{amount:,.0f}"


def _fit_text(draw, value: str, *, font, max_width: int) -> str:
    text = str(value or "")
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    available = max(0, max_width - int(draw.textlength(suffix, font=font)))
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if draw.textlength(text[:middle], font=font) <= available:
            low = middle
        else:
            high = middle - 1
    return f"{text[:low].rstrip()}{suffix}"


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise _invalid(field, "value must be a positive integer", LocalChartProvider.provider_id)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _invalid(field, "value must be a positive integer", LocalChartProvider.provider_id) from exc
    if number <= 0:
        raise _invalid(field, "value must be positive", LocalChartProvider.provider_id)
    return number


def _finite_number(
    value: Any,
    *,
    field: str,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    if isinstance(value, bool):
        raise _invalid(field, "value must be a finite number", LocalChartProvider.provider_id)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _invalid(field, "value must be a finite number", LocalChartProvider.provider_id) from exc
    if not math.isfinite(number):
        raise _invalid(field, "value must be finite", LocalChartProvider.provider_id)
    if positive and number <= 0:
        raise _invalid(field, "value must be positive", LocalChartProvider.provider_id)
    if nonnegative and number < 0:
        raise _invalid(field, "value cannot be negative", LocalChartProvider.provider_id)
    return number


def _invalid(field: str, reason: str, provider: str = "local_chart_v1") -> MarketDataValidationError:
    invalid_series = field == "series" or field.startswith("series.")
    return MarketDataValidationError(
        field=field,
        reason=reason,
        code="invalid_market_series" if invalid_series else "invalid_arguments",
        status="invalid_data" if invalid_series else "invalid_arguments",
        provider=provider,
    )


__all__ = [
    "ChartArtifactResult",
    "ChartRequest",
    "LocalChartProvider",
    "SUPPORTED_CHART_INTERVALS",
    "SUPPORTED_CHART_TYPES",
    "SUPPORTED_MOVING_AVERAGES",
]

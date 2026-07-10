from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FakeEmQuantData:
    ErrorCode: int = 0
    ErrorMsg: str = "success"
    Codes: list[str] = field(default_factory=list)
    Indicators: list[str] = field(default_factory=list)
    Dates: list[str] = field(default_factory=list)
    Data: dict[str, Any] = field(default_factory=dict)
    RequestID: int = 0
    SerialID: int = 0


class FakeEmQuantSDK:
    """Deterministic SDK double. It never loads libraries, logs in, or uses network."""

    def __init__(
        self,
        *,
        start_error_code: int = 0,
        function_error_codes: dict[str, int] | None = None,
        news_rows: list[dict[str, Any]] | None = None,
        quote_rows: list[dict[str, Any]] | None = None,
        series_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.start_error_code = int(start_error_code)
        self.function_error_codes = {str(name): int(code) for name, code in dict(function_error_codes or {}).items()}
        self.news_rows = list(news_rows or [])
        self.quote_rows = list(quote_rows or [])
        self.series_rows = list(series_rows or [])
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.started = False
        self.main_callback: Callable[[Any], Any] | None = None
        self.news_callbacks: dict[int, Callable[[Any], Any]] = {}
        self.quote_callbacks: dict[int, Callable[[Any], Any]] = {}
        self._serial_id = 1000

    def start(self, options: str = "", logcallback=None, mainCallBack=None):
        self.calls.append(("start", (options, logcallback, mainCallBack)))
        self.main_callback = mainCallBack if callable(mainCallBack) else None
        code = self.start_error_code
        self.started = code == 0
        return self._result("start", error_code=code)

    def stop(self):
        self.calls.append(("stop", ()))
        self.started = False
        return self._result("stop")

    def datastatistics(self, funcname: str, indicators: str = "", options: str = ""):
        self.calls.append(("datastatistics", (funcname, indicators, options)))
        code = self._error("datastatistics")
        return FakeEmQuantData(
            ErrorCode=code,
            ErrorMsg=self._message(code),
            Indicators=["FUNCNAME", "USEDRATIO", "AVAILABEDATA"],
            Data={"0": ["cfn", 0.1, 900]},
        )

    def cfn(self, codes: str, content: str, mode: int, options: str = ""):
        self.calls.append(("cfn", (codes, content, mode, options)))
        code = self._error("cfn")
        indicators = [
            "datetime",
            "eitime",
            "content",
            "title",
            "infoCode",
            "medianname",
            "url",
            "type",
            "label",
        ]
        grouped: dict[str, list[list[Any]]] = {}
        for row in self.news_rows:
            row_code = str(row.get("code") or "")
            grouped.setdefault(row_code, []).append([row.get(name) for name in indicators])
        return FakeEmQuantData(
            ErrorCode=code,
            ErrorMsg=self._message(code),
            Codes=list(grouped.keys()),
            Indicators=indicators,
            Data=grouped if code == 0 else {},
        )

    def csqsnapshot(self, codes: str, indicators: str, options: str = ""):
        self.calls.append(("csqsnapshot", (codes, indicators, options)))
        code = self._error("csqsnapshot")
        fields = [item.strip() for item in str(indicators or "").split(",") if item.strip()]
        data: dict[str, list[Any]] = {}
        for row in self.quote_rows:
            row_code = str(row.get("code") or row.get("CODE") or "")
            folded = {str(key).lower(): value for key, value in row.items()}
            data[row_code] = [folded.get(field.lower()) for field in fields]
        return FakeEmQuantData(
            ErrorCode=code,
            ErrorMsg=self._message(code),
            Codes=list(data.keys()),
            Indicators=fields,
            Data=data if code == 0 else {},
        )

    def csd(self, codes: str, indicators: str, startdate=None, enddate=None, options: str = ""):
        self.calls.append(("csd", (codes, indicators, startdate, enddate, options)))
        code = self._error("csd")
        requested_codes = [item.strip() for item in str(codes or "").split(",") if item.strip()]
        fields = [item.strip() for item in str(indicators or "").split(",") if item.strip()]
        dates = sorted(
            {
                str(row.get("datetime") or row.get("date") or "").strip()
                for row in self.series_rows
                if str(row.get("datetime") or row.get("date") or "").strip()
            }
        )
        start_text = str(startdate or "").strip()
        end_text = str(enddate or "").strip()
        dates = [
            date_value
            for date_value in dates
            if (not start_text or date_value[:10] >= start_text[:10])
            and (not end_text or date_value[:10] <= end_text[:10])
        ]
        data: dict[str, list[list[Any]]] = {}
        for requested_code in requested_codes:
            rows_by_date = {
                str(row.get("datetime") or row.get("date") or "").strip(): {
                    str(key).lower(): value for key, value in row.items()
                }
                for row in self.series_rows
                if str(row.get("code") or row.get("CODE") or "").strip().upper() == requested_code.upper()
            }
            if not rows_by_date:
                continue
            data[requested_code] = [
                [rows_by_date.get(date_value, {}).get(field.lower()) for date_value in dates]
                for field in fields
            ]
        return FakeEmQuantData(
            ErrorCode=code,
            ErrorMsg=self._message(code),
            Codes=list(data.keys()),
            Indicators=fields,
            Dates=dates,
            Data=data if code == 0 else {},
        )

    def css(self, codes: str, indicators: str, options: str = ""):
        self.calls.append(("css", (codes, indicators, options)))
        return self._result("css", error_code=self._error("css"))

    def sector(self, pukeycode: str, tradedate: str, options: str = ""):
        self.calls.append(("sector", (pukeycode, tradedate, options)))
        return self._result("sector", error_code=self._error("sector"))

    def cnq(self, codes: str, content: str, options: str = "", fncallback=None, userparams=None):
        self.calls.append(("cnq", (codes, content, options, fncallback, userparams)))
        code = self._error("cnq")
        serial_id = self._next_serial() if code == 0 else 0
        if serial_id and callable(fncallback):
            self.news_callbacks[serial_id] = fncallback
        return FakeEmQuantData(
            ErrorCode=code,
            ErrorMsg=self._message(code),
            SerialID=serial_id,
        )

    def cnqcancel(self, serialID: int):
        self.calls.append(("cnqcancel", (serialID,)))
        code = self._error("cnqcancel")
        if code == 0:
            self.news_callbacks.pop(int(serialID), None)
        return self._result("cnqcancel", error_code=code)

    def csq(self, codes: str, indicators: str, options: str = "", fncallback=None, userparams=None):
        self.calls.append(("csq", (codes, indicators, options, fncallback, userparams)))
        code = self._error("csq")
        serial_id = self._next_serial() if code == 0 else 0
        if serial_id and callable(fncallback):
            self.quote_callbacks[serial_id] = fncallback
        return FakeEmQuantData(
            ErrorCode=code,
            ErrorMsg=self._message(code),
            SerialID=serial_id,
        )

    def csqcancel(self, serialID: int):
        self.calls.append(("csqcancel", (serialID,)))
        code = self._error("csqcancel")
        if code == 0:
            self.quote_callbacks.pop(int(serialID), None)
        return self._result("csqcancel", error_code=code)

    def emit_news(self, serial_id: int, rows: list[dict[str, Any]]) -> None:
        callback = self.news_callbacks[int(serial_id)]
        result = FakeEmQuantSDK(news_rows=rows).cfn("", "", 0, "")
        result.SerialID = int(serial_id)
        callback(result)

    def emit_quote(self, serial_id: int, rows: list[dict[str, Any]], indicators: str) -> None:
        callback = self.quote_callbacks[int(serial_id)]
        result = FakeEmQuantSDK(quote_rows=rows).csqsnapshot("", indicators, "")
        result.SerialID = int(serial_id)
        callback(result)

    def emit_system_error(self, error_code: int) -> None:
        if self.main_callback is None:
            return
        self.main_callback(
            FakeEmQuantData(
                ErrorCode=int(error_code),
                ErrorMsg=self._message(int(error_code)),
            )
        )

    def porder(self, *args: Any):
        self.calls.append(("porder", args))
        raise AssertionError("mutating API must never be called by the bridge")

    def _result(self, function_name: str, *, error_code: int | None = None) -> FakeEmQuantData:
        code = self._error(function_name) if error_code is None else int(error_code)
        return FakeEmQuantData(ErrorCode=code, ErrorMsg=self._message(code))

    def _error(self, function_name: str) -> int:
        return int(self.function_error_codes.get(function_name, 0))

    @staticmethod
    def _message(error_code: int) -> str:
        return "success" if int(error_code) == 0 else f"fake_error_{int(error_code)}"

    def _next_serial(self) -> int:
        self._serial_id += 1
        return self._serial_id

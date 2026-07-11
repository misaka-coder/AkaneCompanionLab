from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class PublicMarketDependencyIsolationTests(unittest.TestCase):
    def test_public_market_dependencies_are_pinned_and_optional(self) -> None:
        optional_requirements = (ROOT / "requirements-finance-public.txt").read_text(encoding="utf-8").lower()
        base_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()

        self.assertIn("yfinance==1.5.1", optional_requirements)
        self.assertIn("akshare==1.18.64", optional_requirements)
        self.assertNotIn("yfinance", base_requirements)
        self.assertNotIn("akshare", base_requirements)


class YahooShapeFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _load_fixture("public_market_yahoo_v1.json")

    def test_fixture_distinguishes_schema_contract_from_live_market_data(self) -> None:
        self.assertEqual(self.fixture["fixture_schema"], "akane.public_market.yahoo_shape.v1")
        self.assertEqual(self.fixture["fixture_kind"], "schema_contract")
        self.assertFalse(self.fixture["live_observation"]["ok"])
        self.assertEqual(self.fixture["live_observation"]["status"], "unavailable")
        self.assertFalse(self.fixture["safety"]["contains_market_rows"])

    def test_adapter_arguments_override_risky_yfinance_defaults(self) -> None:
        contract = self.fixture["download_contract"]
        self.assertTrue(contract["observed_defaults"]["auto_adjust"])
        self.assertTrue(contract["observed_defaults"]["multi_level_index"])
        self.assertEqual(
            contract["adapter_locked_arguments"],
            {
                "auto_adjust": False,
                "multi_level_index": False,
                "threads": False,
                "progress": False,
                "interval": "1d",
            },
        )
        self.assertEqual(set(contract["required_columns"]), {"Open", "High", "Low", "Close"})


class AkShareETFShapeFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _load_fixture("public_market_akshare_etf_v1.json")

    def test_fixture_records_minimal_live_shape_and_actual_upstream(self) -> None:
        self.assertEqual(self.fixture["fixture_schema"], "akane.public_market.akshare_etf_shape.v1")
        self.assertEqual(self.fixture["fixture_kind"], "minimal_live_observation")
        self.assertEqual(self.fixture["upstream"]["name"], "Eastmoney public web data")
        self.assertEqual(self.fixture["upstream"]["spot_function"], "fund_etf_spot_em")
        self.assertEqual(self.fixture["upstream"]["history_function"], "fund_etf_hist_em")

    def test_spot_shape_preserves_exchange_time_and_required_fields(self) -> None:
        shape = self.fixture["spot_shape"]
        required = {"代码", "名称", "最新价", "成交量", "成交额", "数据日期", "更新时间"}
        self.assertTrue(required.issubset(shape["columns"]))
        self.assertEqual(shape["timezone"], "Asia/Shanghai")
        captured_at = datetime.fromisoformat(self.fixture["captured_at"])
        for sample in shape["samples"]:
            observed_at = datetime.fromisoformat(sample["updated_at"])
            self.assertIsNotNone(observed_at.tzinfo)
            self.assertEqual(observed_at.utcoffset().total_seconds(), 8 * 60 * 60)
            self.assertLess(observed_at, captured_at)

    def test_volume_is_normalized_from_lots_instead_of_passed_through(self) -> None:
        for section_name in ("spot_shape", "history_shape"):
            section = self.fixture[section_name]
            self.assertEqual(section["volume_source_unit"], "lot_100_shares")
            self.assertEqual(section["volume_normalization_multiplier"], 100)
            for sample in section["samples"]:
                reference_price = sample.get("last", sample.get("close"))
                implied_multiplier = sample["amount_cny"] / (reference_price * sample["volume_lots"])
                self.assertGreater(implied_multiplier, 80)
                self.assertLess(implied_multiplier, 120)

    def test_verified_etfs_keep_canonical_and_vendor_codes_separate(self) -> None:
        instruments = {item["canonical_code"]: item for item in self.fixture["verified_instruments"]}
        self.assertEqual(set(instruments), {"513000.SH", "513520.SH"})
        for canonical_code, instrument in instruments.items():
            self.assertNotEqual(canonical_code, instrument["vendor_symbol"])
            self.assertEqual(instrument["tracking_target"], "日经225指数")
            self.assertEqual(instrument["exchange"], "上海证券交易所")
            self.assertGreaterEqual(len(instrument["official_sources"]), 2)


if __name__ == "__main__":
    unittest.main()

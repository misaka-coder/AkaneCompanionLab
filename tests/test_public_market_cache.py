from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import unittest

from services.market_data.public_cache import TTLMarketDataCache


class MutableClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class TTLMarketDataCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.cache = TTLMarketDataCache(max_entries=2, clock=self.clock)

    def test_hit_preserves_original_value_and_expiry_metadata(self) -> None:
        value = object()
        self.cache.set(("yahoo", "NIKKEI225.INDEX"), value, ttl_seconds=60)

        lookup = self.cache.get(("yahoo", "NIKKEI225.INDEX"))

        self.assertTrue(lookup.hit)
        self.assertIs(lookup.value, value)
        self.assertFalse(lookup.negative)
        self.assertEqual(lookup.stored_at, 100.0)
        self.assertEqual(lookup.expires_at, 160.0)

    def test_expired_values_are_never_returned_as_stale(self) -> None:
        self.cache.set("series", "old", ttl_seconds=10)
        self.clock.value = 110.0

        lookup = self.cache.get("series")

        self.assertFalse(lookup.hit)
        self.assertEqual(len(self.cache), 0)

    def test_negative_entries_are_explicit_and_expire_normally(self) -> None:
        self.cache.set("failure", {"status": "unavailable"}, ttl_seconds=15, negative=True)

        lookup = self.cache.get("failure")

        self.assertTrue(lookup.hit)
        self.assertTrue(lookup.negative)
        self.clock.value = 116.0
        self.assertFalse(self.cache.get("failure").hit)

    def test_lru_eviction_keeps_recently_read_entry(self) -> None:
        self.cache.set("first", 1, ttl_seconds=60)
        self.cache.set("second", 2, ttl_seconds=60)
        self.cache.get("first")
        self.cache.set("third", 3, ttl_seconds=60)

        self.assertTrue(self.cache.get("first").hit)
        self.assertFalse(self.cache.get("second").hit)
        self.assertTrue(self.cache.get("third").hit)

    def test_delete_clear_and_invalid_arguments(self) -> None:
        self.cache.set("one", 1, ttl_seconds=10)
        self.assertTrue(self.cache.delete("one"))
        self.assertFalse(self.cache.delete("one"))
        self.cache.set("two", 2, ttl_seconds=10)
        self.cache.clear()
        self.assertEqual(len(self.cache), 0)
        with self.assertRaises(ValueError):
            self.cache.set("bad", 1, ttl_seconds=0)
        with self.assertRaises(TypeError):
            self.cache.get([])

    def test_concurrent_access_stays_bounded(self) -> None:
        cache = TTLMarketDataCache(max_entries=256, clock=self.clock)

        def write_and_read(index: int) -> bool:
            key = ("key", index)
            cache.set(key, index, ttl_seconds=60)
            return cache.get(key).hit

        with ThreadPoolExecutor(max_workers=8) as executor:
            hits = list(executor.map(write_and_read, range(200)))

        self.assertTrue(all(hits))
        self.assertLessEqual(len(cache), 256)


if __name__ == "__main__":
    unittest.main()

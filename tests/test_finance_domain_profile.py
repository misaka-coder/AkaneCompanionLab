from __future__ import annotations

import unittest

from companion_v01.domain_profiles import (
    DEFAULT_DOMAIN_PROFILE_ID,
    FINANCE_DOMAIN_PROFILE_ID,
    DomainProfileRegistry,
    build_domain_profile_prompt,
    filter_tool_names,
    resolve_turn_domain_context,
)
from companion_v01.qq_gateway import QQMessageContext


class RetiredFinanceDomainProfileTests(unittest.TestCase):
    def test_legacy_finance_request_resolves_to_default_without_prompt_or_static_filter(self) -> None:
        registry = DomainProfileRegistry(
            finance_enabled=True,
            finance_push_enabled=True,
            finance_tool_round_budget=12,
            finance_tool_round_hard_limit=16,
        )

        profile = registry.resolve(profile_id=FINANCE_DOMAIN_PROFILE_ID, finance_mode="push")

        self.assertEqual(profile.id, DEFAULT_DOMAIN_PROFILE_ID)
        self.assertEqual(build_domain_profile_prompt(profile), "")
        self.assertEqual(
            filter_tool_names(("web_search", "send_sticker", "plugin.read.v1"), profile),
            ("web_search", "send_sticker", "plugin.read.v1"),
        )

    def test_turn_context_drops_legacy_finance_mode_and_profile(self) -> None:
        profile, mode = resolve_turn_domain_context(
            {"finance_mode": "push", "domain_profile": FINANCE_DOMAIN_PROFILE_ID}
        )

        self.assertEqual(profile.id, DEFAULT_DOMAIN_PROFILE_ID)
        self.assertEqual(mode, "off")

    def test_qq_turn_payload_does_not_advertise_retired_finance_mode(self) -> None:
        payload = QQMessageContext(
            should_respond=True,
            reason="private_message",
            session_id="owner",
            profile_user_id="owner",
            clean_message="贵州茅台现在怎么看？",
            finance_mode="push",
        ).to_turn_payload()

        self.assertNotIn("finance_mode", payload)
        self.assertNotIn("domain_profile", payload)


if __name__ == "__main__":
    unittest.main()

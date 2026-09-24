from __future__ import annotations

import unittest

from companion_v01.prompt_context_lifecycle import (
    PromptContextContribution,
    PromptContextLifecycle,
    materialize_prompt_contexts,
)


class PromptContextLifecycleTests(unittest.TestCase):
    def test_enum_values_keep_their_declared_lifecycle(self) -> None:
        result = materialize_prompt_contexts(
            [
                PromptContextContribution(
                    name="stable",
                    content="STABLE",
                    lifecycle=PromptContextLifecycle.STABLE,
                ),
                PromptContextContribution(
                    name="turn",
                    content="TURN",
                    lifecycle=PromptContextLifecycle.TURN,
                ),
            ],
            event_timeline_authoritative=True,
        )

        self.assertEqual(
            result.sections,
            (("stable", "STABLE", False), ("turn", "TURN", True)),
        )

    def test_authoritative_timeline_skips_any_event_backed_source_without_rendering_it(self) -> None:
        renders: list[str] = []
        result = materialize_prompt_contexts(
            [
                PromptContextContribution(
                    name="custom.runtime_state",
                    content=lambda: renders.append("rendered") or "LARGE REPEATED SNAPSHOT",
                    lifecycle=PromptContextLifecycle.EVENT_BACKED,
                )
            ],
            event_timeline_authoritative=True,
        )

        self.assertEqual(renders, [])
        self.assertEqual(result.sections, ())
        self.assertEqual(result.skipped_event_backed, ("custom.runtime_state",))

    def test_event_backed_source_falls_back_to_visible_turn_context_without_authoritative_timeline(self) -> None:
        result = materialize_prompt_contexts(
            [
                PromptContextContribution(
                    name="custom.runtime_state",
                    content="CURRENT SNAPSHOT",
                    lifecycle="event_backed",
                )
            ],
            event_timeline_authoritative=False,
        )

        self.assertEqual(result.sections, (("custom.runtime_state", "CURRENT SNAPSHOT", True),))
        self.assertEqual(result.skipped_event_backed, ())

    def test_unknown_lifecycle_stays_visible_instead_of_being_dropped_for_cache(self) -> None:
        result = materialize_prompt_contexts(
            [
                PromptContextContribution(
                    name="third_party.state",
                    content="VISIBLE",
                    lifecycle="future_mode",
                )
            ],
            event_timeline_authoritative=True,
        )

        self.assertEqual(result.sections, (("third_party.state", "VISIBLE", True),))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from companion_v01.client_protocol import ClientMode
from companion_v01.engine_services.turn_context import resolve_qq_actor_relation


class TurnContextTests(unittest.TestCase):
    def test_qq_owner_relation_uses_verified_stable_id(self) -> None:
        self.assertEqual(
            resolve_qq_actor_relation(
                client_mode=ClientMode.QQ_TEXT,
                actor_stable_id="qq:1906243651",
                master_qq="1906243651",
            ),
            "owner",
        )

    def test_other_qq_member_is_participant_even_when_the_name_implies_owner(self) -> None:
        self.assertEqual(
            resolve_qq_actor_relation(
                client_mode=ClientMode.QQ_TEXT,
                actor_stable_id="qq:2660153472",
                master_qq="1906243651",
            ),
            "participant",
        )

    def test_relation_is_not_invented_outside_a_concrete_qq_actor(self) -> None:
        self.assertEqual(
            resolve_qq_actor_relation(
                client_mode=ClientMode.DESKTOP_PET,
                actor_stable_id="qq:1906243651",
                master_qq="1906243651",
            ),
            "",
        )
        self.assertEqual(
            resolve_qq_actor_relation(
                client_mode=ClientMode.QQ_TEXT,
                actor_stable_id="",
                master_qq="1906243651",
            ),
            "",
        )
        self.assertEqual(
            resolve_qq_actor_relation(
                client_mode=ClientMode.QQ_TEXT,
                actor_stable_id="qq:2660153472",
                master_qq=0,
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()

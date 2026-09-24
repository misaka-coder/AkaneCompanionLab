import asyncio
import base64
import io
import unittest
from unittest.mock import patch

from PIL import Image

from companion_v01.scene.contracts import CancelRequest, ImportRequest, Receipt, TurnRequest
from companion_v01.scene.presentation.service import PresentationService
from companion_v01.scene.resources.importer import ResourceImporter
from tests import test_scene_room as fixture


class SceneRecoveryTests(unittest.TestCase):
    setUp = fixture.SceneRoomTests.setUp
    make_service = fixture.SceneRoomTests.make_service
    request = fixture.SceneRoomTests.request

    def test_crash_after_care_commit_recovers_without_second_charge(self):
        before = self.service.snapshot(self.identity)
        request = self.request('buy', 'dango', 'crash-purchase01')
        apply = self.service._apply
        def crash(*args):
            apply(*args)
            raise RuntimeError('process died after Care save')
        with patch.object(self.service, '_apply', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.service.action(request)
        self.make_service()
        recovered = self.service.action(request)
        self.assertTrue(recovered.duplicate)
        self.assertEqual(recovered.snapshot.care.coins, before.care.coins - 7)
        self.assertEqual(recovered.snapshot.care.inventory['dango'], 1)

    def image(self, mode='RGB', color='navy'):
        data = io.BytesIO()
        Image.new(mode, (256, 256), color).save(data, format='PNG')
        return base64.b64encode(data.getvalue()).decode()

    def test_import_is_visible_immutable_and_does_not_equip(self):
        before = self.service.snapshot(self.identity)
        request = ImportRequest(identity=self.identity, kind='background', name='QA room', data=self.image())
        importer = ResourceImporter(self.service)
        after = importer.publish(request)
        self.assertNotEqual(after.catalog.revision, before.catalog.revision)
        self.assertEqual(after.room, before.room)
        imported = [a for a in after.catalog.backgrounds if a.name == 'QA room']
        self.assertEqual(len(imported), 1)
        self.assertEqual(importer.publish(request).catalog, after.catalog)
        self.assertEqual(self.events[-1]['event']['fields']['equipped'], False)

    def test_invalid_or_opaque_portrait_never_publishes(self):
        importer = ResourceImporter(self.service)
        before = self.service.snapshot(self.identity)
        for data in ('not_base64', self.image(), self.image('RGBA', (255, 255, 255, 255)), self.image('RGBA', (0, 0, 0, 0))):
            request = ImportRequest(identity=self.identity, kind='outfit', name='bad', data=data)
            with self.assertRaises(ValueError):
                importer.publish(request)
        self.assertEqual(self.service.snapshot(self.identity).catalog, before.catalog)

    def test_cancel_exact_request_and_unshown_delivery_projection(self):
        async def scenario():
            started = asyncio.Event()
            async def generate(payload):
                started.set()
                await asyncio.Event().wait()
            service = PresentationService(room=self.service, generate=generate)
            request = TurnRequest(identity=self.identity, request_id='cancel-this-turn', generation=1, message='hello')
            task = asyncio.create_task(service.turn(request))
            await started.wait()
            service.cancel(CancelRequest(identity=self.identity, request_id=request.request_id))
            with self.assertRaises(asyncio.CancelledError):
                await task
            async def complete(_):
                return {'beats': [{'speech': '已看到'}, {'speech': '还没看到'}]}
            service.generate = complete
            p = await service.turn(request.model_copy(update={'request_id': 'completed-turn01'}))
            for kind in ('display_started', 'text_revealed', 'audio_started', 'audio_interrupted'):
                service.receipt(Receipt(identity=self.identity, turn_id=p.turn_id, beat_id=p.beats[0].beat_id, generation=1, kind=kind))
            projection = service.delivery_projection(self.identity)
            self.assertTrue(projection[0]['text_fully_shown'])
            self.assertFalse(projection[0]['audio_fully_played'])
            self.assertFalse(projection[1]['text_fully_shown'])
            self.assertFalse(projection[1]['audio_fully_played'])
        asyncio.run(scenario())

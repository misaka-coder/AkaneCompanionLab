"""Real FFmpeg checks; tones test media policy, not RVC model quality."""

import asyncio
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import wave

from companion_v01.plugin_subprocess import PluginProcessRunner
from plugins.akane_cover_song.src.akane_cover_song import CoverMedia, CoverSongError


def tone(path, *, seconds=1, rate=44100, amplitude=0.1):
    samples = [int(32767 * amplitude * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(seconds * rate))]
    with wave.open(str(path), "wb") as writer:
        writer.setparams((1, 2, rate, len(samples), "NONE", "not compressed"))
        writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_format_is_probed_not_guessed_from_extension(self):
        mislabeled = self.root / "mislabeled.mp3"
        shutil.copyfile(self.source, mislabeled)
        self.assertEqual((await self.media.probe_audio(mislabeled))["container"], "wav")
        with self.assertRaises(CoverSongError) as caught:
            await self.media.probe_output(mislabeled, "mp3")
        self.assertEqual(caught.exception.reason, "cover_output_format_mismatch")

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runner = PluginProcessRunner()
        self.ffmpeg = os.environ.get("AKANE_COVER_TEST_FFMPEG") or shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        self.assertTrue(self.ffmpeg and self.ffprobe, "Real FFmpeg/FFprobe required")
        self.media = CoverMedia(run=self.runner.run, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        self.source = self.root / "tone.wav"
        tone(self.source)

    async def asyncTearDown(self):
        await self.runner.aclose()
        self.assertFalse(self.runner.processes)
        self.tmp.cleanup()

    async def test_decode_and_all_mix_formats_real_audio_gain(self):
        decoded = self.root / "decoded.wav"
        await self.media.decode(source_path=self.source, output_path=decoded)
        with wave.open(str(decoded), "rb") as reader:
            self.assertEqual((reader.getframerate(), reader.getnchannels(), reader.getsampwidth()), (44100, 2, 2))
            raw = reader.readframes(reader.getnframes())
        baseline = struct.unpack(f"<{len(raw) // 2}h", raw)
        baseline_rms = math.sqrt(sum(v * v for v in baseline) / len(baseline)) / 32768
        for fmt in ("wav", "flac", "mp3"):
            mixed = self.root / f"mixed.{fmt}"
            await self.media.mix(
                converted_vocals=decoded,
                instrumental=decoded,
                output_path=mixed,
                output_format=fmt,
                vocal_gain_db=0.0,
                instrumental_gain_db=0.0,
            )
            self.assertAlmostEqual(await self.media.probe_duration(mixed), 1, delta=0.1)
            pcm = self.root / f"measure-{fmt}.wav"
            await self.media.decode(source_path=mixed, output_path=pcm)
            with wave.open(str(pcm), "rb") as reader:
                data = reader.readframes(reader.getnframes())
            values = struct.unpack(f"<{len(data) // 2}h", data)
            rms = math.sqrt(sum(v * v for v in values) / len(values)) / 32768
            # Measure relative to the decoded stereo input, accounting for
            # FFmpeg's mono-to-stereo -3dB pan law.
            self.assertGreater(rms / baseline_rms, 1.9)
            self.assertLess(rms / baseline_rms, 2.2)

    async def test_mono_40khz_voice_preserves_stereo_instrumental(self):
        vocals, backing = self.root / "rvc-mono.wav", self.root / "backing-stereo.wav"
        tone(vocals, rate=40000, amplitude=0.05)
        frames = [
            (int(2500 * math.sin(2 * math.pi * 330 * i / 44100)), int(2500 * math.sin(2 * math.pi * 880 * i / 44100)))
            for i in range(44100)
        ]
        with wave.open(str(backing), "wb") as writer:
            writer.setparams((2, 2, 44100, 44100, "NONE", "not compressed"))
            writer.writeframes(b"".join(struct.pack("<hh", left, right) for left, right in frames))
        for fmt in ("wav", "flac", "mp3"):
            output = self.root / f"stereo-cover.{fmt}"
            await self.media.mix(
                converted_vocals=vocals,
                instrumental=backing,
                output_path=output,
                output_format=fmt,
                vocal_gain_db=0,
                instrumental_gain_db=0,
            )
            info = await self.media.probe_audio(output)
            self.assertEqual((info["sample_rate"], info["channels"]), (44100, 2))
            pcm = self.root / f"stereo-measure-{fmt}.wav"
            await self.media.decode(source_path=output, output_path=pcm)
            with wave.open(str(pcm), "rb") as reader:
                raw = reader.readframes(reader.getnframes())
            values = struct.unpack(f"<{len(raw) // 2}h", raw)
            differences = [left - right for left, right in zip(values[::2], values[1::2])]
            # Upmixing an already folded mono result would produce identical
            # channels; actual backing stereo must survive before amix.
            self.assertGreater(math.sqrt(sum(v * v for v in differences) / len(differences)), 1500)

    async def test_unequal_tracks_do_not_double_remaining_tail(self):
        short = self.root / "short.wav"
        tone(short, seconds=0.5)
        mixed = self.root / "unequal.flac"
        await self.media.mix(
            converted_vocals=short,
            instrumental=self.source,
            output_path=mixed,
            output_format="flac",
            vocal_gain_db=0,
            instrumental_gain_db=0,
        )
        pcm = self.root / "unequal-pcm.wav"
        await self.media.decode(source_path=mixed, output_path=pcm)
        with wave.open(str(pcm), "rb") as reader:
            raw = reader.readframes(reader.getnframes())
        values = struct.unpack(f"<{len(raw) // 2}h", raw)[::2]
        head, tail = values[4410:17640], values[26460:39690]
        ratio = math.sqrt(sum(v * v for v in head) / len(head)) / math.sqrt(sum(v * v for v in tail) / len(tail))
        self.assertAlmostEqual(ratio, 2.0, delta=0.05)

    async def test_audio_duration_in_longer_video_and_invalid_sources(self):
        video = self.root / "long-video.mp4"
        code, _ = await self.runner.run(
            [
                self.ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=s=32x32:d=3",
                "-i",
                self.source,
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                video,
            ]
        )
        self.assertEqual(code, 0)
        self.assertAlmostEqual(await self.media.probe_duration(video), 1, delta=0.1)
        bad = self.root / "invalid.wav"
        bad.write_bytes(b"not audio")
        with self.assertRaises(CoverSongError) as caught:
            await self.media.probe_duration(bad)
        self.assertEqual(caught.exception.reason, "cover_media_invalid")
        missing = CoverMedia(run=self.runner.run, ffmpeg=self.ffmpeg)
        with self.assertRaises(CoverSongError) as caught:
            await missing.probe_duration(self.source)
        self.assertEqual(caught.exception.reason, "ffprobe_not_found")

    async def test_reject_existing_output_and_nonfinite_gain(self):
        original = self.source.read_bytes()
        with self.assertRaises(CoverSongError) as caught:
            await self.media.decode(source_path=self.source, output_path=self.source)
        self.assertEqual(caught.exception.reason, "cover_output_already_exists")
        self.assertEqual(self.source.read_bytes(), original)
        for gain in (float("nan"), float("inf"), -13, True):
            with self.assertRaises(CoverSongError) as caught:
                await self.media.mix(
                    converted_vocals=self.source,
                    instrumental=self.source,
                    output_path=self.root / "bad.wav",
                    output_format="wav",
                    vocal_gain_db=gain,
                    instrumental_gain_db=0,
                )
            self.assertEqual(caught.exception.reason, "cover_gain_invalid")
        self.assertFalse((self.root / "bad.wav").exists())

    async def test_cancel_actual_ffmpeg_drains_child(self):
        async def slow_run(argv, **kwargs):
            # Read the real WAV in realtime so cancellation reliably catches
            # an active FFmpeg rather than a completed millisecond conversion.
            return await self.runner.run([argv[0], "-re", *argv[1:]], **kwargs)

        media = CoverMedia(run=slow_run, ffmpeg=self.ffmpeg)
        task = asyncio.create_task(media.decode(source_path=self.source, output_path=self.root / "cancel.wav"))
        for _ in range(200):
            if self.runner.processes:
                break
            await asyncio.sleep(0.01)
        children = tuple(self.runner.processes)
        self.assertTrue(children)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.runner.processes)
        self.assertTrue(all(child.returncode is not None for child in children))

    async def test_public_pipeline_and_local_service_use_same_real_media_policy(self):
        from scripts.akane_local_capability_host import _cover_media

        decoded = self.root / "host-decoded.wav"
        await self.media.decode(source_path=self.source, output_path=decoded)
        self.assertAlmostEqual(await self.media.probe_duration(decoded), 1, delta=0.01)
        host_mix, local_mix = self.root / "host.wav", self.root / "local.wav"
        params = dict(
            converted_vocals=decoded,
            instrumental=decoded,
            output_format="wav",
            vocal_gain_db=0,
            instrumental_gain_db=-1,
        )
        await self.media.mix(output_path=host_mix, **params)
        await _cover_media(Path(self.ffmpeg), self.runner).mix(output_path=local_mix, **params)
        self.assertEqual(host_mix.read_bytes(), local_mix.read_bytes())
        self.assertAlmostEqual(await self.media.probe_duration(host_mix), 1, delta=0.01)


if __name__ == "__main__":
    unittest.main()

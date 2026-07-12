from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from .generated_files_media import build_generated_media_info_projection


_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
_PROTECTED_MEDIA_EXTENSIONS = {"kgm", "mflac", "mgg", "ncm", "qmc", "qmc0", "qmc3", "tkm"}
_PIPELINE_VERSION = "rvc-cover-v1"


class CoverSongError(RuntimeError):
    def __init__(self, *, stage: str, reason: str, public_message: str) -> None:
        super().__init__(reason)
        self.stage = str(stage or "unknown")
        self.reason = str(reason or "cover_song_failed")
        self.public_message = str(public_message or "翻唱处理失败了。").strip()


class RvcWebUiProvider:
    """Local RVC WebUI adapter using its named Gradio dependencies."""

    provider_id = "rvc_webui"
    _locks_guard = threading.RLock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(
        self,
        *,
        base_url: str,
        root_dir: str | Path = "",
        timeout_seconds: float = 1800.0,
        separation_model: str = "HP5_only_main_vocal",
    ) -> None:
        normalized_url = str(base_url or "http://127.0.0.1:7899").strip().rstrip("/")
        parsed = urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("RVC endpoint must be localhost in V1.")
        self.base_url = normalized_url
        self.root_dir = Path(root_dir).resolve() if str(root_dir or "").strip() else None
        self.timeout_seconds = max(30.0, min(7200.0, float(timeout_seconds or 1800.0)))
        self.separation_model = str(separation_model or "HP5_only_main_vocal").strip()
        self._config_cache: tuple[float, dict[str, Any]] | None = None
        with self._locks_guard:
            self._operation_lock = self._locks.setdefault(self.base_url, threading.RLock())

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        with self._operation_lock:
            yield

    def capability_status(self) -> dict[str, Any]:
        try:
            models = self.list_voice_models()
        except Exception:
            return {"enabled": False, "status": "unavailable", "reason": "rvc_webui_unreachable"}
        if not models:
            return {"enabled": False, "status": "missing_model", "reason": "rvc_voice_models_missing"}
        return {"enabled": True, "status": "ready", "reason": ""}

    def list_voice_models(self) -> list[str]:
        config = self._load_config()
        dependency = self._dependency(config, "infer_change_voice")
        components = self._components_by_id(config)
        for component_id in dependency.get("inputs") or []:
            component = components.get(str(component_id)) or {}
            props = component.get("props") if isinstance(component.get("props"), dict) else {}
            label = str(props.get("label") or "").lower()
            if component.get("type") == "dropdown" and ("音色" in label or "voice" in label):
                return self._choice_values(props.get("choices"))
        return []

    def resolve_voice_model(self, requested: str, *, default_model: str = "") -> str:
        models = self.list_voice_models()
        raw = str(requested or "").strip()
        if not raw or raw.lower() in {"auto", "default", "默认", "自动"}:
            raw = str(default_model or "").strip() or (models[0] if models else "")
        if not raw:
            raise CoverSongError(
                stage="voice_model",
                reason="voice_model_missing",
                public_message="本机 RVC 暂时没有可用的目标音色模型。",
            )
        lowered = raw.lower()
        exact = [
            item for item in models if item.lower() == lowered or Path(item).stem.lower() == Path(raw).stem.lower()
        ]
        if len(exact) == 1:
            return exact[0]
        normalized = self._normalize_model_key(raw)
        fuzzy = [item for item in models if normalized and normalized in self._normalize_model_key(item)]
        if len(fuzzy) == 1:
            return fuzzy[0]
        if len(fuzzy) > 1:
            names = "、".join(fuzzy[:6])
            raise CoverSongError(
                stage="voice_model",
                reason="voice_model_ambiguous",
                public_message=f"目标音色名称不够明确，可匹配到：{names}。请指定其中一个。",
            )
        names = "、".join(models[:8])
        raise CoverSongError(
            stage="voice_model",
            reason="voice_model_not_found",
            public_message=f"没有找到目标 RVC 音色“{raw}”。当前可用音色包括：{names}。",
        )

    def model_fingerprint(self, model_name: str) -> dict[str, Any]:
        output: dict[str, Any] = {"model": str(model_name or "")}
        if self.root_dir is None:
            return output
        model_path = self.root_dir / "assets" / "weights" / Path(model_name).name
        output["weight"] = self._stat_fingerprint(model_path)
        stem_key = self._normalize_model_key(Path(model_name).stem)
        index_items: list[dict[str, Any]] = []
        for base in (self.root_dir / "assets" / "indices", self.root_dir / "logs"):
            if not base.exists():
                continue
            for path in base.rglob("*.index"):
                if stem_key and stem_key.split("test")[0] in self._normalize_model_key(path.name):
                    index_items.append({"name": path.name, **self._stat_fingerprint(path)})
        output["indices"] = sorted(index_items, key=lambda item: str(item.get("name") or ""))[:12]
        return output

    def separate_vocals(self, *, source_path: Path, work_dir: Path) -> tuple[Path, Path]:
        last_reason = "rvc_uvr_failed"
        max_attempts = 3
        for attempt in range(max_attempts):
            attempt_dir = work_dir / f"uvr_attempt_{attempt + 1}"
            input_dir = attempt_dir / "input"
            vocals_dir = attempt_dir / "vocals"
            instrumental_dir = attempt_dir / "instrumental"
            for directory in (input_dir, vocals_dir, instrumental_dir):
                directory.mkdir(parents=True, exist_ok=True)
            unique_stem = f"cover_{uuid.uuid4().hex[:12]}"
            staged_input = input_dir / f"{unique_stem}{source_path.suffix.lower() or '.wav'}"
            shutil.copy2(source_path, staged_input)
            config = self._load_config(force=True)
            data = self._build_api_inputs(
                config,
                "uvr_convert",
                {
                    "model": self.separation_model,
                    "input_dir": str(input_dir),
                    "vocals_dir": str(vocals_dir),
                    "instrumental_dir": str(instrumental_dir),
                    "aggressiveness": 10,
                    "output_format": "wav",
                },
            )
            response = self._post_predict(config, "uvr_convert", data)
            message = str((response.get("data") or [""])[0] or "")
            if "success" in message.lower():
                vocals = self._wait_for_audio_file(vocals_dir)
                instrumental = self._wait_for_audio_file(instrumental_dir)
                if vocals is not None and instrumental is not None:
                    return vocals, instrumental
                last_reason = "separation_outputs_missing"
            if attempt + 1 < max_attempts:
                time.sleep(0.5)
        if last_reason == "separation_outputs_missing":
            raise CoverSongError(
                stage="separation",
                reason=last_reason,
                public_message="分离流程已经结束，但没有得到完整的人声和伴奏文件。",
            )
        raise CoverSongError(
            stage="separation",
            reason=last_reason,
            public_message="本机分离模型重试后仍没有成功拆出主唱和伴奏。",
        )

    def convert_voice(
        self,
        *,
        source_path: Path,
        output_path: Path,
        model_name: str,
        pitch_shift: int,
        index_rate: float,
        filter_radius: int,
        rms_mix_rate: float,
        protect: float,
    ) -> dict[str, Any]:
        config = self._load_config(force=True)
        change_data = self._build_api_inputs(
            config,
            "infer_change_voice",
            {"model": model_name, "protect": protect},
        )
        change_response = self._post_predict(config, "infer_change_voice", change_data)
        index_path = self._extract_change_voice_index(config, change_response)
        infer_data = self._build_api_inputs(
            config,
            "infer_convert",
            {
                "speaker_id": 0,
                "source_path": str(source_path),
                "pitch_shift": int(pitch_shift),
                "f0_file": None,
                "f0_method": "rmvpe",
                "index_path": index_path,
                "index_rate": float(index_rate),
                "filter_radius": int(filter_radius),
                "resample_rate": 0,
                "rms_mix_rate": float(rms_mix_rate),
                "protect": float(protect),
            },
        )
        response = self._post_predict(config, "infer_convert", infer_data)
        payload = list(response.get("data") or [])
        info = str(payload[0] if payload else "")
        if "success" not in info.lower() or len(payload) < 2:
            raise CoverSongError(
                stage="voice_conversion",
                reason="rvc_inference_failed",
                public_message="RVC 没有成功完成人声音色转换。",
            )
        audio = payload[1]
        result_path = Path(str(audio.get("name") if isinstance(audio, dict) else audio or ""))
        if not result_path.exists() or not result_path.is_file() or result_path.suffix.lower() not in _AUDIO_SUFFIXES:
            raise CoverSongError(
                stage="voice_conversion",
                reason="rvc_output_missing",
                public_message="RVC 返回了成功状态，但转换后的音频文件不可用。",
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result_path, output_path)
        return {"index_path": index_path, "info": info}

    def _load_config(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._config_cache is not None and now - self._config_cache[0] < 30.0:
            return self._config_cache[1]
        try:
            response = requests.get(f"{self.base_url}/config", timeout=min(20.0, self.timeout_seconds))
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CoverSongError(
                stage="provider",
                reason="rvc_webui_unreachable",
                public_message="本机 RVC 服务暂时无法连接，请确认 7899 端口的 RVC WebUI 已启动。",
            ) from exc
        if not isinstance(payload, dict):
            raise CoverSongError(
                stage="provider",
                reason="rvc_config_invalid",
                public_message="本机 RVC 服务返回了无法识别的配置。",
            )
        self._config_cache = (now, payload)
        return payload

    def _post_predict(self, config: dict[str, Any], api_name: str, data: list[Any]) -> dict[str, Any]:
        dependency_index = self._dependency_index(config, api_name)
        try:
            response = requests.post(
                f"{self.base_url}/api/predict",
                json={"fn_index": dependency_index, "data": data},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CoverSongError(
                stage="provider",
                reason=f"rvc_{api_name}_request_failed",
                public_message="本机 RVC 服务调用失败，现有输入和缓存没有被删除。",
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise CoverSongError(
                stage="provider",
                reason=f"rvc_{api_name}_response_invalid",
                public_message="本机 RVC 服务返回了无法识别的结果。",
            )
        return payload

    def _build_api_inputs(self, config: dict[str, Any], api_name: str, values: dict[str, Any]) -> list[Any]:
        dependency = self._dependency(config, api_name)
        components = self._components_by_id(config)
        output: list[Any] = []
        for component_id in dependency.get("inputs") or []:
            component = components.get(str(component_id)) or {}
            props = component.get("props") if isinstance(component.get("props"), dict) else {}
            label = str(props.get("label") or "").lower()
            component_type = str(component.get("type") or "")
            default = props.get("value")
            value = default
            if api_name == "infer_change_voice":
                if component_type == "dropdown" and ("音色" in label or "voice" in label):
                    value = values.get("model")
                elif "保护" in label:
                    value = values.get("protect", default)
            elif api_name == "infer_convert":
                if "说话人" in label or "speaker" in label:
                    value = values.get("speaker_id", 0)
                elif "待处理音频" in label or "audio file path" in label:
                    value = values.get("source_path")
                elif "变调" in label or "semitone" in label:
                    value = values.get("pitch_shift", 0)
                elif "f0曲线" in label or "f0 curve" in label:
                    value = values.get("f0_file")
                elif "音高提取" in label or "pitch extraction" in label:
                    value = values.get("f0_method", "rmvpe")
                elif component_type == "textbox" and "检索库" in label:
                    value = ""
                elif component_type == "dropdown" and "index" in label:
                    value = values.get("index_path", "")
                elif "检索特征占比" in label:
                    value = values.get("index_rate", default)
                elif "中值滤波" in label:
                    value = values.get("filter_radius", default)
                elif "重采样" in label:
                    value = values.get("resample_rate", 0)
                elif "音量包络" in label:
                    value = values.get("rms_mix_rate", default)
                elif "保护" in label:
                    value = values.get("protect", default)
            elif api_name == "uvr_convert":
                if component_type == "dropdown" and "模型" in label:
                    value = values.get("model")
                elif component_type == "textbox" and "输入待处理音频文件夹" in label:
                    value = values.get("input_dir")
                elif component_type == "textbox" and ("非主人声文件夹" in label or "instrumental" in label):
                    value = values.get("instrumental_dir")
                elif component_type == "textbox" and ("主人声文件夹" in label or "vocals" in label):
                    value = values.get("vocals_dir")
                elif component_type == "file":
                    value = None
                elif "激进程度" in label or "aggressiveness" in label:
                    value = values.get("aggressiveness", default)
                elif "导出文件格式" in label or "output format" in label:
                    value = values.get("output_format", "wav")
            output.append(value)
        return output

    def _extract_change_voice_index(self, config: dict[str, Any], response: dict[str, Any]) -> str:
        dependency = self._dependency(config, "infer_change_voice")
        components = self._components_by_id(config)
        data = list(response.get("data") or [])
        for position, component_id in enumerate(dependency.get("outputs") or []):
            component = components.get(str(component_id)) or {}
            props = component.get("props") if isinstance(component.get("props"), dict) else {}
            label = str(props.get("label") or "").lower()
            if component.get("type") != "dropdown" or "index" not in label or position >= len(data):
                continue
            item = data[position]
            return str(item.get("value") if isinstance(item, dict) else item or "").strip()
        return ""

    def _dependency(self, config: dict[str, Any], api_name: str) -> dict[str, Any]:
        dependencies = config.get("dependencies") if isinstance(config.get("dependencies"), list) else []
        for dependency in dependencies:
            if isinstance(dependency, dict) and str(dependency.get("api_name") or "") == api_name:
                return dependency
        raise CoverSongError(
            stage="provider",
            reason=f"rvc_api_missing_{api_name}",
            public_message=f"当前 RVC WebUI 没有暴露 {api_name} 能力。",
        )

    def _dependency_index(self, config: dict[str, Any], api_name: str) -> int:
        dependencies = config.get("dependencies") if isinstance(config.get("dependencies"), list) else []
        for index, dependency in enumerate(dependencies):
            if isinstance(dependency, dict) and str(dependency.get("api_name") or "") == api_name:
                return index
        self._dependency(config, api_name)
        return -1

    def _components_by_id(self, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        components = config.get("components") if isinstance(config.get("components"), list) else []
        return {str(item.get("id")): item for item in components if isinstance(item, dict)}

    def _first_audio_file(self, directory: Path) -> Path | None:
        files = [path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in _AUDIO_SUFFIXES]
        return sorted(files, key=lambda path: path.name.lower())[0] if files else None

    def _wait_for_audio_file(self, directory: Path, *, timeout_seconds: float = 10.0) -> Path | None:
        deadline = time.time() + max(0.1, float(timeout_seconds))
        while time.time() < deadline:
            candidate = self._first_audio_file(directory)
            if candidate is not None and candidate.stat().st_size > 0:
                return candidate
            time.sleep(0.1)
        return self._first_audio_file(directory)

    def _choice_values(self, value: Any) -> list[str]:
        output: list[str] = []
        for item in value if isinstance(value, list) else []:
            candidate = item[0] if isinstance(item, (list, tuple)) and item else item
            text = str(candidate or "").strip()
            if text and text not in output:
                output.append(text)
        return output

    def _normalize_model_key(self, value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())

    def _stat_fingerprint(self, path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError:
            return {"missing": True}
        return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


class CoverSongService:
    def __init__(
        self,
        *,
        generated_file_service: Any,
        provider: RvcWebUiProvider,
        cache_root: str | Path,
        default_model: str = "",
        default_output_format: str = "mp3",
        default_delivery: str = "auto",
        max_duration_seconds: float = 900.0,
        max_input_bytes: int = 256 * 1024 * 1024,
        ffmpeg_path: str = "",
        ffprobe_path: str = "",
    ) -> None:
        self.generated_file_service = generated_file_service
        self.provider = provider
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.default_model = str(default_model or "").strip()
        self.default_output_format = self._normalize_output_format(default_output_format)
        self.default_delivery = self._normalize_delivery(default_delivery)
        self.max_duration_seconds = max(15.0, min(3600.0, float(max_duration_seconds or 900.0)))
        self.max_input_bytes = max(1024 * 1024, int(max_input_bytes or 0))
        self.ffmpeg_path = str(ffmpeg_path or shutil.which("ffmpeg") or "").strip()
        self.ffprobe_path = str(ffprobe_path or shutil.which("ffprobe") or "").strip()
        self._cache_lock = threading.RLock()

    def capability_status(self) -> dict[str, Any]:
        if not self.ffmpeg_path:
            return {"enabled": False, "status": "missing_executor", "reason": "ffmpeg_not_found"}
        return self.provider.capability_status()

    def list_voice_models(self) -> list[str]:
        return self.provider.list_voice_models()

    def cover_song(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_target: str = "",
        song_title: str = "",
        artist: str = "",
        voice_model: str = "auto",
        pitch_shift: int = 0,
        index_rate: float = 0.6,
        filter_radius: int = 3,
        rms_mix_rate: float = 0.25,
        protect: float = 0.33,
        vocal_gain_db: float = 0.0,
        instrumental_gain_db: float = -1.0,
        output_format: str = "",
        delivery: str = "auto",
        force_rebuild: bool = False,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_format = self._normalize_output_format(output_format or self.default_output_format)
        normalized_delivery = self._normalize_delivery(delivery or self.default_delivery)
        model_name = self.provider.resolve_voice_model(voice_model, default_model=self.default_model)
        params = {
            "pitch_shift": max(-24, min(24, int(pitch_shift or 0))),
            "index_rate": self._bounded_float(index_rate, 0.0, 1.0, 0.6),
            "filter_radius": max(0, min(7, int(filter_radius or 0))),
            "rms_mix_rate": self._bounded_float(rms_mix_rate, 0.0, 1.0, 0.25),
            "protect": self._bounded_float(protect, 0.0, 0.5, 0.33),
            "vocal_gain_db": self._bounded_float(vocal_gain_db, -12.0, 12.0, 0.0),
            "instrumental_gain_db": self._bounded_float(instrumental_gain_db, -12.0, 6.0, -1.0),
        }
        clean_title = self._clean_label(song_title, 120)
        clean_artist = self._clean_label(artist, 80)

        if not str(source_target or "").strip():
            return self._restore_cached_cover(
                profile_user_id=profile_user_id,
                session_id=session_id,
                song_title=clean_title,
                artist=clean_artist,
                model_name=model_name,
                output_format=normalized_format,
                delivery=normalized_delivery,
                timestamp=effective_ts,
            )

        source = self.generated_file_service._resolve_media_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=source_target,
        )
        if source is None:
            return self._failure("source", "source_not_found", "没有找到要翻唱的音频或视频材料。")
        source_path = Path(source.get("absolute_path") or "")
        if not source_path.exists() or not source_path.is_file():
            return self._failure("source", "source_file_missing", "要翻唱的来源文件已经不在本地工作区。")
        input_ext = source_path.suffix.lower().lstrip(".")
        if input_ext in _PROTECTED_MEDIA_EXTENSIONS:
            return self._failure(
                "source",
                "protected_media_format",
                "这个来源属于平台加密或专有缓存格式，请提供普通 MP3、FLAC、WAV、M4A 或视频文件。",
            )
        if source_path.stat().st_size > self.max_input_bytes:
            return self._failure("source", "input_too_large", "这份音频超过当前翻唱任务允许的文件大小。")
        duration = self._probe_duration(source_path)
        if duration > self.max_duration_seconds:
            return self._failure(
                "source",
                "duration_too_long",
                f"这首音频约 {duration / 60:.1f} 分钟，超过当前 {self.max_duration_seconds / 60:.1f} 分钟的处理上限。",
            )

        inferred_title = self._clean_label(str(source.get("title") or source_path.stem), 120)
        clean_title = clean_title or inferred_title or "未命名歌曲"
        source_hash = self._sha256_file(source_path)
        cache_payload = {
            "pipeline": _PIPELINE_VERSION,
            "source_sha256": source_hash,
            "provider": self.provider.provider_id,
            "model_fingerprint": self.provider.model_fingerprint(model_name),
            "separation_model": self.provider.separation_model,
            "voice_model": model_name,
            "params": params,
            "output_format": normalized_format,
        }
        cache_key = hashlib.sha256(
            json.dumps(cache_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_dir = self._profile_cache_dir(profile_user_id) / cache_key
        cached_output = cache_dir / f"cover.{normalized_format}"
        manifest_path = cache_dir / "manifest.json"
        if not force_rebuild and self._valid_cached_output(cached_output, manifest_path, cache_key):
            manifest = self._read_json(manifest_path)
            return self._publish_generated(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source=source,
                cached_output=cached_output,
                song_title=clean_title or str(manifest.get("song_title") or ""),
                artist=clean_artist or str(manifest.get("artist") or ""),
                model_name=model_name,
                params=params,
                output_format=normalized_format,
                delivery=normalized_delivery,
                cache_key=cache_key,
                cache_hit=True,
                timestamp=effective_ts,
            )

        job_dir = self.generated_file_service.work_dir / "_cover_song_tmp" / uuid.uuid4().hex
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            prepared = job_dir / "source.wav"
            self._decode_source(source_path=source_path, output_path=prepared)
            converted_vocals = job_dir / "converted_vocals.wav"
            with self.provider.exclusive():
                vocals, instrumental = self.provider.separate_vocals(source_path=prepared, work_dir=job_dir)
                conversion = self.provider.convert_voice(
                    source_path=vocals,
                    output_path=converted_vocals,
                    model_name=model_name,
                    pitch_shift=params["pitch_shift"],
                    index_rate=params["index_rate"],
                    filter_radius=params["filter_radius"],
                    rms_mix_rate=params["rms_mix_rate"],
                    protect=params["protect"],
                )
            mixed_output = job_dir / f"cover.{normalized_format}"
            self._mix_tracks(
                converted_vocals=converted_vocals,
                instrumental=instrumental,
                output_path=mixed_output,
                output_format=normalized_format,
                vocal_gain_db=params["vocal_gain_db"],
                instrumental_gain_db=params["instrumental_gain_db"],
            )
            if not mixed_output.exists() or mixed_output.stat().st_size <= 0:
                raise CoverSongError(
                    stage="mix",
                    reason="cover_output_missing",
                    public_message="翻唱混音流程结束了，但没有生成可用的成品音频。",
                )
            manifest = {
                "cache_key": cache_key,
                "pipeline": _PIPELINE_VERSION,
                "song_title": clean_title,
                "artist": clean_artist,
                "voice_model": model_name,
                "source_sha256": source_hash,
                "output_format": normalized_format,
                "params": params,
                "index_name": Path(str(conversion.get("index_path") or "")).name,
                "created_at": effective_ts,
            }
            with self._cache_lock:
                cache_dir.mkdir(parents=True, exist_ok=True)
                self._atomic_copy(mixed_output, cached_output)
                self._atomic_write_json(manifest_path, manifest)
            return self._publish_generated(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source=source,
                cached_output=cached_output,
                song_title=clean_title,
                artist=clean_artist,
                model_name=model_name,
                params=params,
                output_format=normalized_format,
                delivery=normalized_delivery,
                cache_key=cache_key,
                cache_hit=False,
                timestamp=effective_ts,
            )
        except CoverSongError as exc:
            return self._failure(exc.stage, exc.reason, exc.public_message)
        except Exception:
            return self._failure("pipeline", "cover_song_failed", "翻唱流程遇到了未预期错误，输入和已有缓存仍然保留。")
        finally:
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)

    def _restore_cached_cover(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        song_title: str,
        artist: str,
        model_name: str,
        output_format: str,
        delivery: str,
        timestamp: int,
    ) -> dict[str, Any]:
        if not song_title:
            return self._failure("cache", "source_or_title_required", "请提供歌曲材料，或者说出已经翻唱过的歌曲名。")
        matches: list[tuple[dict[str, Any], Path]] = []
        profile_dir = self._profile_cache_dir(profile_user_id)
        if profile_dir.exists():
            target_title = self._normalize_lookup(song_title)
            target_artist = self._normalize_lookup(artist)
            for manifest_path in profile_dir.glob("*/manifest.json"):
                manifest = self._read_json(manifest_path)
                if self._normalize_lookup(manifest.get("song_title")) != target_title:
                    continue
                if target_artist and self._normalize_lookup(manifest.get("artist")) != target_artist:
                    continue
                if str(manifest.get("voice_model") or "").lower() != model_name.lower():
                    continue
                if str(manifest.get("output_format") or "").lower() != output_format:
                    continue
                output = manifest_path.parent / f"cover.{output_format}"
                if output.exists() and output.stat().st_size > 0:
                    matches.append((manifest, output))
        if not matches:
            return self._failure("cache", "cached_cover_not_found", f"还没有找到《{song_title}》的现成翻唱缓存。")
        if len(matches) > 1 and not artist:
            artists = sorted({str(item[0].get("artist") or "未知原唱") for item in matches})
            if len(artists) > 1:
                return self._failure(
                    "cache",
                    "cached_cover_ambiguous",
                    f"《{song_title}》有多个缓存版本，请补充原唱：{'、'.join(artists[:6])}。",
                )
        manifest, cached_output = sorted(matches, key=lambda item: int(item[0].get("created_at") or 0), reverse=True)[0]
        source = {
            "source_type": "cover_cache",
            "source_id": f"cover_cache::{manifest.get('cache_key')}",
            "handle": "",
            "title": str(manifest.get("song_title") or song_title),
            "extra_source_ids": [],
        }
        return self._publish_generated(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source=source,
            cached_output=cached_output,
            song_title=str(manifest.get("song_title") or song_title),
            artist=str(manifest.get("artist") or artist),
            model_name=model_name,
            params=dict(manifest.get("params") or {}),
            output_format=output_format,
            delivery=delivery,
            cache_key=str(manifest.get("cache_key") or cached_output.parent.name),
            cache_hit=True,
            timestamp=timestamp,
        )

    def _publish_generated(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source: dict[str, Any],
        cached_output: Path,
        song_title: str,
        artist: str,
        model_name: str,
        params: dict[str, Any],
        output_format: str,
        delivery: str,
        cache_key: str,
        cache_hit: bool,
        timestamp: int,
    ) -> dict[str, Any]:
        model_label = Path(model_name).stem
        title = self.generated_file_service._normalize_title(f"{song_title}_{model_label}_翻唱") or "Akane翻唱"
        output_path = self.generated_file_service._build_output_path(
            profile_user_id=profile_user_id,
            session_id=session_id,
            title=title,
            output_format=output_format,
            timestamp=timestamp,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._link_or_copy(cached_output, output_path)
        source_ids = [str(source.get("source_id") or "").strip()]
        source_ids.extend(str(item or "").strip() for item in list(source.get("extra_source_ids") or []))
        source_ids = [item for item in source_ids if item]
        summary = f"《{song_title}》的 {model_label} RVC 翻唱成品"
        if artist:
            summary += f"（原唱：{artist}）"
        content_card = {
            "type": "cover_song",
            "summary": summary + ("，已命中缓存。" if cache_hit else "。"),
            "source": {
                "source_type": str(source.get("source_type") or ""),
                "source_id": str(source.get("source_id") or ""),
                "handle": str(source.get("handle") or ""),
                "title": str(source.get("title") or song_title),
            },
            "cover": {
                "song_title": song_title,
                "artist": artist,
                "provider": self.provider.provider_id,
                "voice_model": model_name,
                "pitch_shift": int(params.get("pitch_shift") or 0),
                "output_format": output_format,
                "cache_hit": bool(cache_hit),
                "cache_key_prefix": cache_key[:12],
                "pipeline_version": _PIPELINE_VERSION,
            },
        }
        content_card["media_info"] = build_generated_media_info_projection(
            self.generated_file_service,
            output_path=output_path,
            output_format=output_format,
            source=source,
            hints={"file_size": output_path.stat().st_size},
        )
        generated = self.generated_file_service.store.add_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            output_title=title,
            output_format=output_format,
            storage_relpath=self.generated_file_service._storage_relpath(output_path),
            mime_type=self.generated_file_service._mime_type_for_format(output_format),
            file_ext=output_format,
            file_size=output_path.stat().st_size,
            source_ids=source_ids,
            content_card=content_card,
            summary=str(content_card.get("summary") or ""),
            created_by_tool="cover_song",
            delivery_status="pending" if delivery != "none" else "not_requested",
            timestamp=timestamp,
        )
        generated["absolute_path"] = str(self.generated_file_service.absolute_path(generated))
        return {
            "ok": True,
            "generated": generated,
            "send_to_user": delivery != "none",
            "delivery_mode": delivery,
            "cache_hit": bool(cache_hit),
            "followup_context": (
                f"你刚刚已经完成《{song_title}》的 RVC 翻唱，结果是 {generated.get('generated_handle')}。"
                + ("这次命中了现成缓存，没有重复推理。" if cache_hit else "这次完成了人声分离、音色转换和重新混音。")
                + (f"交付方式是 {delivery}。" if delivery != "none" else "结果暂时只保留在生成区，没有主动发送。")
            ),
        }

    def _decode_source(self, *, source_path: Path, output_path: Path) -> None:
        if not self.ffmpeg_path:
            raise CoverSongError(stage="decode", reason="ffmpeg_not_found", public_message="本机没有找到 FFmpeg。")
        completed = subprocess.run(
            [
                self.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=min(self.provider.timeout_seconds, 900.0),
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            raise CoverSongError(
                stage="decode",
                reason="audio_decode_failed",
                public_message="无法把这份来源转换为翻唱流程需要的普通音频格式。",
            )

    def _mix_tracks(
        self,
        *,
        converted_vocals: Path,
        instrumental: Path,
        output_path: Path,
        output_format: str,
        vocal_gain_db: float,
        instrumental_gain_db: float,
    ) -> None:
        codec_args: list[str]
        if output_format == "mp3":
            codec_args = ["-c:a", "libmp3lame", "-b:a", "320k"]
        elif output_format == "flac":
            codec_args = ["-c:a", "flac"]
        else:
            codec_args = ["-c:a", "pcm_s24le"]
        filter_complex = (
            f"[0:a]volume={vocal_gain_db:.3f}dB[v];"
            f"[1:a]volume={instrumental_gain_db:.3f}dB[i];"
            "[v][i]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95:attack=5:release=50[m]"
        )
        completed = subprocess.run(
            [
                self.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(converted_vocals),
                "-i",
                str(instrumental),
                "-filter_complex",
                filter_complex,
                "-map",
                "[m]",
                *codec_args,
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=min(self.provider.timeout_seconds, 1200.0),
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            raise CoverSongError(
                stage="mix",
                reason="audio_mix_failed",
                public_message="转换后的人声已经生成，但最后与伴奏混音失败了。",
            )

    def _probe_duration(self, path: Path) -> float:
        if not self.ffprobe_path:
            return 0.0
        completed = subprocess.run(
            [
                self.ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            return max(0.0, float(str(completed.stdout or "0").strip()))
        except Exception:
            return 0.0

    def _valid_cached_output(self, output: Path, manifest: Path, cache_key: str) -> bool:
        if not output.exists() or not output.is_file() or output.stat().st_size <= 0 or not manifest.exists():
            return False
        return str(self._read_json(manifest).get("cache_key") or "") == cache_key

    def _profile_cache_dir(self, profile_user_id: str) -> Path:
        safe_profile = self.generated_file_service._safe_filename(profile_user_id or "profile")[:80] or "profile"
        return self.cache_root / safe_profile

    def _atomic_copy(self, source: Path, destination: Path) -> None:
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _link_or_copy(self, source: Path, destination: Path) -> None:
        if destination.exists():
            destination.unlink()
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "mp3").strip().lower().lstrip(".")
        aliases = {"wave": "wav", "mpeg3": "mp3"}
        text = aliases.get(text, text)
        return text if text in {"mp3", "flac", "wav"} else "mp3"

    def _normalize_delivery(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        aliases = {"qq_voice": "voice", "audio": "voice", "send": "auto", "不发送": "none"}
        text = aliases.get(text, text)
        return text if text in {"auto", "voice", "file", "both", "none"} else "auto"

    def _bounded_float(self, value: Any, lower: float, upper: float, default: float) -> float:
        try:
            parsed = float(value)
        except Exception:
            parsed = default
        return max(lower, min(upper, parsed))

    def _clean_label(self, value: Any, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text[:limit]

    def _normalize_lookup(self, value: Any) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())

    def _failure(self, stage: str, reason: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "generated": None,
            "stage": str(stage or "unknown"),
            "error": str(reason or "cover_song_failed"),
            "followup_context": f"这次翻唱没有完成：{message}请根据现有信息自然告诉用户，不要假装已生成文件。",
        }

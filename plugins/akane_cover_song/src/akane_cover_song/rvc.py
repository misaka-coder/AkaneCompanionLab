from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from .errors import CoverSongError
from .lease import EndpointLease, check_cancelled
from .models import endpoint_namespace, matching_models, normalize_model_key
from .cache import cache_key

_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}


def exclusive_operation(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.exclusive():
            check_cancelled(self.cancelled)
            return method(self, *args, **kwargs)

    return call


class RvcWebUiProvider:
    """Local RVC WebUI adapter using its named Gradio dependencies."""

    provider_id = "rvc_webui"

    def __init__(
        self,
        *,
        base_url: str,
        root_dir: str | Path = "",
        timeout_seconds: float = 1800.0,
        separation_model: str = "HP5_only_main_vocal",
        state_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        cancelled=lambda: False,
    ) -> None:
        normalized_url = str(base_url or "http://127.0.0.1:7899").strip().rstrip("/")
        parsed = urlparse(normalized_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("RVC endpoint must be localhost in V1.")
        self.base_url = normalized_url
        self.root_dir = Path(root_dir).resolve() if str(root_dir or "").strip() else None
        self.output_dir = (
            Path(output_dir).resolve() if output_dir else (self.root_dir / "TEMP" if self.root_dir else None)
        )
        self.timeout_seconds = max(30.0, min(7200.0, float(timeout_seconds or 1800.0)))
        self.separation_model = str(separation_model or "HP5_only_main_vocal").strip()
        self._config_cache: tuple[float, dict[str, Any]] | None = None
        self.cancelled = cancelled
        self.lease = EndpointLease(
            self.base_url, state_dir=state_dir, timeout=self.timeout_seconds, cancelled=cancelled
        )

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        with self.lease.hold():
            yield

    def capability_status(self) -> dict[str, Any]:
        try:
            models = self.list_voice_models(force=True, request_timeout_seconds=2.0)
        except Exception:
            return {"enabled": False, "status": "unavailable", "reason": "rvc_webui_unreachable"}
        if not models:
            return {"enabled": False, "status": "missing_model", "reason": "rvc_voice_models_missing"}
        return {"enabled": True, "status": "ready", "reason": ""}

    def list_voice_models(
        self,
        *,
        force: bool = False,
        request_timeout_seconds: float | None = None,
    ) -> list[str]:
        config = self._load_config(force=force, request_timeout_seconds=request_timeout_seconds)
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
        matches = matching_models(models, raw)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = "、".join(matches[:6])
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

    def cache_namespace(self):
        weights = {}
        if self.root_dir is not None:
            base = self.root_dir / "assets" / "uvr5_weights"
            for suffix in (".pth", ".onnx"):
                path = base / (Path(self.separation_model).name + suffix)
                weights[suffix] = self._stat_fingerprint(path)
        return cache_key({"endpoint": endpoint_namespace(self.base_url, self.root_dir), "separator_weights": weights})

    @exclusive_operation
    def separate_vocals(self, *, source_path: Path, work_dir: Path) -> tuple[Path, Path]:
        attempt_dir = work_dir / f"uvr_{uuid.uuid4().hex}"
        input_dir = attempt_dir / "input"
        vocals_dir = attempt_dir / "vocals"
        instrumental_dir = attempt_dir / "instrumental"
        for directory in (input_dir, vocals_dir, instrumental_dir):
            directory.mkdir(parents=True, exist_ok=True)
        staged_input = input_dir / f"cover{source_path.suffix.lower() or '.wav'}"
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
            vocals = self._first_audio_file(vocals_dir)
            instrumental = self._first_audio_file(instrumental_dir)
            if vocals is not None and instrumental is not None:
                return vocals, instrumental
            raise CoverSongError(
                stage="separation",
                reason="separation_outputs_missing",
                public_message="分离流程已经结束，但没有得到完整的人声和伴奏文件。",
            )
        raise CoverSongError(
            stage="separation",
            reason="rvc_uvr_failed",
            public_message="本机分离模型没有成功拆出主唱和伴奏。",
        )

    @exclusive_operation
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
        if self.output_dir is None or not self.output_dir.is_dir():
            raise CoverSongError(
                stage="provider",
                reason="rvc_output_directory_required",
                public_message="请配置 RVC 的专用临时输出目录后再转换。",
            )
        config = self._load_config()
        model_selection_started = time.perf_counter()
        change_data = self._build_api_inputs(
            config,
            "infer_change_voice",
            {"model": model_name, "protect": protect},
        )
        change_response = self._post_predict(config, "infer_change_voice", change_data)
        index_path = self._extract_change_voice_index(config, change_response)
        model_selection_seconds = time.perf_counter() - model_selection_started
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
        inference_started = time.perf_counter()
        response = self._post_predict(config, "infer_convert", infer_data)
        inference_request_seconds = time.perf_counter() - inference_started
        payload = list(response.get("data") or [])
        info = str(payload[0] if payload else "")
        if "success" not in info.lower() or len(payload) < 2:
            raise CoverSongError(
                stage="voice_conversion",
                reason="rvc_inference_failed",
                public_message="RVC 没有成功完成人声音色转换。",
            )
        audio = payload[1]
        result_path = Path(str(audio.get("name") if isinstance(audio, dict) else audio or "")).resolve()
        if not result_path.is_relative_to(self.output_dir.resolve()):
            raise CoverSongError(
                stage="voice_conversion",
                reason="rvc_output_outside_directory",
                public_message="RVC 返回的文件不在配置的临时输出目录内，已拒绝读取。",
            )
        if (
            not result_path.is_file()
            or result_path.suffix.lower() not in _AUDIO_SUFFIXES
            or result_path.stat().st_size == 0
        ):
            raise CoverSongError(
                stage="voice_conversion",
                reason="rvc_output_missing",
                public_message="RVC 返回了成功状态，但转换后的音频文件不可用。",
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result_path, output_path)
        timings = {
            "model_selection": round(model_selection_seconds, 3),
            "inference_request": round(inference_request_seconds, 3),
        }
        timings.update(self._parse_rvc_timings(info))
        return {"index_path": index_path, "info": info, "timings": timings}

    def _load_config(
        self,
        *,
        force: bool = False,
        request_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        if not force and self._config_cache is not None and now - self._config_cache[0] < 30.0:
            return self._config_cache[1]
        timeout = min(20.0, self.timeout_seconds)
        if request_timeout_seconds is not None:
            timeout = max(0.25, min(timeout, float(request_timeout_seconds)))
        try:
            payload = self._request_json("GET", "/config", deadline=time.monotonic() + timeout)
        except Exception:
            raise CoverSongError(
                stage="provider",
                reason="rvc_webui_unreachable",
                public_message="本机 RVC 服务暂时无法连接，请确认配置的专用 RVC WebUI 已启动。",
            ) from None
        if not isinstance(payload, dict):
            raise CoverSongError(
                stage="provider",
                reason="rvc_config_invalid",
                public_message="本机 RVC 服务返回了无法识别的配置。",
            )
        self._config_cache = (now, payload)
        return payload

    def _request_json(self, method, path, *, deadline, body=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        # No ambient proxy/credential hydration and no redirects for local APIs.
        with requests.Session() as session:
            session.trust_env = False
            with session.request(
                method, f"{self.base_url}{path}", json=body, timeout=remaining, allow_redirects=False, stream=True
            ) as response:
                if response.status_code != 200:
                    raise ValueError
                raw = bytearray()
                while chunk := response.raw.read1(8192, decode_content=True):
                    raw.extend(chunk)
                    if len(raw) > 4 * 1024 * 1024 or time.monotonic() >= deadline:
                        raise ValueError
                return json.loads(raw)

    @exclusive_operation
    def _post_predict(self, config: dict[str, Any], api_name: str, data: list[Any]) -> dict[str, Any]:
        dependency_index = self._dependency_index(config, api_name)
        # Gradio retains generator iterators by session_hash + fn_index. Never
        # use the shared None session or treat its first yielded value as final.
        session_hash = uuid.uuid4().hex
        deadline = time.monotonic() + self.timeout_seconds
        last_yield = None
        self.lease.begin(session_hash)
        try:
            for _ in range(64):
                # Cancellation never interrupts the drain of an already-started
                # generator. These POSTs resume that iterator, not new inference.
                payload = self._request_json(
                    "POST",
                    "/api/predict",
                    deadline=deadline,
                    body={"fn_index": dependency_index, "data": data, "session_hash": session_hash},
                )
                if (
                    not isinstance(payload, dict)
                    or not isinstance(payload.get("data"), list)
                    or not isinstance(payload.get("is_generating"), bool)
                ):
                    raise ValueError
                if payload["is_generating"]:
                    last_yield = payload["data"]
                    continue
                # FINISHED_ITERATING produces component-update placeholders;
                # return the last real value only after the iterator is closed.
                result = {**payload, "data": last_yield if last_yield is not None else payload["data"]}
                self.lease.finish()
                break
            else:
                raise ValueError
        except Exception:
            raise CoverSongError(
                stage="provider",
                reason=f"rvc_{api_name}_request_failed",
                public_message="未能确认本机 RVC 调用已完整结束；不能把中间输出当成完成，请先确认服务状态。",
            ) from None
        check_cancelled(self.cancelled)
        return result

    def _build_api_inputs(self, config: dict[str, Any], api_name: str, values: dict[str, Any]) -> list[Any]:
        dependency = self._dependency(config, api_name)
        components = self._components_by_id(config)
        output: list[Any] = []
        mapped = set()

        def take(key, default=None):
            mapped.add(key)
            return values.get(key, default)

        for component_id in dependency.get("inputs") or []:
            component = components.get(str(component_id)) or {}
            props = component.get("props") if isinstance(component.get("props"), dict) else {}
            label = str(props.get("label") or "").lower()
            component_type = str(component.get("type") or "")
            default = props.get("value")
            value = default
            if api_name == "infer_change_voice":
                if component_type == "dropdown" and ("音色" in label or "voice" in label):
                    value = take("model")
                elif "保护" in label:
                    value = take("protect", default)
            elif api_name == "infer_convert":
                if "说话人" in label or "speaker" in label:
                    value = take("speaker_id", 0)
                elif "待处理音频" in label or "audio file path" in label:
                    value = take("source_path")
                elif "变调" in label or "semitone" in label:
                    value = take("pitch_shift", 0)
                elif "f0曲线" in label or "f0 curve" in label:
                    value = take("f0_file")
                elif "音高提取" in label or "pitch extraction" in label:
                    value = take("f0_method", "rmvpe")
                elif component_type == "textbox" and "检索库" in label:
                    value = ""
                elif component_type == "dropdown" and "index" in label:
                    value = take("index_path", "")
                elif "检索特征占比" in label:
                    value = take("index_rate", default)
                elif "中值滤波" in label:
                    value = take("filter_radius", default)
                elif "重采样" in label:
                    value = take("resample_rate", 0)
                elif "音量包络" in label:
                    value = take("rms_mix_rate", default)
                elif "保护" in label:
                    value = take("protect", default)
            elif api_name == "uvr_convert":
                if component_type == "dropdown" and "模型" in label:
                    value = take("model")
                elif component_type == "textbox" and "输入待处理音频文件夹" in label:
                    value = take("input_dir")
                elif component_type == "textbox" and ("非主人声文件夹" in label or "instrumental" in label):
                    value = take("instrumental_dir")
                elif component_type == "textbox" and ("主人声文件夹" in label or "vocals" in label):
                    value = take("vocals_dir")
                elif component_type == "file":
                    value = None
                elif "激进程度" in label or "aggressiveness" in label:
                    value = take("aggressiveness", default)
                elif "导出文件格式" in label or "output format" in label:
                    value = take("output_format", "wav")
            output.append(value)
        required = {
            "infer_change_voice": {"model"},
            "infer_convert": {
                "source_path",
                "pitch_shift",
                "f0_method",
                "index_path",
                "index_rate",
                "filter_radius",
                "rms_mix_rate",
                "protect",
            },
            "uvr_convert": {"model", "input_dir", "vocals_dir", "instrumental_dir", "output_format", "aggressiveness"},
        }
        if not required.get(api_name, set()).issubset(mapped):
            raise CoverSongError(
                stage="provider",
                reason="rvc_api_schema_unsupported",
                public_message="当前 RVC 接口参数无法完整映射，已停止调用，避免使用错误的默认设置。",
            )
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
        root = directory.resolve()
        files = [
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix.lower() in _AUDIO_SUFFIXES
            and path.resolve().is_relative_to(root)
            and path.stat().st_size > 0
        ]
        return files[0] if len(files) == 1 else None

    def _choice_values(self, value: Any) -> list[str]:
        output: list[str] = []
        for item in value if isinstance(value, list) else []:
            candidate = item[0] if isinstance(item, (list, tuple)) and item else item
            text = str(candidate or "").strip()
            if text and not any(char in text for char in ("/", "\\", ":", "\n", "\r")) and text not in output:
                output.append(text)
        return output

    def _parse_rvc_timings(self, value: str) -> dict[str, float]:
        output: dict[str, float] = {}
        labels = {
            "npy": "feature_extraction",
            "f0": "pitch_extraction",
            "infer": "voice_synthesis",
        }
        for label, key in labels.items():
            match = re.search(rf"(?:^|\s){label}\s*:\s*([0-9]+(?:\.[0-9]+)?)s", str(value or ""), re.IGNORECASE)
            if match:
                output[key] = round(float(match.group(1)), 3)
        return output

    def _normalize_model_key(self, value: str) -> str:
        return normalize_model_key(value)

    def _stat_fingerprint(self, path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError:
            return {"missing": True}
        return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}

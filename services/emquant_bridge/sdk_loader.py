from __future__ import annotations

import importlib.util
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SDK_FUNCTION_NAMES = (
    "start",
    "stop",
    "datastatistics",
    "cfn",
    "cnq",
    "cnqcancel",
    "csq",
    "csqcancel",
    "csqsnapshot",
    "csd",
    "css",
    "sector",
)

READ_ONLY_QUERY_FUNCTIONS = frozenset({"datastatistics", "cfn", "csqsnapshot", "csd", "css", "sector"})
READ_ONLY_SUBSCRIPTION_FUNCTIONS = frozenset({"cnq", "cnqcancel", "csq", "csqcancel"})
FORBIDDEN_MUTATING_FUNCTIONS = frozenset({"pcreate", "porder", "pctransfer", "pdelete"})


@dataclass(frozen=True)
class SDKLoadResult:
    ok: bool
    status: str
    reason: str
    sdk: Any = None
    module_path: Path | None = None
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    python_bits: int = 0
    dll_present: bool | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": self.status,
            "reason": self.reason,
            "capabilities": dict(self.capabilities),
            "python_bits": self.python_bits,
            "dll_present": self.dll_present,
        }


class EmQuantSDKLoader:
    def load(self, api_root: str | Path | None) -> SDKLoadResult:
        raw_root = str(api_root or "").strip()
        if not raw_root:
            return SDKLoadResult(
                ok=False,
                status="missing_config",
                reason="EMQUANT_API_ROOT is not configured",
            )
        root = Path(raw_root).expanduser().resolve()
        module_path = self._resolve_module_path(root)
        if module_path is None:
            return SDKLoadResult(
                ok=False,
                status="invalid_sdk",
                reason="EmQuantAPI.py was not found under the configured SDK root",
            )
        python_bits = 64 if platform.architecture()[0].startswith("64") else 32
        dll_present = self._detect_platform_library(module_path.parent, python_bits=python_bits)
        try:
            spec = importlib.util.spec_from_file_location("_akane_emquant_bridge_sdk", module_path)
            if spec is None or spec.loader is None:
                raise ImportError("unable to create module spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sdk = getattr(module, "c", None)
            if sdk is None:
                raise ImportError("EmQuantAPI module does not expose c")
        except Exception as exc:
            return SDKLoadResult(
                ok=False,
                status="invalid_sdk",
                reason=f"failed to import EmQuantAPI: {type(exc).__name__}",
                module_path=module_path,
                python_bits=python_bits,
                dll_present=dll_present,
            )
        capabilities = {name: callable(getattr(sdk, name, None)) for name in SDK_FUNCTION_NAMES}
        if not capabilities.get("start") or not capabilities.get("stop"):
            return SDKLoadResult(
                ok=False,
                status="invalid_sdk",
                reason="EmQuantAPI is missing required lifecycle functions",
                sdk=sdk,
                module_path=module_path,
                capabilities=capabilities,
                python_bits=python_bits,
                dll_present=dll_present,
            )
        return SDKLoadResult(
            ok=True,
            status="loaded",
            reason="",
            sdk=sdk,
            module_path=module_path,
            capabilities=capabilities,
            python_bits=python_bits,
            dll_present=dll_present,
        )

    @staticmethod
    def _resolve_module_path(root: Path) -> Path | None:
        candidates = (
            root if root.is_file() else root / "EmQuantAPI.py",
            root / "python3" / "EmQuantAPI.py",
        )
        for candidate in candidates:
            if candidate.is_file() and candidate.name.lower() == "emquantapi.py":
                return candidate.resolve()
        return None

    @staticmethod
    def _detect_platform_library(module_dir: Path, *, python_bits: int) -> bool | None:
        system = platform.system().lower()
        if system == "windows":
            filename = "EmQuantAPI_x64.dll" if python_bits == 64 else "EmQuantAPI.dll"
            return (module_dir / "libs" / "windows" / filename).is_file()
        if system == "darwin":
            return (module_dir / "libs" / "mac" / "libEMQuantAPIx64.dylib").is_file()
        if system == "linux":
            arch_dir = "x64" if python_bits == 64 else "x86"
            return (module_dir / "libs" / "linux" / arch_dir / "libEMQuantAPI.so").is_file()
        return None

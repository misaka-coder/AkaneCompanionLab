"""Install an explicit backend/full profile using verified official artifacts."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.package_supply import exact_pins, public_wheelhouse, validate_wheelhouse

ROOT = Path(__file__).resolve().parents[1]


def _pip(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "pip", "--disable-pip-version-check", *args], check=True)


def install(*, profile: str, wheelhouse: Path | None = None, index_url: str = "") -> None:
    if profile not in {"backend", "full"}:
        raise ValueError("dependency_profile_invalid")
    pins_file = ROOT / "requirements-packages.txt"
    requirements = ROOT / ("requirements-backend.txt" if profile == "backend" else "requirements.txt")
    if index_url and wheelhouse:
        raise ValueError("dependency_supply_sources_conflict")
    if index_url:
        _pip("install", "--force-reinstall", "--no-deps", "--index-url", index_url, "-r", str(pins_file))
        _pip("install", "--index-url", index_url, "-r", str(requirements))
    else:
        if wheelhouse:
            wheelhouse = wheelhouse.resolve()
            validate_wheelhouse(wheelhouse, exact_pins(pins_file), offline_profile=profile)
            source = nullcontext(wheelhouse)
        else:
            source = public_wheelhouse(ROOT)
        with source as verified:
            _pip("install", "--force-reinstall", "--no-deps", "--no-index", "--find-links", str(verified),
                 "-r", str(pins_file))
            # A supplied complete wheelhouse is entirely offline. The official
            # internal-only bundle uses PyPI for ordinary third-party packages.
            options = ["--no-index"] if wheelhouse else []
            _pip("install", *options, "--find-links", str(verified), "-r", str(requirements))
    _pip("check")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "check_packaged_dependencies.py")], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("backend", "full"), default="backend")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--index-url", default="")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        print("AKANE_DEPENDENCIES_FAILED:python_3_11_required", file=sys.stderr)
        return 1
    try:
        supplied = args.wheelhouse or (Path(os.environ["AKANE_PACKAGE_WHEELHOUSE"])
                                      if os.environ.get("AKANE_PACKAGE_WHEELHOUSE") else None)
        install(profile=args.profile, wheelhouse=supplied,
                index_url=args.index_url or os.environ.get("AKANE_PACKAGE_INDEX_URL", ""))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        # Do not print configured URLs: custom package indexes may carry secrets.
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"AKANE_DEPENDENCIES_FAILED:{reason}", file=sys.stderr)
        return 1
    print(f"AKANE_DEPENDENCIES_OK:profile={args.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

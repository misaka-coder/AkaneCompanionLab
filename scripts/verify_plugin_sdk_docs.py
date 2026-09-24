"""Check that the public SDK, its packaging and its documentation agree.

The SDK version and the API names it documents drifted apart while slices were
landing: `pyproject.toml`, `akane_plugin.__version__`, the README heading and the
example projects could disagree, and documented `@plugin.*` / `ctx.*` methods
were not checked against the code. This module is the single automated guard for
that class of drift; it reads the real package instead of a hand-kept list.
"""

from __future__ import annotations

import re
import sys
import tomllib
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import akane_plugin  # noqa: E402  (path bootstrap must run first)

PYPROJECT = ROOT / "pyproject.toml"
SDK_README = ROOT / "akane_plugin" / "README.md"
EXAMPLES_README = ROOT / "examples" / "plugins" / "README.md"
SKILL = ROOT / "skills" / "plugin-development" / "SKILL.md"

_VERSION_PATTERN = re.compile(r"^version\s*=\s*\"([^\"]+)\"", re.MULTILINE)
_HEADING_PATTERN = re.compile(r"^#\s*Akane Plugin SDK\s*([0-9][0-9.]*)", re.MULTILINE)
_PLUGIN_METHOD_PATTERN = re.compile(r"@plugin\.([a-z_]+)")
_CONTEXT_METHOD_PATTERN = re.compile(r"ctx\.(events|tools|services|connections)\.([a-z_]+)")
_DOCUMENTED_NAMES_PATTERN = re.compile(r"`([A-Z][A-Za-z0-9]+)`")
_DOCUMENTED_SDK_TYPES = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Receipt|Context|Input|Event|Binding|Error|Spec|Payload|Subscription|Delivery))\b")
_DOCUMENTED_TYPE_ALLOWLIST = frozenset({"EventReceipt", "TurnReceipt", "ObservationReceipt"})
_SDK_VERSION_CLAIM_PATTERN = re.compile(r"SDK([^\n`]{0,24}?)([0-9]+\.[0-9]+\.[0-9]+)")
_MINIMUM_SDK_CLAIM_PATTERN = re.compile(r"`akane-plugin>=([0-9.]+),<([0-9.]+)`")
_NOT_SDK_NAMES = frozenset({
    "Any", "False", "Literal", "None", "PYTHONPATH", "True", "CancelledError", "ValueError", "TimeoutError",
})


def _resolve_member(owner: object, name: str) -> object | None:
    """Resolve one SDK member, following annotated ports and property types."""

    member = getattr(owner, name, None)
    if isinstance(member, property):
        return typing.get_type_hints(member.fget).get("return") if member.fget is not None else None
    resolved = typing.get_type_hints(owner).get(name)
    if resolved is not None and resolved is not typing.Any:
        # Slot descriptors and plain class attributes both resolve through the
        # class annotation, which is the real port type.
        return resolved
    if member is not None and not (hasattr(member, "__get__") and not hasattr(member, "__call__")):
        return member
    return None


_CONTEXT_PORT_CLASSES = {"tools": "Tools", "events": "Events", "services": "Services", "connections": "Connections"}


def _context_ports(namespace: str) -> object | None:
    """Return the port object the SDK contexts expose under one namespace."""

    for owner in (akane_plugin.ToolContext, akane_plugin.EventContext):
        ports = _resolve_member(owner, namespace)
        if ports is not None:
            return ports
    # `services`/`connections` are unannotated properties that return these
    # public port classes.
    return getattr(akane_plugin, _CONTEXT_PORT_CLASSES[namespace], None)


def declared_version() -> str:
    match = _VERSION_PATTERN.search(PYPROJECT.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError("pyproject.toml does not declare a project version")
    return match.group(1)


def check_version_consistency() -> tuple[str, ...]:
    """Return every place that disagrees with the packaged SDK version."""

    version = akane_plugin.__version__
    problems: list[str] = []
    if declared_version() != version:
        problems.append(f"pyproject.toml declares {declared_version()}, package has {version}")
    heading = _HEADING_PATTERN.search(SDK_README.read_text(encoding="utf-8"))
    if heading is None or heading.group(1) != version:
        problems.append(f"akane_plugin/README.md heading does not name SDK {version}")
    if f"akane_plugin-{version}-py3-none-any.whl" not in SDK_README.read_text(encoding="utf-8"):
        problems.append(f"akane_plugin/README.md install example does not name SDK {version}")
    return tuple(problems)


def check_documented_api() -> tuple[str, ...]:
    """Return documented SDK members that the package does not actually expose."""

    problems: list[str] = []
    for document in (SDK_README, EXAMPLES_README, SKILL):
        text = document.read_text(encoding="utf-8")
        for method in sorted(set(_PLUGIN_METHOD_PATTERN.findall(text))):
            if not hasattr(akane_plugin.Plugin, method):
                problems.append(f"{document.name} documents @plugin.{method}, which Plugin does not provide")
        for namespace, method in sorted(set(_CONTEXT_METHOD_PATTERN.findall(text))):
            ports = _context_ports(namespace)
            if ports is None or not hasattr(ports, method):
                problems.append(
                    f"{document.name} documents ctx.{namespace}.{method}, which the SDK contexts do not provide"
                )
        for name in sorted(set(_DOCUMENTED_NAMES_PATTERN.findall(text))):
            if name in _NOT_SDK_NAMES or name in akane_plugin.__all__:
                continue
            problems.append(f"{document.name} names `{name}`, which the SDK does not export")
        # A documented type the SDK does not export is a doc bug even when it is
        # never written with an `akane_plugin.` prefix in an example.
        for name in sorted(set(_DOCUMENTED_SDK_TYPES.findall(text))):
            if name in _NOT_SDK_NAMES or name in akane_plugin.__all__:
                continue
            problems.append(f"{document.name} documents `{name}`, which the SDK does not export")
    return tuple(problems)


def check_release_claims() -> tuple[str, ...]:
    """Return stale SDK version claims in the example index and the Skill."""

    problems: list[str] = []
    version = akane_plugin.__version__
    major, minor, _ = version.split(".")
    for document in (EXAMPLES_README, SKILL):
        text = document.read_text(encoding="utf-8")
        for between, claimed in _SDK_VERSION_CLAIM_PATTERN.findall(text):
            if " and " in between:
                # "SDK and CapCore 0.1.3" names another artifact's version.
                continue
            if claimed != version:
                problems.append(f"{document.name} claims SDK {claimed}; the package is {version}")
        for minimum, maximum in _MINIMUM_SDK_CLAIM_PATTERN.findall(text):
            if not (minimum.startswith(f"{major}.{minor}") and maximum.startswith(f"{major}.{int(minor) + 1}")):
                problems.append(
                    f"{document.name} claims akane-plugin>={minimum},<{maximum}, which excludes {version}"
                )
    return tuple(problems)


def check_example_release_compatibility() -> tuple[str, ...]:
    """Return installable packages whose SDK pin excludes the packaged release.

    ``scripts/verify_plugin_sdk_release.py`` installs the SDK together with every
    release example and the image-generation plugin in one environment, so a stale
    upper bound in any manifest makes the whole release check unresolvable. That
    regression shipped silently once; this guard fails the docs check instead.
    """

    problems: list[str] = []
    version = akane_plugin.__version__
    major, minor, _ = version.split(".")
    manifests = [
        *sorted((ROOT / "examples" / "plugins").glob("*/pyproject.toml")),
        *sorted((ROOT / "plugins").glob("*/pyproject.toml")),
    ]
    for manifest in manifests:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        for requirement in data.get("project", {}).get("dependencies", ()):
            if not requirement.startswith("akane-plugin"):
                continue
            for minimum, maximum in _MINIMUM_SDK_CLAIM_PATTERN.findall(f"`{requirement}`"):
                if not (minimum.startswith(f"{major}.{minor}") and maximum.startswith(f"{major}.{int(minor) + 1}")):
                    problems.append(
                        f"{manifest.parent.name} pins {requirement}, which excludes the packaged SDK {version}"
                    )
            break
    return tuple(problems)


def check_example_readme_claims() -> tuple[str, ...]:
    """Return per-example READMEs that still name an older SDK release.

    Each example's own README tells an author which release to install. Those
    claims drifted independently of the pinned manifests and the examples index,
    so they are checked here as well.
    """

    problems: list[str] = []
    version = akane_plugin.__version__
    for readme in sorted((ROOT / "examples" / "plugins").glob("*/README.md")):
        text = readme.read_text(encoding="utf-8")
        for between, claimed in _SDK_VERSION_CLAIM_PATTERN.findall(text):
            if " and " in between:
                # "SDK and CapCore 0.1.3" names another artifact's version.
                continue
            if claimed != version:
                problems.append(
                    f"{readme.parent.name}/README.md claims SDK {claimed}; the package is {version}"
                )
    return tuple(problems)


def check() -> tuple[str, ...]:
    return (
        *check_version_consistency(),
        *check_documented_api(),
        *check_release_claims(),
        *check_example_release_compatibility(),
        *check_example_readme_claims(),
    )


if __name__ == "__main__":
    issues = check()
    for issue in issues:
        print(issue)
    raise SystemExit(1 if issues else 0)

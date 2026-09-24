"""Receipt-bound forwarding of already finalized dependency results.

The current invocation may return a result it actually received. Artifact files
are neither copied nor registered again; their original producer remains intact.
Public JSON alone cannot manufacture a receipt in another invocation.
"""

import hashlib
import json
from collections.abc import Mapping

from .plugin_result_experience import has_reserved_plugin_result_key
from .plugin_result_projection import project_capability_result, sanitize_capability_result


def _fingerprint(result):
    if not has_reserved_plugin_result_key(getattr(result, "content", None)):
        return None
    snapshot = project_capability_result(result)
    content = snapshot.get("content")
    if not isinstance(content, Mapping):
        return None
    # Presentation is assigned by each receiving host, not by plugin code.
    # Everything else, including origin, delivery intent and value, must match.
    references = content.get("managed_artifacts")
    for reference in references if isinstance(references, list) else ():
        if isinstance(reference, dict):
            reference.pop("forwarded_by_tool", None)
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def remember_dependency_result(invocation, result):
    fingerprint = _fingerprint(result)
    if fingerprint is not None:
        invocation.dependency_result_receipts.add(fingerprint)


def forwarded_dependency_result(invocation, result):
    """Return a verified copy, or None to use ordinary untrusted-result validation.

Revocation prevents new calls and delivery; it does not erase confirmed results
already received by this still-owned invocation.
"""
    if invocation is None or not invocation.dependency_result_receipts:
        return None
    fingerprint = _fingerprint(result)
    if fingerprint is None or fingerprint not in invocation.dependency_result_receipts:
        return None
    forwarded = sanitize_capability_result(result)
    references = forwarded.content.get("managed_artifacts")
    for reference in references if isinstance(references, list) else ():
        if isinstance(reference, dict):
            reference["forwarded_by_tool"] = invocation.capability_id
    return forwarded

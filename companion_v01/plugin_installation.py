"""Stable plugin artifact staging, installation and publication imports.

Implementation lives in plugin_artifact_runtime.
"""

from __future__ import annotations

import hashlib as hashlib
import json as json
import os as os
import re as re
import shutil as shutil
import subprocess as subprocess
import sys as sys
import tempfile as tempfile
import threading as threading
import time as time
import uuid as uuid
import zipfile as zipfile
from dataclasses import dataclass as dataclass
from email.parser import Parser as Parser
from pathlib import Path as Path
from typing import Any as Any
from typing import Iterable as Iterable
from typing import Mapping as Mapping
from .distribution_artifacts import audit_distribution_artifact as audit_distribution_artifact
from .plugin_api import AKANE_PLUGIN_ENTRYPOINT_GROUP as AKANE_PLUGIN_ENTRYPOINT_GROUP
from .plugin_api import is_valid_permission_id as is_valid_permission_id
from .plugin_api import is_valid_plugin_id as is_valid_plugin_id
from .plugin_generation import PluginGenerationError as PluginGenerationError
from .plugin_generation import PluginGenerationProcess as PluginGenerationProcess

from .plugin_artifact_runtime.contracts import (
    PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION as PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION,
    PLUGIN_ARTIFACT_CATALOG_FILENAME as PLUGIN_ARTIFACT_CATALOG_FILENAME,
    PLUGIN_STAGE_METADATA_FILENAME as PLUGIN_STAGE_METADATA_FILENAME,
    PLUGIN_INSTALL_TIMEOUT_SECONDS as PLUGIN_INSTALL_TIMEOUT_SECONDS,
    PLUGIN_PROBE_TIMEOUT_SECONDS as PLUGIN_PROBE_TIMEOUT_SECONDS,
    PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS as PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS,
    PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS as PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS,
    PLUGIN_SOURCE_TEST_OUTPUT_BYTES as PLUGIN_SOURCE_TEST_OUTPUT_BYTES,
    PLUGIN_PIP_DIAGNOSTIC_BYTES as PLUGIN_PIP_DIAGNOSTIC_BYTES,
    PluginInstallationError as PluginInstallationError,
    StagedPluginArtifact as StagedPluginArtifact,
    PluginGenerationSource as PluginGenerationSource,
    _normalize_permissions as _normalize_permissions,
)

from .plugin_artifact_runtime.dependencies import (
    _wheel_runtime_requirements as _wheel_runtime_requirements,
    _diagnostic_id as _diagnostic_id,
    _simple_diagnostic as _simple_diagnostic,
    _pip_failure as _pip_failure,
    _HOST_PROVIDED_DISTRIBUTIONS as _HOST_PROVIDED_DISTRIBUTIONS,
    _PIP_MISSING_REQUIREMENT_RE as _PIP_MISSING_REQUIREMENT_RE,
    InvalidRequirement as InvalidRequirement,
    Requirement as Requirement,
)

from .plugin_artifact_runtime.storage import (
    importlib_metadata as importlib_metadata,
    _copy_and_hash as _copy_and_hash,
    _inspect_installed_site as _inspect_installed_site,
    _process_plugin_entry_points as _process_plugin_entry_points,
    _site_has_plugin_entry_point as _site_has_plugin_entry_point,
    _write_json_atomic as _write_json_atomic,
)

from .plugin_artifact_runtime.staging import (
    _read_test_output as _read_test_output,
    _scrubbed_plugin_subprocess_env as _scrubbed_plugin_subprocess_env,
    _SENSITIVE_ENV_NAME_RE as _SENSITIVE_ENV_NAME_RE,
)

from .plugin_artifact_runtime.store import (
    ManagedPluginArtifactStore as ManagedPluginArtifactStore,
)

__all__ = [
    "PLUGIN_ARTIFACT_CATALOG_FILENAME",
    "PLUGIN_ARTIFACT_CATALOG_SCHEMA_VERSION",
    "PLUGIN_INSTALL_TIMEOUT_SECONDS",
    "PLUGIN_PROBE_TIMEOUT_SECONDS",
    "PLUGIN_SOURCE_BUILD_TIMEOUT_SECONDS",
    "PLUGIN_SOURCE_TEST_OUTPUT_BYTES",
    "PLUGIN_SOURCE_TEST_TIMEOUT_SECONDS",
    "ManagedPluginArtifactStore",
    "PluginGenerationSource",
    "PluginInstallationError",
    "StagedPluginArtifact",
]

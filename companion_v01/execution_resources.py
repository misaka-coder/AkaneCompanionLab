"""Compatibility entry point for execution resource staging and artifact registration.

Implementation responsibilities live in ``execution_resource_runtime``. All
handlers continue to share one bridge, its binding map and registration lock.
"""
from __future__ import annotations

import re as re
import shutil as shutil
import threading as threading
import time as time
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from typing import Any as Any
from typing import Mapping as Mapping
from typing import Sequence as Sequence
from .execution_run import is_valid_run_id as is_valid_run_id
from .execution_specs import ARTIFACT_STATUS_NOT_REGISTERED as ARTIFACT_STATUS_NOT_REGISTERED
from .execution_specs import ARTIFACT_STATUS_NOT_REQUESTED as ARTIFACT_STATUS_NOT_REQUESTED
from .execution_specs import ARTIFACT_STATUS_REGISTERED as ARTIFACT_STATUS_REGISTERED
from .execution_specs import ARTIFACT_STATUS_REGISTRATION_FAILED as ARTIFACT_STATUS_REGISTRATION_FAILED

from .execution_resource_runtime.contracts import EXEC_MAX_INPUT_RESOURCES as EXEC_MAX_INPUT_RESOURCES
from .execution_resource_runtime.contracts import EXEC_MAX_INPUT_BYTES_PER_FILE as EXEC_MAX_INPUT_BYTES_PER_FILE
from .execution_resource_runtime.contracts import EXEC_MAX_INPUT_TOTAL_BYTES as EXEC_MAX_INPUT_TOTAL_BYTES
from .execution_resource_runtime.contracts import EXEC_MAX_OUTPUT_FILES as EXEC_MAX_OUTPUT_FILES
from .execution_resource_runtime.contracts import EXEC_MAX_OUTPUT_BYTES_PER_FILE as EXEC_MAX_OUTPUT_BYTES_PER_FILE
from .execution_resource_runtime.contracts import EXEC_MAX_OUTPUT_TOTAL_BYTES as EXEC_MAX_OUTPUT_TOTAL_BYTES
from .execution_resource_runtime.contracts import EXEC_RESOURCE_AS_MAX_CHARS as EXEC_RESOURCE_AS_MAX_CHARS
from .execution_resource_runtime.contracts import EXEC_OUTPUT_GLOB_MAX_CHARS as EXEC_OUTPUT_GLOB_MAX_CHARS
from .execution_resource_runtime.contracts import _DRIVE_RE as _DRIVE_RE
from .execution_resource_runtime.contracts import StagedInput as StagedInput
from .execution_resource_runtime.contracts import RegisteredOutput as RegisteredOutput
from .execution_resource_runtime.contracts import ExecutionResourceScope as ExecutionResourceScope
from .execution_resource_runtime.contracts import _RunBinding as _RunBinding
from .execution_resource_runtime.contracts import normalize_resource_as as normalize_resource_as
from .execution_resource_runtime.contracts import normalize_output_glob as normalize_output_glob
from .execution_resource_runtime.contracts import _same_owner as _same_owner
from .execution_resource_runtime.bridge import ExecutionResourceBridge as ExecutionResourceBridge

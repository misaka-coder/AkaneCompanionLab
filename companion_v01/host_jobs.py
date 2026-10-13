"""Stable entry for durable host-owned background jobs.

The implementation lives in ``host_job_runtime``. Workers, control commands,
recovery and completion delivery share one store and its original transactions.
"""
from __future__ import annotations

import json as json
import sqlite3 as sqlite3
import time as time
import uuid as uuid
from contextlib import contextmanager as contextmanager
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from pathlib import Path as Path
from typing import Any as Any
from typing import Callable as Callable
from typing import Iterator as Iterator

from .host_job_runtime.models import JOB_TERMINAL_STATUSES as JOB_TERMINAL_STATUSES
from .host_job_runtime.models import JOB_CONTROL_STATES as JOB_CONTROL_STATES
from .host_job_runtime.models import _COMPLETION_MODES as _COMPLETION_MODES
from .host_job_runtime.models import _MEMORY_MODES as _MEMORY_MODES
from .host_job_runtime.models import HostJobOwner as HostJobOwner
from .host_job_runtime.models import HostJob as HostJob
from .host_job_runtime.models import _json_object as _json_object
from .host_job_runtime.models import _json_value as _json_value
from .host_job_runtime.models import _json_value_load as _json_value_load
from .host_job_runtime.models import _sha256_text as _sha256_text
from .host_job_runtime.models import _json_artifacts as _json_artifacts
from .host_job_runtime.store import HostJobStore as HostJobStore


__all__ = ["HostJob", "HostJobOwner", "HostJobStore", "JOB_TERMINAL_STATUSES", "JOB_CONTROL_STATES"]

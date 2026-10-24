"""Sponsor adapters (Zetaris / Meterless / NVIDIA) + local H-MEM interface."""

from apps.reasoning_engine.adapters.hmem_adapter import (
    HMemAdapter,
    HMemRemoteContractPending,
)
from apps.reasoning_engine.adapters.meterless_adapter import (
    MeterlessAdapter,
    MeterlessAuthError,
    MeterlessNotConfigured,
    MeterlessNotFound,
    MeterlessRuntimeError,
    MeterlessTimeout,
)
from apps.reasoning_engine.adapters.nvidia_adapter import (
    NvidiaAdapter,
    NvidiaAuthError,
    NvidiaModelNotFound,
    NvidiaNotConfigured,
    NvidiaRuntimeError,
    NvidiaTimeout,
)
from apps.reasoning_engine.adapters.zetaris_adapter import (
    ZetarisAdapter,
    ZetarisAuthError,
    ZetarisNotConfigured,
    ZetarisNotFound,
    ZetarisQueryError,
    ZetarisTimeout,
)

__all__ = [
    "HMemAdapter",
    "HMemRemoteContractPending",
    "ZetarisAdapter",
    "ZetarisAuthError",
    "ZetarisNotConfigured",
    "ZetarisNotFound",
    "ZetarisQueryError",
    "ZetarisTimeout",
    "MeterlessAdapter",
    "MeterlessAuthError",
    "MeterlessNotConfigured",
    "MeterlessNotFound",
    "MeterlessRuntimeError",
    "MeterlessTimeout",
    "NvidiaAdapter",
    "NvidiaAuthError",
    "NvidiaModelNotFound",
    "NvidiaNotConfigured",
    "NvidiaRuntimeError",
    "NvidiaTimeout",
]

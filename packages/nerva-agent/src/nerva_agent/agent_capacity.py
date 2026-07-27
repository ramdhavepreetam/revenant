"""Hardware-aware capacity tuning for the Revenant harness (P6).

Detects the machine (RAM, cores, platform) and the active model's size, then
recommends loop knobs — `max_context_tokens`, `max_steps`, `keep_recent_steps` —
and whether to keep multiple models resident or swap on demand.

The goal is that `revenant` works sensibly out of the box on whatever box it's on:
a 96 GB workstation gets a big context budget and can pin models resident; a 16 GB
laptop gets a tighter budget and swap-on-demand.

Everything degrades gracefully: if hardware/model probing fails, conservative
defaults are returned. All values are advisory — the CLI and web layer may override.
"""
from __future__ import annotations

import os
import platform
import subprocess
import urllib.request
import json
from dataclasses import dataclass


@dataclass
class MachineInfo:
    ram_gb: float
    cpu_count: int
    platform: str        # "darwin" | "linux" | "windows" | ...
    arch: str            # "arm64" | "x86_64" | ...
    apple_silicon: bool


@dataclass
class Recommendation:
    max_context_tokens: int
    max_steps: int
    keep_recent_steps: int
    keep_resident: bool          # can we afford to keep >1 model loaded?
    model_gb: float              # size of the active model (0 if unknown)
    machine: MachineInfo
    note: str = ""


def detect_machine() -> MachineInfo:
    """Best-effort hardware detection. Never raises."""
    system = platform.system().lower()
    arch = platform.machine().lower()
    ram_gb = 8.0
    cpu = os.cpu_count() or 4
    try:
        if system == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=3)
            ram_gb = int(out.strip()) / 1024 ** 3
        elif system == "linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        ram_gb = int(line.split()[1]) / 1024 ** 2  # kB -> GB
                        break
        else:  # windows / unknown
            try:
                import ctypes  # local import; only needed here

                class _MS(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
                stat = _MS(); stat.dwLength = ctypes.sizeof(_MS)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
                ram_gb = stat.ullTotalPhys / 1024 ** 3
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    apple_silicon = system == "darwin" and arch in ("arm64", "aarch64")
    return MachineInfo(round(ram_gb, 1), int(cpu), system, arch, apple_silicon)


def model_size_gb(model: str, base_url: str = "http://localhost:11434", timeout: int = 3) -> float:
    """Size of an Ollama model in GB, or 0.0 if unknown/unreachable. Never raises."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for m in data.get("models", []):
            if m.get("name") == model:
                return round(int(m.get("size", 0)) / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def recommend(
    model: str = "",
    *,
    base_url: str = "http://localhost:11434",
    machine: MachineInfo | None = None,
    model_gb: float | None = None,
) -> Recommendation:
    """Recommend loop knobs from RAM and model size.

    Heuristics (RAM tiers), tuned for local 7B-14B quantized models:
      - context budget scales with RAM: enough headroom for the model + KV cache
        + app, without pushing the machine into swap.
      - keep_resident: true when there's room for the active model AND a second
        model (router/companion) to stay loaded (~2.2x the active model's size).
    """
    machine = machine or detect_machine()
    if model_gb is None:
        model_gb = model_size_gb(model, base_url) if model else 0.0

    ram = machine.ram_gb

    # Context budget by RAM tier (tokens). Local models are the limiter, not RAM,
    # but low-RAM boxes can't afford a huge KV cache alongside the app.
    if ram >= 64:
        max_context = 16000
    elif ram >= 32:
        max_context = 10000
    elif ram >= 16:
        max_context = 6000
    elif ram >= 8:
        max_context = 3500
    else:
        max_context = 2000

    # Step cap: more headroom on bigger machines (they finish long tasks faster).
    max_steps = 20 if ram >= 32 else (15 if ram >= 16 else 10)

    # Keep the most recent N step-pairs verbatim; scale slightly with budget.
    keep_recent_steps = 4 if max_context >= 10000 else 3

    # Resident vs swap: room for the active model + a second (~2.2x) with headroom
    # for the app (~4 GB) and OS. If model size is unknown, be conservative.
    effective_model = model_gb or 5.0
    keep_resident = ram >= (effective_model * 2.2 + 6.0)

    note = (
        f"{ram:.0f}GB RAM, {machine.cpu_count} cores"
        + (", Apple Silicon" if machine.apple_silicon else "")
        + (f", model {model_gb:.1f}GB" if model_gb else "")
        + f" → context {max_context}, steps {max_steps}, "
        + ("keep models resident" if keep_resident else "swap on demand")
    )
    return Recommendation(
        max_context_tokens=max_context,
        max_steps=max_steps,
        keep_recent_steps=keep_recent_steps,
        keep_resident=keep_resident,
        model_gb=model_gb,
        machine=machine,
        note=note,
    )

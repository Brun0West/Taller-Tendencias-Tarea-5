"""Lecturas de hardware para el panel "estadísticas para nerds".

Solo Linux. GPU NVIDIA vía nvidia-smi; GPU AMD vía sysfs (driver amdgpu).
Si una lectura no está disponible se devuelve None en lugar de fallar: el
panel muestra "–" y el asistente sigue funcionando.
"""
import asyncio
import os
import shutil
from pathlib import Path

MIB = 1024 ** 2
NVIDIA_FIELDS = (
    "name", "driver_version", "utilization.gpu", "memory.used", "memory.total",
    "temperature.gpu", "power.draw", "power.limit", "clocks.gr", "clocks.mem", "pstate",
)


def _number(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None  # p. ej. "[N/A]" en GPUs de laptop


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


async def _nvidia() -> dict | None:
    if not shutil.which("nvidia-smi"):
        return None
    proc = await asyncio.create_subprocess_exec(
        "nvidia-smi", f"--query-gpu={','.join(NVIDIA_FIELDS)}", "--format=csv,noheader,nounits",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    lines = out.decode().splitlines()
    if proc.returncode != 0 or not lines:
        return None
    v = dict(zip(NVIDIA_FIELDS, (field.strip() for field in lines[0].split(","))))
    used, total = _number(v["memory.used"]), _number(v["memory.total"])
    return {
        "fuente": "nvidia-smi",
        "nombre": v["name"],
        "driver": v["driver_version"],
        "uso": _number(v["utilization.gpu"]),
        "vram_usada": used * MIB if used is not None else None,
        "vram_total": total * MIB if total is not None else None,
        "temperatura": _number(v["temperature.gpu"]),
        "potencia": _number(v["power.draw"]),
        "potencia_max": _number(v["power.limit"]),
        "reloj_mhz": _number(v["clocks.gr"]),
        "reloj_mem_mhz": _number(v["clocks.mem"]),
        "pstate": v["pstate"],
    }


def _amd() -> dict | None:
    cards = [
        (dev, int(total))
        for dev in Path("/sys/class/drm").glob("card[0-9]*/device")
        if (total := _read(dev / "mem_info_vram_total"))
    ]
    if not cards:
        return None
    dev, total = max(cards, key=lambda card: card[1])
    hwmon = next(dev.glob("hwmon/hwmon*"), None)

    def sensor(name: str, scale: float) -> float | None:
        raw = _number(_read(hwmon / name)) if hwmon else None
        return raw / scale if raw is not None else None

    used = _number(_read(dev / "mem_info_vram_used"))
    return {
        "fuente": "amdgpu (sysfs)",
        "nombre": _read(dev / "product_name") or "GPU AMD",
        "driver": "amdgpu",
        "uso": _number(_read(dev / "gpu_busy_percent")),
        "vram_usada": used,
        "vram_total": total,
        "temperatura": sensor("temp1_input", 1000),
        "potencia": sensor("power1_average", 1e6) or sensor("power1_input", 1e6),
        "potencia_max": sensor("power1_cap", 1e6),
        "reloj_mhz": None,
        "reloj_mem_mhz": None,
        "pstate": None,
    }


async def gpu() -> dict | None:
    try:
        return await _nvidia() or _amd()
    except OSError:
        return None


class CpuSampler:
    """% de CPU entre dos llamadas consecutivas (la primera devuelve None)."""

    def __init__(self):
        self._previous = None

    def percent(self) -> float | None:
        fields = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:9]]
        idle, total = fields[3] + fields[4], sum(fields)
        previous, self._previous = self._previous, (idle, total)
        if not previous or total == previous[1]:
            return None
        return round(100 * (1 - (idle - previous[0]) / (total - previous[1])), 1)


def _cpu_name() -> str | None:
    for line in (_read(Path("/proc/cpuinfo")) or "").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


CPU_NAME = _cpu_name()


def _ollama_rss() -> int | None:
    """RSS sumado del servidor Ollama y sus runners (todos se llaman "ollama")."""
    total = 0
    for status in Path("/proc").glob("[0-9]*/status"):
        text = _read(status)
        if not text or not text.startswith("Name:\tollama"):
            continue
        for line in text.splitlines():
            if line.startswith("VmRSS:"):
                total += int(line.split()[1]) * 1024
    return total or None


def system(cpu: CpuSampler) -> dict | None:
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            mem[key] = int(value.split()[0]) * 1024
        load = [float(x) for x in Path("/proc/loadavg").read_text().split()[:3]]
        return {
            "cpu": cpu.percent(),
            "cpu_nombre": CPU_NAME,
            "nucleos": os.cpu_count(),
            "ram_usada": mem["MemTotal"] - mem["MemAvailable"],
            "ram_total": mem["MemTotal"],
            "swap_usada": mem["SwapTotal"] - mem["SwapFree"],
            "swap_total": mem["SwapTotal"],
            "carga": load,
            "ollama_rss": _ollama_rss(),
        }
    except (OSError, KeyError, ValueError):
        return None

"""Reglas por modelo: estimación de VRAM y parámetros de /api/chat.

Las funciones son puras: reciben lo que devuelve Ollama en /api/tags y
/api/show y no hacen llamadas de red.
"""

MIB = 1024 ** 2
# Grafo de cómputo y buffers del runtime. Con este valor la estimación queda
# cerca de lo medido en benchmarks/ (qwen2.5:3b 2,53 GiB; llama3.2:3b 3,77 GiB
# con 16k de contexto).
RUNTIME_OVERHEAD = 256 * MIB
# Lo que el driver y el escritorio suelen ocupar en la GPU.
GPU_RESERVE = 512 * MIB


def kv_cache_bytes(model_info: dict, num_ctx: int) -> int | None:
    """Tamaño de la caché K/V en f16 para num_ctx tokens, según la arquitectura."""
    arch = model_info.get("general.architecture")

    def get(key: str):
        value = model_info.get(f"{arch}.{key}")
        return max(value) if isinstance(value, list) else value

    layers, heads, kv_heads = get("block_count"), get("attention.head_count"), get("attention.head_count_kv")
    embedding = get("embedding_length")
    key = get("attention.key_length") or (embedding // heads if embedding and heads else None)
    value = get("attention.value_length") or key
    if not (layers and kv_heads and key):
        return None
    return layers * kv_heads * (key + value) * num_ctx * 2


def vram_estimate(weights: int, model_info: dict, num_ctx: int) -> int | None:
    kv = kv_cache_bytes(model_info, num_ctx)
    return None if kv is None else weights + kv + RUNTIME_OVERHEAD


def gpu_fit(estimate: int | None, weights: int, vram_total: float | None) -> str | None:
    """"gpu" si cabe entero, "parcial" si se reparte GPU/CPU, "cpu" si casi todo va a CPU."""
    if not vram_total:
        return None
    usable = vram_total - GPU_RESERVE
    if estimate is not None and estimate <= usable:
        return "gpu"
    return "parcial" if weights <= usable else "cpu"


def thinks(show: dict) -> bool:
    return "thinking" in show.get("capabilities", [])


def chat_params(show: dict, options: dict) -> dict:
    """Campos extra de /api/chat para este modelo.

    Los modelos que razonan gastan tokens pensando antes de responder y esos
    tokens cuentan para num_predict. A los que lo permiten se les desactiva el
    razonamiento para responder rápido. gpt-oss no permite desactivarlo: se
    pide el nivel bajo y se da más margen de salida para que no se quede sin
    tokens antes de empezar la respuesta.
    """
    if not thinks(show):
        return {"options": options}
    if show.get("details", {}).get("family") == "gptoss":
        return {"think": "low", "options": {**options, "num_predict": max(options["num_predict"], 2048)}}
    return {"think": False, "options": options}

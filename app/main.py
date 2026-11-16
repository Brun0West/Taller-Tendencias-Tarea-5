"""Asistente Académico FISI: página web + endpoint /preguntar (sin RAG).

Cada pregunta se envía al modelo junto con la base de conocimiento completa
(conocimiento.md) como mensaje de sistema. No hay búsqueda ni embeddings.
"""
import asyncio
import hashlib
import json
import os
import time
from collections import deque
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, StringConstraints

from . import hardware, modelos
from .prompt import build_system_prompt

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_FILE = Path(os.getenv("BASE_CONOCIMIENTO", ROOT / "conocimiento.md"))
PAGE = Path(__file__).parent / "static" / "index.html"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("MODELO", "qwen2.5:3b")  # modelo por defecto si la petición no elige otro
KEEP_ALIVE = os.getenv("KEEP_ALIVE", "30m")
# Mismos valores que benchmarks/ para poder comparar. num_ctx es obligatorio:
# la base ocupa ~12k tokens y el contexto por defecto de Ollama es menor, lo
# que haría que el modelo no vea parte de la base.
OPTIONS = {
    "num_ctx": int(os.getenv("NUM_CTX", "16384")),
    "num_predict": int(os.getenv("NUM_PREDICT", "384")),
    "temperature": float(os.getenv("TEMPERATURE", "0")),
}
WARMUP_QUESTION = "Hola"

ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sin límite de lectura: la primera petición carga el modelo y procesa la
    # base completa, lo que puede tardar bastante en una GPU pequeña.
    app.state.ollama = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))
    app.state.history = deque(maxlen=20)  # métricas de las últimas respuestas (sin preguntas)
    app.state.cpu = hardware.CpuSampler()
    app.state.show_cache = {}  # (modelo, digest) -> respuesta de /api/show
    yield
    await app.state.ollama.aclose()


app = FastAPI(title="Asistente Académico FISI", lifespan=lifespan)


class Pregunta(BaseModel):
    pregunta: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    modelo: ModelName | None = None
    stream: bool = False



class Precarga(BaseModel):
    modelo: ModelName | None = None


# ---------- Ollama: consultas auxiliares ----------

async def ollama_get(path: str) -> dict | None:
    try:
        return (await app.state.ollama.get(f"{OLLAMA_URL}{path}", timeout=5)).json()
    except (httpx.HTTPError, ValueError):
        return None


async def model_show(name: str, digest: str) -> dict:
    key = (name, digest)
    if key not in app.state.show_cache:
        try:
            response = await app.state.ollama.post(f"{OLLAMA_URL}/api/show", json={"model": name}, timeout=10)
            app.state.show_cache[key] = response.json()
        except (httpx.HTTPError, ValueError):
            return {}  # sin detalles: se trata como modelo de texto sin razonamiento; no se guarda en caché
    return app.state.show_cache[key]


async def installed_models() -> list[dict]:
    """Modelos instalados que generan texto, con su /api/show."""
    tags = await ollama_get("/api/tags")
    if tags is None:
        raise HTTPException(503, f"No se pudo conectar con Ollama en {OLLAMA_URL}. ¿Está corriendo el servicio?")
    models = tags.get("models", [])
    shows = await asyncio.gather(*(model_show(m["name"], m["digest"]) for m in models))
    return [{**m, "show": s} for m, s in zip(models, shows) if "completion" in s.get("capabilities", ["completion"])]


async def resolve_model(name: str | None) -> tuple[str, dict]:
    name = name or MODEL
    for model in await installed_models():
        if model["name"] == name:
            return name, model["show"]
    raise HTTPException(400, f"El modelo '{name}' no está instalado. Descárgalo con: ollama pull {name}")


def find_loaded(ps: dict | None, name: str) -> dict | None:
    return next((m for m in (ps or {}).get("models", []) if name in (m.get("name"), m.get("model"))), None)


def gpu_fraction(loaded: dict | None) -> float | None:
    """Fracción del modelo en VRAM según Ollama (1.0 = todo en GPU)."""
    return round(loaded["size_vram"] / loaded["size"], 2) if loaded and loaded.get("size") else None


# ---------- Chat ----------

def metrics(final: dict, ttft: float | None, num_ctx: int) -> dict:
    ns = 1e9
    eval_s = final.get("eval_duration", 0) / ns
    return {
        "modelo": final.get("model", MODEL),
        "total_s": round(final.get("total_duration", 0) / ns, 3),
        "ttft_s": round(ttft, 3) if ttft is not None else None,  # primer token de respuesta, medido aquí
        "carga_s": round(final.get("load_duration", 0) / ns, 3),
        "prefill_s": round(final.get("prompt_eval_duration", 0) / ns, 3),
        "tokens_entrada": final.get("prompt_eval_count"),
        "tokens_salida": final.get("eval_count"),  # incluye los de razonamiento
        "tokens_s": round(final.get("eval_count", 0) / eval_s, 2) if eval_s else None,
        "contexto": num_ctx,
        "fin": final.get("done_reason"),  # "length" = cortada por num_predict
    }


async def open_ollama_stream(pregunta: str, model: str, show: dict, num_predict: int | None = None) -> httpx.Response:
    # Se relee en cada petición para tomar los cambios de la base sin reiniciar.
    knowledge = KNOWLEDGE_FILE.read_text(encoding="utf-8")
    params = modelos.chat_params(show, OPTIONS)
    if num_predict is not None:
        params["options"] = {**params["options"], "num_predict": num_predict}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_system_prompt(knowledge)},
            {"role": "user", "content": pregunta},
        ],
        "stream": True,
        "keep_alive": KEEP_ALIVE,
        **params,
    }
    client = app.state.ollama
    request = client.build_request("POST", f"{OLLAMA_URL}/api/chat", json=body)
    try:
        response = await client.send(request, stream=True)
    except httpx.TransportError:
        raise HTTPException(503, f"No se pudo conectar con Ollama en {OLLAMA_URL}. ¿Está corriendo el servicio?")
    if response.status_code != 200:
        raw = await response.aread()
        await response.aclose()
        try:
            error = json.loads(raw)["error"]
        except (ValueError, KeyError):
            error = raw.decode(errors="replace")
        raise HTTPException(502, f"Ollama respondió {response.status_code}: {error}")
    return response


async def events(response: httpx.Response, started: float, record: bool = True) -> AsyncIterator[dict]:
    """Traduce el stream de Ollama a eventos {razonamiento} / {texto} / {metricas} / {error}."""
    ttft = None
    try:
        async for line in response.aiter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                yield {"error": chunk["error"]}
                return
            if chunk.get("done"):
                final = metrics(chunk, ttft, OPTIONS["num_ctx"])
                if record:
                    app.state.history.append({**final, "hora": time.time()})
                yield {"metricas": final}
                continue
            message = chunk.get("message", {})
            if thinking := message.get("thinking"):
                yield {"razonamiento": thinking}
            if text := message.get("content"):
                if ttft is None:
                    ttft = time.perf_counter() - started
                yield {"texto": text}
    finally:
        # Si el navegador corta la conexión, cerrar aquí detiene la generación.
        await response.aclose()


@app.post("/preguntar")
async def preguntar(p: Pregunta):
    """Responde con la base de conocimiento completa en el contexto.

    modelo: cualquiera de los instalados (GET /modelos); por defecto MODELO.
    stream=false (por defecto): JSON {"respuesta", "razonamiento", "metricas"}.
    stream=true: NDJSON, una línea por evento {"razonamiento"} / {"texto"} y al final {"metricas"}.
    """
    started = time.perf_counter()
    model, show = await resolve_model(p.modelo)
    response = await open_ollama_stream(p.pregunta, model, show)

    if p.stream:
        async def ndjson():
            async for event in events(response, started):
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(ndjson(), media_type="application/x-ndjson")

    parts, thoughts, final = [], [], {}
    async with aclosing(events(response, started)) as stream:
        async for event in stream:
            if "error" in event:
                raise HTTPException(502, f"Error de Ollama: {event['error']}")
            if "texto" in event:
                parts.append(event["texto"])
            elif "razonamiento" in event:
                thoughts.append(event["razonamiento"])
            else:
                final = event["metricas"]
    return {"respuesta": "".join(parts), "razonamiento": "".join(thoughts) or None, "metricas": final}


@app.post("/precargar")
async def precargar(p: Precarga):
    """Carga el modelo y procesa la base para que la primera pregunta sea rápida.

    Envía la base con una pregunta corta y pide un solo token: Ollama deja la
    base procesada en caché y las preguntas siguientes la reutilizan.
    """
    started = time.perf_counter()
    model, show = await resolve_model(p.modelo)
    response = await open_ollama_stream(WARMUP_QUESTION, model, show, num_predict=1)
    final = {}
    async with aclosing(events(response, started, record=False)) as stream:
        async for event in stream:
            if "error" in event:
                raise HTTPException(502, f"Error de Ollama: {event['error']}")
            final = event.get("metricas", final)
    return {"modelo": model, "carga_s": final.get("carga_s"), "prefill_s": final.get("prefill_s"), "total_s": final.get("total_s")}


# ---------- Página, estado y métricas ----------

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(PAGE)


@app.get("/modelos")
async def listar_modelos():
    """Modelos instalados con una estimación de si caben en la GPU con el contexto configurado."""
    models, ps, gpu = await asyncio.gather(installed_models(), ollama_get("/api/ps"), hardware.gpu())
    vram_total = gpu["vram_total"] if gpu else None
    result = []
    for m in models:
        details, show = m.get("details", {}), m["show"]
        estimate = modelos.vram_estimate(m["size"], show.get("model_info", {}), OPTIONS["num_ctx"])
        loaded = find_loaded(ps, m["name"])
        result.append({
            "nombre": m["name"],
            "familia": details.get("family"),
            "parametros": details.get("parameter_size"),
            "cuantizacion": details.get("quantization_level"),
            "tamano": m["size"],
            "vram_estimada": estimate,
            "ajuste": modelos.gpu_fit(estimate, m["size"], vram_total),
            "razona": modelos.thinks(show),
            "cargado": loaded is not None,
            "gpu": gpu_fraction(loaded),
        })
    result.sort(key=lambda m: m["tamano"])
    return {"predeterminado": MODEL, "contexto": OPTIONS["num_ctx"], "vram_total": vram_total, "modelos": result}


@app.get("/estado")
async def estado(modelo: str = MODEL):
    """Estado de Ollama y de un modelo, para el indicador de la página."""
    ps = await ollama_get("/api/ps")
    loaded = find_loaded(ps, modelo)
    return {"ollama": ps is not None, "modelo": modelo, "cargado": loaded is not None, "gpu": gpu_fraction(loaded)}


@app.get("/metricas")
async def metricas(modelo: str = MODEL):
    """Datos del panel "estadísticas para nerds": GPU, sistema, modelo y respuestas."""
    ps, version, gpu = await asyncio.gather(ollama_get("/api/ps"), ollama_get("/api/version"), hardware.gpu())
    loaded = find_loaded(ps, modelo) or {}
    details = loaded.get("details", {})
    knowledge = KNOWLEDGE_FILE.read_bytes()
    return {
        "gpu": gpu,
        "sistema": hardware.system(app.state.cpu),
        "ollama": {
            "conectado": ps is not None,
            "version": (version or {}).get("version"),
            "modelo": modelo,
            "cargado": bool(loaded),
            "tamano": loaded.get("size"),
            "vram": loaded.get("size_vram"),
            "gpu": gpu_fraction(loaded),
            "contexto": loaded.get("context_length"),
            "parametros": details.get("parameter_size"),
            "cuantizacion": details.get("quantization_level"),
            "expira": loaded.get("expires_at"),
            "en_memoria": [m.get("name") for m in (ps or {}).get("models", [])],
        },
        "config": {**OPTIONS, "keep_alive": KEEP_ALIVE},
        "base": {
            "archivo": KNOWLEDGE_FILE.name,
            "bytes": len(knowledge),
            "sha256": hashlib.sha256(knowledge).hexdigest()[:12],
        },
        "ultima": app.state.history[-1] if app.state.history else None,
        "historial": list(app.state.history),
    }

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

from . import hardware
from .prompt import build_system_prompt

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_FILE = Path(os.getenv("BASE_CONOCIMIENTO", ROOT / "conocimiento.md"))
PAGE = Path(__file__).parent / "static" / "index.html"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("MODELO", "qwen2.5:3b")
KEEP_ALIVE = os.getenv("KEEP_ALIVE", "30m")
# Mismos valores que benchmarks/ para poder comparar. num_ctx es obligatorio:
# la base ocupa ~12k tokens y el contexto por defecto de Ollama es menor, lo
# que haría que el modelo no vea parte de la base.
OPTIONS = {
    "num_ctx": int(os.getenv("NUM_CTX", "16384")),
    "num_predict": int(os.getenv("NUM_PREDICT", "384")),
    "temperature": float(os.getenv("TEMPERATURE", "0")),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sin límite de lectura: la primera petición carga el modelo y procesa la
    # base completa, lo que puede tardar bastante en una GPU pequeña.
    app.state.ollama = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))
    app.state.history = deque(maxlen=20)  # métricas de las últimas respuestas (sin preguntas)
    app.state.cpu = hardware.CpuSampler()
    yield
    await app.state.ollama.aclose()


app = FastAPI(title="Asistente Académico FISI", lifespan=lifespan)


class Pregunta(BaseModel):
    pregunta: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    stream: bool = False


def metrics(final: dict, ttft: float | None) -> dict:
    ns = 1e9
    eval_s = final.get("eval_duration", 0) / ns
    return {
        "modelo": final.get("model", MODEL),
        "total_s": round(final.get("total_duration", 0) / ns, 3),
        "ttft_s": round(ttft, 3) if ttft is not None else None,  # medido en el servidor
        "carga_s": round(final.get("load_duration", 0) / ns, 3),
        "prefill_s": round(final.get("prompt_eval_duration", 0) / ns, 3),
        "tokens_entrada": final.get("prompt_eval_count"),
        "tokens_salida": final.get("eval_count"),
        "tokens_s": round(final.get("eval_count", 0) / eval_s, 2) if eval_s else None,
        "contexto": OPTIONS["num_ctx"],
        "fin": final.get("done_reason"),  # "length" = cortada por num_predict
    }


async def open_ollama_stream(client: httpx.AsyncClient, pregunta: str) -> httpx.Response:
    # Se relee en cada petición para tomar los cambios de la base sin reiniciar.
    knowledge = KNOWLEDGE_FILE.read_text(encoding="utf-8")
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt(knowledge)},
            {"role": "user", "content": pregunta},
        ],
        "stream": True,
        "options": OPTIONS,
        "keep_alive": KEEP_ALIVE,
    }
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
        hint = f" Descárgalo con: ollama pull {MODEL}" if response.status_code == 404 else ""
        raise HTTPException(502, f"Ollama respondió {response.status_code}: {error}.{hint}")
    return response


async def events(response: httpx.Response, started: float) -> AsyncIterator[dict]:
    """Traduce el stream de Ollama a eventos {texto} / {metricas} / {error}."""
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
                final = metrics(chunk, ttft)
                app.state.history.append({**final, "hora": time.time()})
                yield {"metricas": final}
            elif text := chunk.get("message", {}).get("content"):
                if ttft is None:
                    ttft = time.perf_counter() - started
                yield {"texto": text}
    finally:
        # Si el navegador corta la conexión, cerrar aquí detiene la generación.
        await response.aclose()


@app.post("/preguntar")
async def preguntar(p: Pregunta):
    """Responde con la base de conocimiento completa en el contexto.

    stream=false (por defecto): JSON {"respuesta", "metricas"}.
    stream=true: NDJSON, una línea por evento {"texto"} y al final {"metricas"}.
    """
    started = time.perf_counter()
    response = await open_ollama_stream(app.state.ollama, p.pregunta)

    if p.stream:
        async def ndjson():
            async for event in events(response, started):
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(ndjson(), media_type="application/x-ndjson")

    parts, final = [], {}
    async with aclosing(events(response, started)) as stream:
        async for event in stream:
            if "error" in event:
                raise HTTPException(502, f"Error de Ollama: {event['error']}")
            if "texto" in event:
                parts.append(event["texto"])
            else:
                final = event["metricas"]
    return {"respuesta": "".join(parts), "metricas": final}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(PAGE)


async def ollama_get(path: str) -> dict | None:
    try:
        return (await app.state.ollama.get(f"{OLLAMA_URL}{path}", timeout=5)).json()
    except (httpx.HTTPError, ValueError):
        return None


def find_model(ps: dict | None) -> dict | None:
    models = (ps or {}).get("models", [])
    return next((m for m in models if MODEL in (m.get("name"), m.get("model"))), None)


def gpu_fraction(model: dict | None) -> float | None:
    """Fracción del modelo en VRAM según Ollama (1.0 = todo en GPU)."""
    return round(model["size_vram"] / model["size"], 2) if model and model.get("size") else None


@app.get("/estado")
async def estado():
    """Estado de Ollama y del modelo, para el indicador de la página."""
    ps = await ollama_get("/api/ps")
    model = find_model(ps)
    return {"ollama": ps is not None, "modelo": MODEL, "cargado": model is not None, "gpu": gpu_fraction(model)}


@app.get("/metricas")
async def metricas():
    """Datos del panel "estadísticas para nerds": GPU, sistema, modelo y respuestas."""
    ps, version, gpu = await asyncio.gather(ollama_get("/api/ps"), ollama_get("/api/version"), hardware.gpu())
    model = find_model(ps) or {}
    details = model.get("details", {})
    knowledge = KNOWLEDGE_FILE.read_bytes()
    return {
        "gpu": gpu,
        "sistema": hardware.system(app.state.cpu),
        "ollama": {
            "conectado": ps is not None,
            "version": (version or {}).get("version"),
            "modelo": MODEL,
            "cargado": bool(model),
            "tamano": model.get("size"),
            "vram": model.get("size_vram"),
            "gpu": gpu_fraction(model),
            "contexto": model.get("context_length"),
            "parametros": details.get("parameter_size"),
            "cuantizacion": details.get("quantization_level"),
            "expira": model.get("expires_at"),
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

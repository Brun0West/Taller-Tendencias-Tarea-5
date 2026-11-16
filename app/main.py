"""Asistente Académico FISI: endpoint /preguntar (sin RAG).

Cada pregunta se envía al modelo junto con la base de conocimiento completa
(conocimiento.md) como mensaje de sistema. No hay búsqueda ni embeddings.
"""
import json
import os
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints

from .prompt import build_system_prompt

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_FILE = Path(os.getenv("BASE_CONOCIMIENTO", ROOT / "conocimiento.md"))
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
    yield
    await app.state.ollama.aclose()


app = FastAPI(title="Asistente Académico FISI", lifespan=lifespan)


class Pregunta(BaseModel):
    pregunta: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    stream: bool = False


def metrics(final: dict) -> dict:
    ns = 1e9
    eval_s = final.get("eval_duration", 0) / ns
    return {
        "modelo": final.get("model", MODEL),
        "total_s": round(final.get("total_duration", 0) / ns, 3),
        "carga_s": round(final.get("load_duration", 0) / ns, 3),
        "tokens_entrada": final.get("prompt_eval_count"),
        "tokens_salida": final.get("eval_count"),
        "tokens_s": round(final.get("eval_count", 0) / eval_s, 2) if eval_s else None,
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


async def events(response: httpx.Response) -> AsyncIterator[dict]:
    """Traduce el stream de Ollama a eventos {texto} / {metricas} / {error}."""
    try:
        async for line in response.aiter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                yield {"error": chunk["error"]}
                return
            if chunk.get("done"):
                yield {"metricas": metrics(chunk)}
            elif text := chunk.get("message", {}).get("content"):
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
    response = await open_ollama_stream(app.state.ollama, p.pregunta)

    if p.stream:
        async def ndjson():
            async for event in events(response):
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(ndjson(), media_type="application/x-ndjson")

    parts, final = [], {}
    async with aclosing(events(response)) as stream:
        async for event in stream:
            if "error" in event:
                raise HTTPException(502, f"Error de Ollama: {event['error']}")
            if "texto" in event:
                parts.append(event["texto"])
            else:
                final = event["metricas"]
    return {"respuesta": "".join(parts), "metricas": final}


# Web del asistente — Pedro

Página web y endpoint `/preguntar` que envían la pregunta del alumno **junto con la base de conocimiento completa** (`conocimiento.md`, de Diego) al modelo local de Ollama. No hay RAG: ni búsqueda, ni embeddings, ni fragmentación. El modelo recibe siempre la base entera como mensaje de sistema.

```
Navegador ──POST /preguntar──▶ FastAPI ──/api/chat (system = prompt + base)──▶ Ollama (qwen2.5:3b en GPU)
          ◀── NDJSON (texto en streaming + métricas) ──┘
```

## Requisitos

- Linux, Python 3.10 o superior.
- [Ollama](https://ollama.com) en ejecución. En Arch: `sudo pacman -S ollama-cuda` (NVIDIA) y `sudo systemctl start ollama`.
- El modelo: `ollama pull qwen2.5:3b`.

Se eligió `qwen2.5:3b` por las mediciones de Adrian (`benchmarks/informe_adrian.md`): genera alrededor de 20 % más tokens/s que `llama3.2:3b` y ocupa menos VRAM (2,5 GiB frente a 3,8 GiB con 16k de contexto). En una GPU de 4 GB solo Qwen cabe entero.

## Ejecutar

Desde la raíz del repositorio:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt
uvicorn app.main:app --reload
```

Abrir <http://127.0.0.1:8000>. Para abrirla desde otros equipos de la red: `uvicorn app.main:app --host 0.0.0.0`.

La primera pregunta tarda más porque Ollama carga el modelo en la GPU y procesa la base completa (~12k tokens). Las siguientes reutilizan ese prefijo ya procesado y responden mucho más rápido mientras el modelo siga cargado (`KEEP_ALIVE`).

Medido de punta a punta sobre `/preguntar` en una laptop con GTX 1650 de 4 GB (i5-10300H, 8 GB de RAM), con `qwen2.5:3b` 100 % en GPU (2,5 GiB de VRAM):

| Petición | Primer token | Total | Generación |
|---|---:|---:|---:|
| Primera (modelo sin cargar) | 98 s (89,5 s procesando la base) | 99 s | 33 tokens/s |
| Siguientes (modelo cargado) | 0,3–0,6 s | 1,4–3,6 s | ~42 tokens/s |

Son pocas peticiones secuenciales, no una prueba de carga. Las mediciones comparables con protocolo formal están en `benchmarks/`.

## Configuración (variables de entorno)

| Variable | Por defecto | Para qué |
|---|---|---|
| `MODELO` | `qwen2.5:3b` | Modelo de Ollama. `MODELO=llama3.2:3b` para comparar. |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Servidor de Ollama. |
| `NUM_CTX` | `16384` | Ventana de contexto. **No bajarla de ~13 000**: la base ocupa ~12k tokens y el resto quedaría fuera del contexto. |
| `NUM_PREDICT` | `384` | Máximo de tokens de respuesta. |
| `TEMPERATURE` | `0` | 0 = respuestas deterministas. |
| `KEEP_ALIVE` | `30m` | Tiempo que el modelo sigue en memoria tras la última pregunta. |
| `BASE_CONOCIMIENTO` | `conocimiento.md` | Ruta de la base. Se relee en cada pregunta: editarla no requiere reiniciar. |

`NUM_CTX`, `NUM_PREDICT` y `TEMPERATURE` usan los mismos valores que las mediciones de `benchmarks/`, para que se puedan comparar.

## API

### `POST /preguntar`

```bash
curl -s http://127.0.0.1:8000/preguntar \
  -H 'Content-Type: application/json' \
  -d '{"pregunta": "¿Cuántos créditos puede llevar un estudiante regular?"}'
```

```json
{
  "respuesta": "Un estudiante regular puede llevar entre 12 y 26 créditos por semestre, o menos de 12 si le faltan menos de 12 créditos para terminar la carrera.",
  "metricas": {
    "modelo": "qwen2.5:3b", "total_s": 1.688, "ttft_s": 0.664, "carga_s": 0.051, "prefill_s": 0.372,
    "tokens_entrada": 11830, "tokens_salida": 45, "tokens_s": 42.7, "contexto": 16384, "fin": "stop"
  }
}
```

- `pregunta`: de 1 a 1000 caracteres (si no, devuelve 422).
- `"stream": true` devuelve NDJSON, un objeto por línea: `{"texto": "…"}` mientras se genera y `{"metricas": {…}}` al final. Es lo que usa la página.
- `fin: "length"` significa que la respuesta se cortó al llegar a `NUM_PREDICT`.
- Errores: `503` si Ollama no responde y `502` si Ollama devuelve un error (por ejemplo, modelo no descargado).

### `GET /estado`

`{"ollama": true, "modelo": "qwen2.5:3b", "cargado": true, "gpu": 1.0}`. `gpu` es la fracción del modelo en VRAM según Ollama: `1.0` = todo en GPU, `0` = solo CPU.

### `GET /metricas`

Datos del panel **Estadísticas para nerds** (botón de actividad en la barra superior de la página):

- `gpu`: uso, VRAM, temperatura, potencia y relojes. NVIDIA vía `nvidia-smi`; AMD vía sysfs. `null` si no hay GPU compatible.
- `sistema`: CPU, RAM, swap, carga y memoria residente de los procesos de Ollama (`/proc`).
- `ollama`: versión, modelo cargado, VRAM asignada, contexto, cuantización y cuándo se descarga.
- `ultima` / `historial`: métricas de las últimas 20 respuestas. Se guardan en memoria, **sin el texto de las preguntas**, y se pierden al reiniciar.
- `base`: tamaño y SHA-256 de la base usada.

## Archivos

| Archivo | Contenido |
|---|---|
| `main.py` | FastAPI: `/`, `/preguntar`, `/estado`, `/metricas`. |
| `prompt.py` | Prompt de sistema. **Provisional**: Bruno lo reemplaza con las restricciones temáticas y anti-inyección. |
| `hardware.py` | Lecturas de GPU y sistema para `/metricas`. |
| `static/index.html` | Página completa (HTML, CSS y JS sin dependencias ni paso de build). |

## Notas

- Cada pregunta es independiente: no se envía historial de la conversación.
- La respuesta del modelo se muestra como texto con formato Markdown básico (negritas, listas). Se construye con nodos DOM, nunca con `innerHTML`, para que una respuesta manipulada no pueda inyectar HTML en la página.
- Lo que queda pendiente para el equipo: integrar el prompt de Bruno en `prompt.py` y que Adrian repita sus mediciones sobre `/preguntar` (las métricas de la respuesta ya incluyen tiempo al primer token, prefill y tokens/s).

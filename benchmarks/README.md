# Mediciones locales — Adrian Palacios

Este directorio corresponde a la comparación de CPU, RAM, GPU, tiempo de respuesta y tokens por segundo de dos modelos. No implementa la web ni sustituye las pruebas de seguridad del equipo.

Entregable: [informe y tabla comparativa](informe_adrian.md). Evidencias: [resultados finales y piloto](resultados/). Reproducción: `medir_modelos.py`.

## Dependencias del trabajo

| Trabajo | ¿Bloquea estas mediciones? | Estado observado al iniciar |
|---|---|---|
| Diego: base de conocimiento | Sí, para una carga representativa | `conocimiento.md` disponible en el commit `da0fb58`; contiene reglamento, calendario, dos sílabos y 20 preguntas frecuentes. No se auditó su vigencia jurídica ni académica. |
| Pedro: instalar Ollama y descargar modelos | Se necesita el runtime; no se necesita la web | Ollama 0.30.10 ya funciona. Las mediciones usan su API directamente y descargan dos modelos en un servidor separado. |
| Pedro: endpoint `/preguntar` y página | No | No están en el repositorio al iniciar. Queda pendiente medir el recorrido completo navegador → web → LLM cuando existan. |
| Richard: arquitectura e infraestructura | No | Hay diagramas iniciales en `docs/diagrams/`. Puede usar los resultados para su propuesta; no representan una prueba de 20 usuarios. |
| Bruno: restricciones temáticas e inyección | No para esta comparación inicial | Su prompt definitivo puede cambiar la latencia y el número de tokens. Después corresponde repetir las mediciones con ese prompt integrado. |

Por tanto, las tareas tienen dependencias parciales y pueden avanzar en paralelo. La dependencia principal aquí es **base disponible + runtime + dos modelos**, no la aplicación web terminada.

## Cómo repetir

Requisitos: Linux, Python 3, Ollama local y `lscpu`/`lspci`. No hay paquetes Python adicionales. Debe poderse leer `/proc/<pid>/smaps_rollup` del servidor y sus procesos para medir PSS; ejecutar el servidor con el mismo usuario facilita esta lectura.

En una terminal, inicie un servidor dedicado (mantenerlo abierto):

```bash
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

En otra terminal, identifique su PID y descargue los modelos si dispone de espacio:

```bash
pgrep -a ollama
OLLAMA_HOST=127.0.0.1:11435 ollama pull qwen2.5:3b
OLLAMA_HOST=127.0.0.1:11435 ollama pull llama3.2:3b
```

Desde la raíz del repositorio, sustituya `PID` por el número de `ollama serve`. Cada ejecución requiere un directorio de salida nuevo:

```bash
python3 benchmarks/medir_modelos.py --model qwen2.5:3b --server-pid PID --url http://127.0.0.1:11435 --output benchmarks/resultados/nueva-qwen
python3 benchmarks/medir_modelos.py --model llama3.2:3b --server-pid PID --url http://127.0.0.1:11435 --output benchmarks/resultados/nueva-llama
```

Los resultados incluidos usaron un almacén temporal en RAM (`OLLAMA_MODELS=/dev/shm/fisi-benchmark-models`) por falta de disco. Para repetir exactamente esa variante, añada esa variable al iniciar el servidor y descargue/mida un solo modelo cada vez. Los pesos ocupan RAM incluso antes de cargar el modelo: el informe distingue este consumo del PSS de los procesos. No mezclar estas cifras con un arranque desde SSD.

## Protocolo e interpretación

- Se envía el contenido **completo** de `conocimiento.md` como contexto en cada petición, sin búsqueda, embeddings ni RAG. Se guarda su SHA-256 para identificar la versión exacta.
- Tres preguntas fijas: créditos de matrícula, calendario 2026-II y evaluación de un sílabo. Cada una se repite tres veces, secuencialmente, con el modelo residente. Antes se hace una petición separada con el modelo descargado de memoria; se conserva como `cold` y se excluye del resumen de las nueve peticiones `warm`.
- `cold` significa modelo no residente en Ollama; no significa caché de disco vacía. Las peticiones `warm` permiten reutilizar el prefijo del contexto, como un asistente con base fija. No se comparan contra un prefill completo en cada turno.
- Configuración común final: contexto de 16 384 tokens, máximo de 384 tokens de salida, temperatura 0, semilla 42, seis hilos de CPU, una petición a la vez. Cada modelo conserva su plantilla nativa. La tokenización difiere: mismos textos no implican igual cantidad de tokens. El script comprueba margen de contexto; también deben revisarse los registros del servidor para descartar truncamiento.
- **Tiempo de respuesta:** reloj monotónico desde antes de enviar HTTP hasta recibir el último evento del stream. Incluye carga, procesamiento del contexto y generación; no incluye descarga de modelos ni tiempo de navegador/web.
- **TTFT:** segundos hasta el primer fragmento de texto no vacío recibido por streaming. Es una medición del cliente, no una medición interna exacta del primer token.
- **Tokens/s de generación:** `eval_count / (eval_duration / 1e9)`. No incluye procesamiento del prompt; no equivale a tokens/s de todo el recorrido. Se guardan por separado tokens de entrada y tiempo de evaluación del prompt.
- **RAM:** se muestrean cada 250 ms el RSS y PSS del servidor dedicado y sus descendientes. RSS suma páginas residentes y puede contar páginas compartidas más de una vez; PSS reparte las páginas compartidas proporcionalmente y es preferible para atribuir memoria. Los picos son observados por muestreo, no máximos absolutos garantizados.
- También se conserva RAM total usada (`MemTotal − MemAvailable`) y VRAM total ocupada de la GPU desde sysfs. Ambas incluyen otras aplicaciones. La VRAM asignada al modelo por Ollama se registra aparte en `/api/ps` (`size_vram`); no equivale a la lectura global de sysfs ni a RAM de CPU.
- El informe usa medianas y rangos de las nueve solicitudes `warm`, manteniendo el resultado `cold` por separado. No son intervalos de confianza ni pruebas de carga concurrente.

## Evidencia

Cada modelo produce `metadata.json`, un JSON por petición con respuesta, métricas originales de Ollama, muestras de memoria y `/api/ps`, además de `mediciones.csv` y `resumen.json`. La descarga y la carga inicial no se confunden con la velocidad de generación. Se conservan respuestas para revisar que se ejecutó la carga académica prevista; esto no constituye una auditoría de calidad ni de seguridad.

Las carpetas `resultados/llama3.2-3b/` y `resultados/qwen2.5-3b/` corresponden a la comparación final con 384 tokens máximos. `resultados/piloto-192/` conserva la ronda inicial: se excluye del informe porque las tres respuestas de Llama sobre el sílabo alcanzaron el límite de salida. Para reproducir ese piloto, use `--max-output 192`. `num_predict` es un máximo, no una longitud obligatoria.

Referencias técnicas: [API de generación de Ollama](https://docs.ollama.com/api/generate), [Llama 3.2 3B](https://ollama.com/library/llama3.2:3b), [Qwen 2.5 3B](https://ollama.com/library/qwen2.5:3b), [soporte de hardware](https://docs.ollama.com/gpu).

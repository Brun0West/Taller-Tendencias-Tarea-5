# Informe de mediciones locales — Adrian Palacios

**Tarea 5 · FISI–UNMSM · 15 de noviembre de 2026 (America/Lima)**

## Resultado y alcance

Se ejecutaron pruebas reales de `qwen2.5:3b` y `llama3.2:3b` en esta PC, usando Ollama y la base completa de Diego en el prompt, sin RAG. Ambos modelos se cargaron íntegramente en la GPU AMD mediante Vulkan, confirmado con el registro del servidor y `/api/ps`. La comparación final contiene 20 peticiones: una con el modelo no residente y nueve con el modelo residente por cada modelo. Se conservan otras 20 peticiones piloto por separado.

Las mediciones no estaban bloqueadas por la web: la base ya existía y Ollama estaba instalado. La web de Pedro y el prompt definitivo de Bruno son necesarios para medir después el sistema integrado. La matriz de dependencias y las instrucciones de reproducción están en [README.md](README.md). Los diagramas y la propuesta de infraestructura para 20 usuarios corresponden a Richard; estos resultados pueden servirle como evidencia local.

## Equipo utilizado

| Componente | Especificación observada |
|---|---|
| CPU | AMD Ryzen 5 3600, 6 núcleos y 12 hilos; frecuencia máxima informada de aproximadamente 4,2 GHz |
| RAM | 16 GB nominales; 15,54 GiB reconocidos por Linux |
| GPU | AMD Navi 33, PCI `1002:7480`, fabricante de placa XFX; familia Radeon RX 7600, 8 GB de VRAM nominales (7,98 GiB reportados por sysfs) |
| Controlador y aceleración | `amdgpu` y Vulkan/RADV; no se utilizó CUDA |
| Sistema | Zorin OS 17.3, kernel Linux 6.8.0-124-generic, x86_64 |
| Runtime | Ollama 0.30.10; servidor de prueba en `127.0.0.1:11435` |
| Disco | Aproximadamente 2,2 GiB libres al iniciar; pesos temporales en RAM por esta limitación |
| Swap | No configurada |

La etiqueta PCI agrupa varias variantes de Navi 33; no se usa como prueba del nombre comercial exacto de la tarjeta. La memoria y el uso efectivo de GPU sí se midieron. El servicio habitual de Ollama en el puerto 11434 y su modelo previo no fueron modificados.

## Condiciones de la prueba

Base: `conocimiento.md`, 40 079 bytes, commit de partida `da0fb583d57e5258e5a918a37c7597b0328100ac`. SHA-256: `6dff961868fdd8dcc396afc80362eef8236f2952d608a9b335327f91a6875dfd`. Incluye reglamento, calendario, dos sílabos y 20 preguntas frecuentes. Se usó tal como estaba; no se auditó su vigencia.

En cada petición se envió la base completa como contexto de sistema y la pregunta como prompt. Se mantuvo contexto de 16 384 tokens, salida máxima de 384 tokens, temperatura 0, semilla 42 y seis hilos de CPU configurados. Hubo un único modelo cargado y una sola petición en curso. Los dos modelos son instruct cuantizados en Q4_K_M y conservaron sus plantillas nativas.

Se consultó por créditos de matrícula, calendario 2026-II y fórmula de evaluación de Tendencias de Arquitectura de Software. Cada pregunta se repitió tres veces con el modelo residente, en el mismo orden para ambos modelos. No se transmitió historial de conversación; Ollama pudo reutilizar el prefijo de la base. La mediana de nueve consultas mezcla tres longitudes de respuesta, por lo que también se presentan tiempos por pregunta.

La primera petición de cada modelo se midió tras descargarlo de la memoria de Ollama. Es un arranque del modelo, no un arranque del sistema con cachés vacías: los pesos estaban en tmpfs y las bibliotecas/controlador podían estar en caché. Su tiempo no representa una carga desde SSD. La ronda piloto con máximo de 192 tokens se excluyó porque las respuestas del sílabo de Llama se cortaron; la ronda final de ambos modelos usa el mismo máximo de 384.

## Tabla comparativa final

Valores de consultas residentes: mediana; el rango es mínimo–máximo de las nueve peticiones. MiB = 2²⁰ bytes; GiB = 2³⁰ bytes.

| Indicador | Qwen 2.5 3B | Llama 3.2 3B |
|---|---:|---:|
| Parámetros reportados | 3.1B | 3.2B |
| Digest del modelo (prefijo) | `357c53fb659c` | `a80c4f17acd5` |
| Tamaño del modelo descargado | 1.80 GiB | 1.88 GiB |
| Tiempo total, modelo no residente (una petición) | 18.19 s | 28.62 s |
| Carga de modelo, primera petición (Ollama) | 2.83 s | 3.17 s |
| Procesamiento del prompt, primera petición | 14.60 s | 24.21 s |
| Tiempo total, modelo residente | 1.67 s (1.16–2.45) | 2.38 s (1.75–4.23) |
| Primer texto recibido, modelo residente (TTFT) | 0.66 s (0.50–0.71) | 0.66 s (0.59–0.71) |
| Velocidad de generación | 73.08 tokens/s (71.92–74.65) | 60.76 tokens/s (60.44–61.28) |
| RAM de procesos: mediana del pico PSS | 249.1 MiB | 262.3 MiB |
| RAM de procesos: mediana del pico RSS | 312.1 MiB | 324.9 MiB |
| VRAM asignada al modelo según Ollama | 2.53 GiB | 3.77 GiB |
| Pico global de RAM usada, mayor de las 10 peticiones | 9.46 GiB | 8.94 GiB |
| Pico global de VRAM usada, mayor de las 10 peticiones | 3.90 GiB | 5.08 GiB |
| Tokens de entrada reportados (mín.–máx.) | 11833–11847 | 11517–11529 |
| Respuestas finales detenidas por límite de salida | 0 | 0 |

Los picos de RAM/VRAM global incluyen escritorio, aplicaciones y pesos temporales. El PSS/RSS de los procesos no incluye los archivos en tmpfs que no estén mapeados; por tanto, no representa toda la memoria física requerida en esta variante. El almacén temporal ocupó aproximadamente 1,80 GiB para Qwen o 1,88 GiB para Llama, antes de contar el runtime. No se deben sumar mecánicamente estas cifras al PSS, porque podrían compartir páginas. La VRAM de Ollama es su asignación reportada, no la ocupación global de la tarjeta.

### Tiempo por tipo de consulta

| Consulta | Qwen: mediana total / tokens de salida | Llama: mediana total / tokens de salida |
|---|---:|---:|
| Créditos de matrícula | 1.21 s / 45 tokens | 1.82 s / 69 tokens |
| Fechas de 2026-II | 1.67 s / 70 tokens | 2.38 s / 104 tokens |
| Fórmula de evaluación | 2.43 s / 124 tokens | 4.20 s / 211 tokens |

El tiempo total se midió en el cliente con reloj monotónico hasta el último evento HTTP. La velocidad se calculó con `eval_count / (eval_duration / 1e9)`, usando las métricas de Ollama: mide generación, excluyendo el procesamiento del prompt. El TTFT es el tiempo hasta recibir el primer fragmento no vacío por streaming. El muestreo de memoria fue cada 250 ms: son máximos observados, no máximos absolutos garantizados. PSS reparte proporcionalmente las páginas compartidas; RSS puede contarlas más de una vez al sumar procesos.

## Observaciones de contenido

La revisión de las respuestas muestra que rendimiento y exactitud requieren evaluaciones separadas. Respecto de la base utilizada:

- Ambos modelos respondieron el rango de 12 a 26 créditos y la excepción de menos de 12 créditos al terminar la carrera.
- Qwen indicó correctamente las fechas de clases de 2026-II, pero respondió matrícula del 16 al 20 de marzo, correspondiente a 2026-I en la base; esta fija matrícula de 2026-II del 10 al 14 de agosto.
- Llama indicó las fechas de agosto, pero llamó miércoles al día 10, que la base identifica como lunes.
- Ambos reprodujeron los coeficientes `0.3·N1 + 0.4·N2 + 0.3·N3`, pero describieron incorrectamente componentes de las notas. Qwen redujo N1 al examen parcial y alteró N2. Llama empezó negando que existiera la fórmula, luego la reprodujo y atribuyó un 50 % a participación sin respaldo en la base.

Son observaciones de tres preguntas repetidas, no una tasa general de exactitud ni un informe de prompt injection. Las respuestas completas quedan disponibles para Bruno; su validación temática y de seguridad sigue siendo trabajo independiente.

## Interpretación y recomendación para esta PC

Qwen generó aproximadamente 20,3 % más tokens por segundo que Llama en las medianas observadas y utilizó menos VRAM. Es el candidato preferible **por rendimiento local** para continuar esta prueba de concepto en esta PC. Las respuestas de Llama fueron más largas; por eso la diferencia de tiempo total no puede atribuirse únicamente a velocidad del modelo. Las tokenizaciones también difieren.

Los dos modelos caben individualmente en la GPU y respondieron de manera interactiva con el contexto residente. El primer procesamiento de la base fue mucho más costoso que las consultas posteriores. Conservar el modelo y el prefijo de contexto puede beneficiar la experiencia, aunque ese resultado depende de cómo Pedro implemente las solicitudes y de que no se desplace la caché.

Al finalizar se detuvo el servidor de prueba y se eliminaron los pesos temporales de ambos modelos, liberando la RAM del almacén. Se conservaron los resultados y el script. Ollama habitual y su modelo previo `qwen2.5-coder:7b` siguen disponibles; los modelos 3B deben descargarse nuevamente para repetir la prueba.

Antes de considerar el asistente listo para alumnos, corresponde integrar el prompt y los controles de Bruno y repetir estas pruebas sobre `/preguntar`. Las mediciones actuales no cuantifican capacidad para 20 usuarios simultáneos, latencia de navegador, colas, consumo eléctrico ni rendimiento solo con CPU. El poco espacio libre de disco motivó el almacén temporal y debe resolverse si se quiere conservar los dos modelos instalados.

## Evidencias y reproducción

- [Especificaciones y configuración observadas](resultados/equipo.json).
- [CSV final de Qwen](resultados/qwen2.5-3b/mediciones.csv) y [resumen](resultados/qwen2.5-3b/resumen.json).
- [CSV final de Llama](resultados/llama3.2-3b/mediciones.csv) y [resumen](resultados/llama3.2-3b/resumen.json).
- `metadata.json` de cada modelo: versión, configuración, plantilla de prompt, huella de la base y digest del modelo. Los archivos por petición incluyen respuesta completa, muestras de memoria, métricas finales y `/api/ps`.
- [Extracto del registro de Ollama](resultados/ollama-evidencia.log): backend Vulkan, asignación de capas a GPU, tamaños de contexto y ausencia de truncamiento de entrada.
- [Script reproducible](medir_modelos.py) y [procedimiento](README.md).
- [Piloto con límite de 192 tokens](resultados/piloto-192/), excluido de la tabla final.

Referencias de interpretación: [API de generación de Ollama](https://docs.ollama.com/api/generate), [modelo Qwen 2.5 3B](https://ollama.com/library/qwen2.5:3b), [modelo Llama 3.2 3B](https://ollama.com/library/llama3.2:3b) y [soporte de GPU](https://docs.ollama.com/gpu). Los números de las tablas proceden de los archivos locales, no de cifras publicitarias de esas páginas.

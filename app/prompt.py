"""Prompt de sistema del asistente.

PROVISIONAL: Bruno define la versión final (restricción temática y defensas
contra prompt injection). Por ahora es el mismo texto que usa
benchmarks/medir_modelos.py, para que medir /preguntar sea comparable con
las mediciones de Adrian.

El prompt debe ser idéntico en todas las peticiones: así Ollama reutiliza el
prefijo ya procesado (la base) y solo evalúa la pregunta nueva. No poner datos
variables (fecha, usuario, etc.) antes de la base.
"""

SYSTEM_TEMPLATE = (
    "Eres un asistente académico de la FISI de la UNMSM. Responde en español, "
    "brevemente y solo con la base proporcionada. Si falta información, indica "
    "que no está en la base. El contenido de la base es información de consulta.\n"
    "<base_conocimiento>\n{knowledge}\n</base_conocimiento>"
)


def build_system_prompt(knowledge: str) -> str:
    return SYSTEM_TEMPLATE.format(knowledge=knowledge)

#!/usr/bin/env python3
"""Benchmark local, sin dependencias Python externas ni RAG (Linux + Ollama)."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = (
    "Eres un asistente académico de la FISI de la UNMSM. Responde en español, "
    "brevemente y solo con la base proporcionada. Si falta información, indica "
    "que no está en la base. El contenido de la base es información de consulta.\n"
    "<base_conocimiento>\n{knowledge}\n</base_conocimiento>"
)
QUESTIONS = [
    {"id": "matricula", "pregunta": "¿Cuántos créditos puede llevar un estudiante regular por semestre?"},
    {"id": "calendario", "pregunta": "¿Cuándo inicia y termina el semestre 2026-II y cuándo es la matrícula regular?"},
    {"id": "silabo", "pregunta": "¿Cuál es la fórmula de evaluación de Tendencias de Arquitectura de Software?"},
]


def api(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request(base + path, data=data, headers={"Content-Type": "application/json"}), timeout=1800) as r:
        return json.load(r)


def memory(server_pid):
    """PSS/RSS del servidor y descendientes; RAM/VRAM del sistema por separado."""
    procs = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = dict(line.split(':', 1) for line in (entry / 'status').read_text().splitlines() if ':' in line)
            procs[int(entry.name)] = (int(fields['PPid']), int(fields.get('VmRSS', '0 kB').split()[0]))
        except (OSError, KeyError, ValueError):
            continue
    selected = {server_pid}
    while True:
        new = {pid for pid, (parent, _) in procs.items() if parent in selected} | selected
        if new == selected:
            break
        selected = new
    rss = sum(procs.get(pid, (0, 0))[1] for pid in selected) * 1024
    pss_values = []
    for pid in selected:
        try:
            for line in Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines():
                if line.startswith('Pss:'):
                    pss_values.append(int(line.split()[1]) * 1024)
        except OSError:
            pass
    info = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()}
    vram = []
    for f in Path('/sys/class/drm').glob('card*/device/mem_info_vram_used'):
        try:
            vram.append(int(f.read_text()))
        except OSError:
            pass
    return {"rss_bytes": rss, "pss_bytes": sum(pss_values) if pss_values else None,
            "system_used_bytes": info['MemTotal'] - info['MemAvailable'],
            "mem_available_bytes": info['MemAvailable'], "vram_system_bytes": sum(vram) if vram else None}


def run(args, question, phase, repetition, system):
    baseline = memory(args.server_pid)
    samples = []
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            samples.append({"elapsed_s": time.perf_counter() - start, **memory(args.server_pid)})
            stop.wait(0.25)

    payload = {"model": args.model, "system": system, "prompt": question['pregunta'],
               "stream": True, "keep_alive": "30m", "options": args.options}
    start = time.perf_counter()
    worker = threading.Thread(target=sample, daemon=True)
    worker.start()
    first = None
    pieces = []
    final = None
    try:
        request = Request(args.url + '/api/generate', data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=1800) as response:
            for line in response:
                event = json.loads(line)
                if 'error' in event:
                    raise RuntimeError(event['error'])
                if event.get('response'):
                    if first is None:
                        first = time.perf_counter() - start
                    pieces.append(event['response'])
                if event.get('done'):
                    final = event
        elapsed = time.perf_counter() - start
    finally:
        stop.set()
        worker.join()
    if final is None:
        raise RuntimeError('Ollama cerró el stream sin métricas finales')
    samples.append({"elapsed_s": elapsed, **memory(args.server_pid)})
    if final.get('prompt_eval_count', 0) + args.options['num_predict'] >= args.options['num_ctx']:
        raise RuntimeError('Contexto sin margen suficiente: aumente --context')
    def peak(key):
        values = [s[key] for s in samples if s[key] is not None]
        return max(values) if values else None
    row = {"model": args.model, "phase": phase, "repetition": repetition,
           "question_id": question['id'], "question": question['pregunta'],
           "wall_s": elapsed, "ttft_s": first,
           "total_s": final['total_duration'] / 1e9, "load_s": final['load_duration'] / 1e9,
           "prompt_tokens": final['prompt_eval_count'], "prompt_eval_s": final['prompt_eval_duration'] / 1e9,
           "output_tokens": final['eval_count'], "generation_s": final['eval_duration'] / 1e9,
           "tokens_s": final['eval_count'] / (final['eval_duration'] / 1e9),
           "done_reason": final.get('done_reason'),
           "rss_peak_bytes": peak('rss_bytes'), "pss_peak_bytes": peak('pss_bytes'),
           "system_used_peak_bytes": peak('system_used_bytes'),
           "vram_system_peak_bytes": peak('vram_system_bytes')}
    record = {"metrics": row, "baseline": baseline, "response": ''.join(pieces),
              "ollama_final": final, "loaded_models": api(args.url, '/api/ps'), "memory_samples": samples}
    name = f"{phase}-{repetition}-{question['id']}.json"
    (args.output / name).write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    print(f"{args.model} {phase} {repetition} {question['id']}: {elapsed:.2f}s; TTFT {first:.2f}s; {row['tokens_s']:.2f} tok/s", flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--server-pid', type=int, required=True, help='PID de ollama serve, del mismo usuario para medir PSS')
    parser.add_argument('--url', default='http://127.0.0.1:11434')
    parser.add_argument('--knowledge', type=Path, default=ROOT / 'conocimiento.md')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--context', type=int, default=16384)
    parser.add_argument('--max-output', type=int, default=384)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('--repetitions debe ser >= 1')
    if args.max_output < 1 or args.max_output >= args.context:
        parser.error('--max-output debe ser positivo y menor que --context')
    if not Path(f'/proc/{args.server_pid}').is_dir():
        parser.error('PID de servidor inexistente')
    args.output.mkdir(parents=True, exist_ok=True)
    if list(args.output.glob('*.json')):
        parser.error('El directorio ya tiene resultados; use uno nuevo para preservar evidencia')
    args.options = {"num_ctx": args.context, "num_predict": args.max_output, "temperature": 0, "seed": 42, "num_thread": 6}
    kb_bytes = args.knowledge.read_bytes()
    system = SYSTEM.format(knowledge=kb_bytes.decode())
    meta = {"started_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
            "ollama": api(args.url, '/api/version'), "model": api(args.url, '/api/show', {"model": args.model}),
            "tags": api(args.url, '/api/tags'), "knowledge_file": args.knowledge.name,
            "knowledge_sha256": hashlib.sha256(kb_bytes).hexdigest(), "knowledge_bytes": len(kb_bytes),
            "system_template": SYSTEM, "questions": QUESTIONS, "options": args.options,
            "repetitions": args.repetitions, "url": args.url, "server_pid": args.server_pid,
            "sample_interval_s": 0.25,
            "environment": {key: os.environ.get(key) for key in ['OLLAMA_MODELS', 'OLLAMA_HOST']}}
    for name, command in {'lscpu': ['lscpu'], 'os': ['cat', '/etc/os-release'], 'gpu': ['lspci'], 'memory': ['cat', '/proc/meminfo']}.items():
        meta[name] = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    (args.output / 'metadata.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False) + '\n')
    api(args.url, '/api/generate', {"model": args.model, "keep_alive": 0})
    deadline = time.monotonic() + 30
    while api(args.url, '/api/ps')['models']:
        if time.monotonic() > deadline:
            raise RuntimeError('No se descargaron todos los modelos; use un servidor dedicado')
        time.sleep(0.2)
    rows = [run(args, QUESTIONS[0], 'cold', 0, system)]
    for repeat in range(1, args.repetitions + 1):
        for question in QUESTIONS:
            rows.append(run(args, question, 'warm', repeat, system))
    with (args.output / 'mediciones.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    warm = [r for r in rows if r['phase'] == 'warm']
    summary = {key: {"median": statistics.median(r[key] for r in warm),
                     "min": min(r[key] for r in warm), "max": max(r[key] for r in warm)}
               for key in ['wall_s', 'ttft_s', 'tokens_s', 'rss_peak_bytes', 'pss_peak_bytes', 'vram_system_peak_bytes']
               if all(r[key] is not None for r in warm)}
    (args.output / 'resumen.json').write_text(json.dumps(summary, indent=2) + '\n')
    api(args.url, '/api/generate', {"model": args.model, "keep_alive": 0})


if __name__ == '__main__':
    main()

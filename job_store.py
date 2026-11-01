"""Bounded, atomic local persistence for asset-library queue metadata."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from uuid import uuid4

ACTIVE = {"queued", "running", "cancelling"}
FINAL = {"completed", "failed", "cancelled"}
ENGINES = {"qwen", "qwen_turbo8", "moss"}


def known_engine(value):
    return isinstance(value, str) and (value in ENGINES or value.startswith("bbapi:") and 6 < len(value) <= 206)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def from_timestamp(value):
    try:
        number = float(value)
        if number > 100_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def elapsed(job):
    try:
        start = datetime.fromisoformat(job["started_at"])
        end = datetime.fromisoformat(job["completed_at"]) if job.get("completed_at") else datetime.now(timezone.utc)
        return round(max(0, (end - start).total_seconds()), 1)
    except (TypeError, ValueError, KeyError):
        return 0


def graph_metadata(graph):
    titles = {str(key): item.get("_meta", {}).get("title") or item.get("class_type", str(key))
              for key, item in graph.items() if isinstance(item, dict)}
    samplers = [str(key) for key, item in graph.items() if item.get("class_type") in ("KSampler", "SamplerCustomAdvanced", "BBAPIMultiReference", "BBAPIPromptToImage")]
    return {"node_count": len(titles), "node_titles": titles, "sampler_node": samplers[0] if samplers else None,
            "sampler_nodes": samplers}


def owned_prompt(prompt):
    """Recognize only explicit asset-library metadata or its saved prefixes."""
    if not isinstance(prompt, (list, tuple)) or len(prompt) < 4 or not isinstance(prompt[2], dict):
        return False
    extra = prompt[3] if isinstance(prompt[3], dict) else {}
    pnginfo = extra.get("extra_pnginfo")
    metadata = pnginfo.get("asset_library") if isinstance(pnginfo, dict) else None
    if isinstance(metadata, dict) and known_engine(metadata.get("engine")):
        return True
    for item in prompt[2].values():
        if not isinstance(item, dict):
            continue
        prefix = str(item.get("inputs", {}).get("filename_prefix", "")).replace("\\", "/")
        if item.get("class_type") == "SaveImage" and (prefix in {"资产库/" + engine for engine in ENGINES} or prefix.startswith("资产库/bbapi_")):
            return True
    return False


def infer_settings(prompt):
    graph, extra = prompt[2], prompt[3] or {}
    metadata = extra.get("extra_pnginfo", {}).get("asset_library", {})
    if isinstance(metadata, dict) and known_engine(metadata.get("engine")):
        settings = dict(metadata)
        settings.setdefault("original_prompt", settings.get("prompt", ""))
        return settings
    prefixes = {str(item.get("inputs", {}).get("filename_prefix", "")).replace("\\", "/") for item in graph.values()}
    engine = next((engine for engine in ("qwen_turbo8", "moss") if "资产库/" + engine in prefixes), "qwen")
    settings = {"engine": engine, "prompt": "", "asset_ids": [], "save_output": True}
    for item in graph.values():
        inputs, kind = item.get("inputs", {}), item.get("class_type")
        if kind in ("CodexQwenStudioEncoder", "CodexQwenReferenceEncoder") and isinstance(inputs.get("prompt"), str):
            settings["prompt"] = inputs["prompt"]
        elif kind == "KSampler":
            settings["seed"] = inputs.get("seed")
        elif kind == "RandomNoise":
            settings["seed"] = inputs.get("noise_seed")
        if kind in ("CodexQwenCanvasLatent", "EmptySD3LatentImage", "EmptyFlux2LatentImage"):
            settings.update({key: inputs[key] for key in ("width", "height") if isinstance(inputs.get(key), int)})
    settings["original_prompt"] = settings["prompt"]
    return settings


def new_job(identifier, settings, graph, workflow=None, client_id=None, number=None):
    engine_name = {"qwen": "千问", "qwen_turbo8": "千问 Turbo 8步", "moss": "MOSS"}.get(settings.get("engine"), str(settings.get("engine", "千问")).replace("bbapi:", "BBAPI · "))
    name = settings.get("name") or engine_name + " · " + str(settings.get("prompt", "人物组合"))[:32]
    return {"id": identifier, "prompt_id": identifier, "name": name, "engine": settings.get("engine", "qwen"),
            "status": "queued", "progress": None, "remaining_seconds": None,
            "created_at": now_iso(), "started_at": None, "completed_at": None, "images": [], "error": None,
            "settings": settings, "client_id": client_id, "number": number,
            "step": None, "value": None, "max": None, "node": None,
            **graph_metadata(graph), "_graph": graph, "_workflow": workflow}


def public_job(job):
    result = {key: value for key, value in job.items() if not key.startswith("_")}
    result["settings"] = dict(job.get("settings", {}))
    result["settings"].setdefault("original_prompt", result["settings"].get("prompt", ""))
    result["elapsed_seconds"] = elapsed(job)
    result["remaining_seconds"] = None
    result["remaining_scope"], result["estimated"] = None, False
    tracker = job.get("_sampling_progress", {})
    samples = tracker.get("samples", [])
    if job.get("status") == "running" and tracker.get("node") == job.get("node") and len(samples) >= 3:
        first_value, first_time = samples[0]
        last_value, last_time = samples[-1]
        maximum = tracker.get("max")
        if (all(isinstance(value, (int, float)) and math.isfinite(value) for value in (first_value, first_time, last_value, last_time, maximum))
                and last_value > first_value and last_time > first_time and maximum >= last_value):
            result["remaining_seconds"] = round((last_time - first_time) / (last_value - first_value) * (maximum - last_value), 1)
            result["remaining_scope"], result["estimated"] = "sampling", True
    return result


class JobStore:
    def __init__(self, data_dir=None):
        self.path = Path(data_dir) / "jobs.json" if data_dir else None
        self.jobs, self.client_id = OrderedDict(), "asset-library-" + uuid4().hex
        self._write_lock = threading.Lock()
        if self.path and self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("资产库任务记录格式无效，已保留原文件。")
            client = data.get("client_id", "")
            if isinstance(client, str) and client.startswith("asset-library-"):
                self.client_id = client
            for job in data.get("jobs", [])[-100:]:
                if isinstance(job, dict) and isinstance(job.get("prompt_id"), str) and known_engine(job.get("engine")):
                    # Monotonic samples belong to one process lifetime.
                    job.pop("_sampling_progress", None)
                    if job.get("_graph"):
                        job.update(graph_metadata(job["_graph"]))
                    self.jobs[job["prompt_id"]] = job

    def trim(self):
        while len(self.jobs) > 100:
            removable = next((key for key, job in self.jobs.items() if job.get("status") not in ACTIVE), None)
            if removable is None:
                raise ValueError("任务上限为100，请等待现有任务完成。")
            del self.jobs[removable]

    def snapshot(self):
        self.trim()
        return {"version": 1, "client_id": self.client_id, "jobs": list(self.jobs.values())}

    def save(self, snapshot):
        if self.path is None:
            return
        with self._write_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=".jobs-", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(snapshot, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)

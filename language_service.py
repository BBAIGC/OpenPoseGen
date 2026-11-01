"""Async text-only prompt refinement using the mature workbench rules."""
from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp

DEFAULTS = {"temperature": .3, "top_p": .9, "num_ctx": 8192, "max_tokens": 2048, "detail_level": 3}
TOKEN = re.compile(r"<image[1-9]\d*>|@?(?:参考图|图)[1-9]\d*|@(形象|角色|服装|姿态|场景|道具)")


class LanguageError(ValueError):
    def __init__(self, message, code="invalid_request", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def error_for_response(status, payload=None):
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    text = json.dumps(error, ensure_ascii=False).lower()
    if status == 402 or any(word in text for word in ("insufficient_quota", "quota_exceeded", "insufficient_balance", "credit", "balance")):
        return LanguageError("语言服务额度不足，请检查账户余额。", "balance", 402)
    if any(word in text for word in ("moderation", "content_policy", "content_filter", "safety", "policy_violation")):
        return LanguageError("语言服务未接受本次内容，请调整后重试。", "moderation", 400)
    if status in (401, 403):
        return LanguageError("语言服务认证失败，请检查密钥与权限。", "auth", status)
    if status == 429:
        return LanguageError("语言服务繁忙，请稍后重试。", "rate_limit", 429)
    if status == 404 or any(word in text for word in ("model_not_found", "model not found", "does not exist")):
        return LanguageError("所选语言模型不可用，请刷新模型列表。", "missing_model", 404)
    return LanguageError("语言服务请求失败，请检查连接后重试。", "network", 502)


def mature_rules():
    local = Path(__file__).resolve().parent
    candidates = [Path(os.environ["OPENPOSEGEN_H3_ROOT"]) / "prompt_optimize.py"] if os.environ.get("OPENPOSEGEN_H3_ROOT") else []
    candidates += [local.parent / "ComfyUI-H3-PromptDirector" / "prompt_optimize.py",
                   local.parent / "H3与生图工作台" / "prompt_optimize.py"]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise LanguageError("未找到成熟工作台的提示词规则，请检查H3插件。", "missing_rules", 503)
    spec = importlib.util.spec_from_file_location("_asset_mature_prompt_rules", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference_tokens(text):
    return Counter(match.group(0) for match in TOKEN.finditer(text))


def validate_references(source, text):
    expected, actual = reference_tokens(source), reference_tokens(text)
    if set(actual) - set(expected) or any(actual[token] < count for token, count in expected.items()):
        raise LanguageError("优化结果改变或遗漏了图片引用，原始提示词已保留。", "invalid_references", 422)
    # Reject additional unmarked numeric reference identities as well.
    slots = {int(value) for value in re.findall(r"(?:<image|@?参考图|@?图)([1-9]\d*)>?", source)}
    numeric = {int(value) for value in re.findall(r"(?:<image|@?参考图|@?图)([1-9]\d*)>?", text)}
    if numeric - slots:
        raise LanguageError("优化结果出现了未引用的图片编号，原始提示词已保留。", "invalid_references", 422)
    return text


def normalize(payload):
    if not isinstance(payload, dict):
        raise LanguageError("优化参数须为对象。")
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
        raise LanguageError("请填写不超过20000字的原始提示词。")
    engine = payload.get("engine", "local")
    if engine not in ("local", "bbapi"):
        raise LanguageError("请选择本地或云端语言服务。")
    model = payload.get("model", "")
    if not isinstance(model, str) or len(model) > 200 or any(ord(char) < 32 for char in model):
        raise LanguageError("语言模型编号无效。")
    settings = {key: payload.get(key, value) for key, value in DEFAULTS.items()}
    for key, low, high in (("temperature", 0, 2), ("top_p", .1, 1)):
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise LanguageError("语言模型采样参数无效。")
    for key, low, high in (("num_ctx", 4096, 65536), ("max_tokens", 128, 8192), ("detail_level", 1, 5)):
        if type(settings[key]) is not int or not low <= settings[key] <= high:
            raise LanguageError("上下文、输出上限或详细程度无效。")
    if settings["max_tokens"] + 256 > settings["num_ctx"]:
        raise LanguageError("请增大上下文或降低输出上限。")
    request_id = payload.get("request_id") or uuid4().hex
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
        raise LanguageError("优化请求编号无效。")
    assets = payload.get("assets", [])
    roles = payload.get("asset_roles", {})
    if not isinstance(assets, list) or len(assets) > 16 or not isinstance(roles, dict) or len(roles) > 16:
        raise LanguageError("素材引用信息无效。")
    safe_assets = []
    for asset in assets:
        if isinstance(asset, str):
            safe_assets.append({"id": asset[:200]})
        elif isinstance(asset, dict):
            safe_assets.append({key: str(asset[key])[:500] for key in ("id", "kind", "title", "prompt") if key in asset})
        else:
            raise LanguageError("素材引用信息无效。")
    safe_roles = {str(key)[:50]: value for key, value in roles.items() if isinstance(value, (str, int)) and not isinstance(value, bool)}
    if any(len(str(value)) > 200 for value in safe_roles.values()):
        raise LanguageError("素材角色编号无效。")
    target_engine = payload.get("target_engine", "qwen")
    target = "MOSS-Image" if target_engine == "moss" else "GPT Image 2/2.5" if str(target_engine).startswith("bbapi:") else "Image2.1"
    return {"prompt": prompt, "engine": engine, "model": model, "request_id": request_id,
            "assets": safe_assets, "asset_roles": safe_roles, "target": target, **settings}


class LanguageService:
    def __init__(self, provider=None, ollama_url="http://127.0.0.1:11434", rules=None):
        address = urlsplit(ollama_url)
        if address.scheme != "http" or address.hostname not in ("127.0.0.1", "localhost", "::1") or address.path not in ("", "/"):
            raise ValueError("本地语言服务地址必须为本机HTTP地址。")
        self.provider, self.ollama_url = provider, ollama_url.rstrip("/")
        self.rules = rules or mature_rules()
        self.jobs, self.tasks = OrderedDict(), {}
        self._models, self._models_at = None, 0
        self._closed = False

    async def _http(self, method, url, *, timeout=120, **kwargs):
        try:
            remote = urlsplit(url).hostname not in ('127.0.0.1', 'localhost', '::1')
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=remote) as session:
                async with session.request(method, url, allow_redirects=False, **kwargs) as response:
                    raw = bytearray()
                    while chunk := await response.content.read(65536):
                        raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            raise LanguageError("语言服务返回内容过大。", "invalid_response", 502)
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        if response.status >= 400:
                            raise error_for_response(response.status)
                        raise LanguageError("语言服务未返回有效结果。", "invalid_response", 502) from None
                    if response.status >= 400 or isinstance(data, dict) and data.get("error"):
                        raise error_for_response(response.status, data)
                    return data
        except asyncio.TimeoutError:
            raise LanguageError("提示词优化超时，原始输入已保留。", "timeout", 504) from None
        except aiohttp.ClientError:
            raise LanguageError("语言服务连接失败，请检查运行状态。", "network", 503) from None

    async def models(self, force=False):
        if not force and self._models is not None and time.monotonic() - self._models_at < 15:
            return self._models
        async def local():
            try:
                value = await self._http("GET", self.ollama_url + "/api/tags", timeout=3)
                items = [{"id": model["name"], "name": model["name"], "size": model.get("size"), "available": True, "vision": None}
                         for model in value.get("models", []) if isinstance(model.get("name"), str)]
                return items[:100], None
            except LanguageError as error:
                return [], {"engine": "local", "code": error.code, "message": str(error)}
        async def cloud():
            if self.provider is None or not self.provider.public_config()["configured"]:
                return [], None
            try:
                return await self.provider.language_models(), None
            except ValueError:
                return [], {"engine": "bbapi", "code": "models_unavailable", "message": "云端语言模型列表暂不可用，请检查连接或当前密钥权限。"}
        (installed, local_error), (remote, cloud_error) = await asyncio.gather(local(), cloud())
        names = [item["id"] for item in installed]
        preferred = next((name for name in names if "qwen" in name.lower() and re.search(r"(?:[:_-])9b(?:[-_:]|$)", name, re.I)), None)
        preferred = preferred or next((name for name in names if "qwen" in name.lower()), names[0] if names else "")
        result = {"local": installed, "cloud": remote, "default_local": preferred, "defaults": dict(DEFAULTS)}
        errors = [error for error in (local_error, cloud_error) if error]
        if errors:
            result["errors"] = errors
        self._models, self._models_at = result, time.monotonic()
        return result

    async def optimize(self, payload):
        settings = normalize(payload)
        models = await self.models()
        model = (settings["model"] or models["default_local"]) if settings["engine"] == "local" else settings["model"]
        available = {item["id"] for item in models[settings["engine"] if settings["engine"] == "local" else "cloud"]}
        if not model or model not in available:
            raise LanguageError("所选语言模型尚未安装或未开放，请刷新模型列表。", "missing_model", 404)
        language = self.rules.output_language(settings["prompt"])
        system = self.rules.optimization_system(settings["target"], self.rules.DEFAULT_RULES, settings["detail_level"], language)
        system += ("\n逐字保留原稿所有<imageN>、@图N和@形象/@服装/@姿态等引用标记，不增删，不重编号。"
                   "用摄影与自然动作描述人体；不得自行加入骨架、骨骼、人台或关节结构术语。"
                   "素材标题仅用于理解引用，不作为必须添加的人物、内容或场景。")
        context = {"原始提示词": settings["prompt"], "目标模型": settings["target"],
                   "素材角色": settings["asset_roles"], "素材信息": settings["assets"]}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
        started = time.monotonic()
        if settings["engine"] == "local":
            result = await self._http("POST", self.ollama_url + "/api/chat", timeout=600,
                json={"model": model, "messages": messages, "stream": False, "think": False, "keep_alive": 0,
                      "format": self.rules.OUTPUT_SCHEMA, "options": {"temperature": settings["temperature"], "top_p": settings["top_p"],
                      "num_ctx": settings["num_ctx"], "num_predict": settings["max_tokens"]}})
            content = result.get("message", {}).get("content", "")
            finish = result.get("done_reason")
            usage = {"input_tokens": result.get("prompt_eval_count"), "output_tokens": result.get("eval_count")}
        else:
            if self.provider is None:
                raise LanguageError("请先配置云端语言服务。", "auth", 401)
            config = self.provider.public_config()
            value = await asyncio.to_thread(self.provider.store.load, config["profile"], config["base_url"])
            body = {"model": model, "messages": messages, "stream": False}
            reasoning = bool(re.match(r"(?:o[134](?:-|$)|gpt-[56](?:[.-]|$))", model, re.I))
            body["max_completion_tokens" if reasoning else "max_tokens"] = settings["max_tokens"]
            if not reasoning:
                body.update(temperature=settings["temperature"], top_p=settings["top_p"])
            result = await self._http("POST", value["base_url"] + "/v1/chat/completions", timeout=180,
                                      headers={"Authorization": "Bearer " + value["api_key"]}, json=body)
            choices = result.get("choices", [])
            if not choices:
                raise LanguageError("云端语言模型未返回正文。", "invalid_response", 502)
            content, finish = choices[0].get("message", {}).get("content", ""), choices[0].get("finish_reason")
            usage = result.get("usage", {})
        if finish in ("length", "max_tokens"):
            raise LanguageError("优化结果达到输出上限，请提高上限后重试。", "truncated", 422)
        if finish == "content_filter":
            raise LanguageError("语言服务未接受本次内容，请调整后重试。", "moderation", 400)
        try:
            text = self.rules.final_prompt(content, settings["target"])
        except ValueError:
            raise LanguageError("优化结果格式无效，原始输入已保留。", "invalid_response", 422) from None
        if len(text) > 20000:
            raise LanguageError("优化结果过长，原始输入已保留。", "invalid_response", 422)
        validate_references(settings["prompt"], text)
        if self.rules.language_mismatch(text, language, settings["prompt"]):
            raise LanguageError("优化结果未遵守原稿语言，原始输入已保留。", "invalid_response", 422)
        return {"text": text, "model": model, "elapsed_seconds": round(time.monotonic() - started, 2), "usage": usage}

    def _public(self, identifier):
        value = self.jobs[identifier]
        return {key: item for key, item in value.items() if not key.startswith("_")}

    async def submit(self, payload):
        if self._closed:
            raise LanguageError("语言服务已关闭。", "closed", 503)
        settings = normalize(payload)
        identifier = settings["request_id"]
        if identifier in self.jobs:
            return self._public(identifier)
        while len(self.jobs) >= 8:
            completed = next((key for key, value in self.jobs.items() if value["status"] not in ("queued", "running")), None)
            if completed is None:
                raise LanguageError("已有8个优化请求，请等待完成或取消。", "busy", 429)
            self.jobs.pop(completed)
            self.tasks.pop(completed, None)
        self.jobs[identifier] = {"id": identifier, "request_id": identifier, "status": "queued", "model": settings["model"], "elapsed_seconds": 0}
        async def run():
            job = self.jobs[identifier]
            job["status"] = "running"
            started = time.monotonic()
            try:
                result = await self.optimize(payload)
                job.update(result, status="completed")
            except asyncio.CancelledError:
                job.update(status="cancelled", elapsed_seconds=round(time.monotonic() - started, 2))
                raise
            except LanguageError as error:
                job.update(status="failed", error=str(error), error_code=error.code, elapsed_seconds=round(time.monotonic() - started, 2))
            except Exception:
                job.update(status="failed", error="提示词优化失败，原始输入已保留。", error_code="internal", elapsed_seconds=round(time.monotonic() - started, 2))
        self.tasks[identifier] = asyncio.create_task(run())
        return self._public(identifier)

    async def result(self, identifier):
        if identifier not in self.jobs:
            raise LanguageError("优化任务记录已失效，请重新提交。", "not_found", 404)
        return self._public(identifier)

    async def cancel(self, identifier):
        if identifier not in self.jobs:
            raise LanguageError("优化任务记录已失效。", "not_found", 404)
        task = self.tasks.get(identifier)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.jobs[identifier]["status"] = "cancelled"
        return self._public(identifier)

    async def close(self):
        self._closed = True
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

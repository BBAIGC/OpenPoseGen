"""Safe BBAPI profile/model access; this module never submits paid generation."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

import aiohttp

def discover_paths(plugin_dir=None, environment=None):
    plugin_dir, environment = Path(plugin_dir or Path(__file__).resolve().parent), os.environ if environment is None else environment
    sibling = plugin_dir.parent / "ComfyUI-BBAPI-Private"
    runtime = Path(environment["OPENPOSEGEN_BBAPI_ROOT"]) if environment.get("OPENPOSEGEN_BBAPI_ROOT") else sibling if sibling.is_dir() else Path(r"F:\AIGC\ComfyUI_NEW\custom_nodes\ComfyUI-BBAPI-Private")
    profiles = Path(environment["COMFYUI_BBAPI_PRIVATE_CONFIG"]) if environment.get("COMFYUI_BBAPI_PRIVATE_CONFIG") else runtime.parent.parent / "user" / "bbapi_private_profiles.json"
    return runtime, profiles


BBAPI_ROOT, PROFILE_PATH = discover_paths()
CLOUD_PREFIX = "bbapi:"


def load_module(filename):
    name = "_asset_bbapi_" + Path(filename).stem
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, BBAPI_ROOT / filename)
        if spec is None or spec.loader is None:
            raise RuntimeError("未找到本机BBAPI连接组件。")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def model_capabilities(identifier):
    """Limits enforced by the already installed transport, not invented API support."""
    lower = identifier.lower()
    if "gemini-2.5" in lower and "image" in lower:
        maximum = 3
    elif "gemini-3" in lower and "image" in lower:
        maximum = 14
    else:
        maximum = 16
    return {"max_references": maximum, "resolutions": ["1K", "2K"] if "grok" in lower else ["1K", "2K", "4K"],
            "ratios": ["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"],
            "capability_source": "installed_bbapi_transport", "availability_source": "models_api"}


class ProviderService:
    def __init__(self, data_dir, *, profile_store=None, client_module=None):
        self.data_dir, self.path = Path(data_dir), Path(data_dir) / "provider.json"
        self.secret_module = load_module("secrets.py") if profile_store is None else None
        self.store = profile_store or self.secret_module.ProfileStore(PROFILE_PATH)
        self.client = client_module or load_module("client.py")
        self._lock = asyncio.Lock()
        self._language_cache, self._language_stamp, self._language_profile = [], 0, None
        self.config = {"version": 1, "models": [], "default_model": "", "connection_status": "untested"}
        if self.path.is_file():
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError("资产库云端配置格式无效，已保留原文件。")
            self.config.update({key: value[key] for key in ("models", "default_model", "profile", "base_url", "connection_status") if key in value})
        profiles = self.store.public_profiles()
        selected = next((item for item in profiles if item["name"] == self.config.get("profile")), None)
        selected = selected or next((item for item in profiles if item["name"] == "asset-library"), None)
        selected = selected or next((item for item in profiles if item["name"] == "bbapi-main"), None)
        selected = selected or (profiles[0] if len(profiles) == 1 else None)
        if selected:
            if self.config.get("profile") and (self.config["profile"] != selected["name"] or self.config.get("base_url") != selected["base_url"]):
                self.config.update(models=[], connection_status="untested", default_model=selected.get("default_model", ""))
            self.config.update(profile=selected["name"], base_url=selected["base_url"])
            if not self.config["default_model"]:
                self.config["default_model"] = selected.get("default_model", "")
        else:
            self.config.setdefault("profile", "asset-library")
            self.config.setdefault("base_url", "https://bbapi.ainivox.cn")

    def public_config(self):
        profiles = self.store.public_profiles()
        configured = any(item["name"] == self.config["profile"] for item in profiles)
        return {"base_url": self.config["base_url"], "configured": configured,
                "api_key_masked": "已配置" if configured else "", "models": self.config["models"],
                "default_model": self.config["default_model"], "connection_status": self.config["connection_status"],
                "profile": self.config["profile"], "profiles": profiles}

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    async def save(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("云端配置须为对象。")
        async with self._lock:
            profile = payload.get("profile") or self.config["profile"]
            profiles = self.store.public_profiles()
            by_name = {item["name"]: item for item in profiles}
            if not isinstance(profile, str) or profile not in by_name and profile != "asset-library":
                raise ValueError("所选本机配置不存在。")
            key = payload.get("api_key", "")
            if not isinstance(key, str):
                raise ValueError("API Key须为文本。")
            new_name = payload.get("new_profile_name", "")
            if not isinstance(new_name, str):
                raise ValueError("新服务名称须为文本。")
            new_name = new_name.strip()
            target = "asset-library-" + new_name if new_name and not new_name.startswith("asset-library-") else new_name
            if target and (len(target) > 60 or re.search(r"[\x00-\x1f/\\]", target)):
                raise ValueError("新服务名称过长或含无效字符。")
            if target and target in by_name:
                raise ValueError("此服务名称已存在，请选择已有服务或使用其他名称。")
            if target and not key.strip():
                raise ValueError("添加新服务时请填写API Key。")
            switching = profile != self.config["profile"]
            selected = by_name.get(profile, {})
            base = payload.get("base_url") or selected.get("base_url") or self.config["base_url"]
            default = payload.get("default_model", "" if target else selected.get("default_model", "") if switching else self.config["default_model"])
            if not isinstance(base, str):
                raise ValueError("接口地址须为文本。")
            if not isinstance(default, str) or len(default) > 200:
                raise ValueError("默认模型编号无效。")
            if key.strip():
                # New keys belong only to this library alias; other profiles stay intact.
                profile = target or "asset-library"
                await asyncio.to_thread(self.store.save, profile, base, key, default)
            else:
                value = await asyncio.to_thread(self.store.load, profile)
                if base.rstrip("/").removesuffix("/v1") != value["base_url"]:
                    await asyncio.to_thread(self.store.save, "asset-library", base, value["api_key"], default)
                    profile = "asset-library"
            base = next((item["base_url"] for item in self.store.public_profiles() if item["name"] == profile), base)
            changed = base != self.config["base_url"] or profile != self.config["profile"] or bool(key.strip())
            self.config.update(base_url=base, profile=profile, default_model=default)
            if changed:
                self.config.update(models=[], connection_status="untested")
            await asyncio.to_thread(self._write)
            return self.public_config()

    async def test(self):
        """Only GET /v1/models via the mature client; never an image POST."""
        async with self._lock:
            value = await asyncio.to_thread(self.store.load, self.config["profile"], self.config["base_url"])
            connection = self.client.Connection(base_url=value["base_url"], api_key=value["api_key"],
                                                profile=self.config["profile"], timeout=30, default_model=self.config["default_model"])
            try:
                models = await asyncio.to_thread(self.client.ImageClient(connection).models)
                safe = []
                for item in models:
                    identifier = item.get("id")
                    if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
                        continue
                    if not re.search(r"image|imagen|flux|dall.?e|banana|grok-imagine", identifier, re.I):
                        continue
                    safe.append({"id": identifier, "name": str(item.get("display_name") or item.get("name") or identifier)[:200], **model_capabilities(identifier)})
                self.config.update(models=safe, connection_status="connected")
                available = {item["id"] for item in safe}
                if self.config["default_model"] not in available:
                    self.config["default_model"] = next((name for name in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2") if name in available), safe[0]["id"] if safe else "")
                await asyncio.to_thread(self._write)
                return self.public_config()
            except Exception as error:
                self.config["connection_status"] = "failed"
                await asyncio.to_thread(self._write)
                raise ValueError(self.client.safe_message(str(error), value["api_key"])) from None

    def model(self, engine):
        if not isinstance(engine, str) or not engine.startswith(CLOUD_PREFIX):
            raise ValueError("云端模型编号无效。")
        identifier = engine[len(CLOUD_PREFIX):]
        item = next((item for item in self.config["models"] if item["id"] == identifier), None)
        if item is None:
            raise ValueError("此云端模型尚未通过连接测试，请先读取模型列表。")
        return item

    async def language_models(self, force=False):
        """Read the actual relay language list without invoking paid chat."""
        identity = (self.config["profile"], self.config["base_url"])
        if not force and identity == self._language_profile and time.monotonic() - self._language_stamp < 60:
            return list(self._language_cache)
        value = await asyncio.to_thread(self.store.load, *identity)
        try:
            from urllib.parse import urlsplit
            remote = urlsplit(value['base_url']).hostname not in ('127.0.0.1', 'localhost', '::1')
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=remote) as session:
                async with session.get(value["base_url"] + "/v1/models", headers={"Authorization": "Bearer " + value["api_key"]}, allow_redirects=False) as response:
                    raw = bytearray()
                    while chunk := await response.content.read(65536):
                        raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ValueError("模型列表过大。")
                    if response.status != 200:
                        raise ValueError("云端语言模型列表读取失败（HTTP " + str(response.status) + "）。")
                    payload = json.loads(raw)
            models = []
            for item in payload.get("data", []):
                identifier = item.get("id") if isinstance(item, dict) else None
                if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
                    continue
                if re.search(r"image|imagen|dall.?e|banana|grok-imagine|flux|embedding|whisper|tts|audio|video|sora|moderation", identifier, re.I):
                    continue
                models.append({"id": identifier, "name": str(item.get("display_name") or item.get("name") or identifier)[:200], "available": True})
            self._language_cache, self._language_stamp, self._language_profile = models[:300], time.monotonic(), identity
            return list(self._language_cache)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise ValueError(self.client.safe_message(str(error), value["api_key"]) or "云端语言模型列表读取失败。") from None

    def engines(self):
        configured = self.public_config()["configured"]
        return [{"id": CLOUD_PREFIX + item["id"], "name": "BBAPI · " + item["name"], "provider": "bbapi",
                 "ready": configured and self.config["connection_status"] == "connected",
                 "missing": [] if configured else ["BBAPI密钥尚未配置"], "reference_mode": "individual",
                 "reference_note": "参考图片按当前顺序提交到云端；模型和配额以当前密钥开放列表为准。",
                 "presets": [{"id": "default", "name": "云端默认"}], **{key: item[key] for key in ("max_references", "resolutions", "ratios")}}
                for item in self.config["models"]]

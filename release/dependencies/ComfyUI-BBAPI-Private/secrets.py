"""Local-only credential profiles. Keys never form part of a Comfy workflow."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit, urlunsplit

_LOCK = threading.RLock()


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("接口地址需要完整的 HTTP 或 HTTPS 地址。")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("接口地址不能包含登录信息、查询参数或片段。")
    if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("远程接口必须使用 HTTPS。")
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


def normalize_profile(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 60 or re.search(r"[\x00-\x1f/\\]", value):
        raise ValueError("配置名称需要 1–60 个字符，不能包含路径符号。")
    return value


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("本机加密配置仅支持 Windows；其他系统可使用 BBAPI_API_KEY 环境变量。")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        )
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "ComfyUI BBAPI Private", None, None, None, 1, ctypes.byref(target)
        )
    if not ok:
        raise RuntimeError("Windows 本机密钥加密失败，请检查当前登录用户。")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


class ProfileStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "profiles": {}}
        try:
            value = json.loads(self.path.read_text("utf-8"))
            if not isinstance(value.get("profiles"), dict):
                raise ValueError()
            return value
        except (ValueError, OSError) as error:
            raise RuntimeError("本机 BBAPI 配置无法读取，请检查配置文件。") from error

    def save(self, alias: str, base_url: str, api_key: str, default_model: str = "") -> None:
        alias = normalize_profile(alias)
        base_url = normalize_base_url(base_url)
        api_key = api_key.strip()
        if not 8 <= len(api_key) <= 4096 or any(c.isspace() for c in api_key):
            raise ValueError("API Key 格式不正确。")
        sealed = base64.b64encode(_dpapi(api_key.encode("utf-8"))).decode("ascii")
        with _LOCK:
            data = self._read()
            data["profiles"][alias] = {
                "base_url": base_url, "key_dpapi": sealed,
                "default_model": default_model.strip()[:200],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            os.replace(temporary, self.path)

    def load(self, alias: str, base_url: str = "") -> dict:
        alias = normalize_profile(alias)
        with _LOCK:
            item = self._read()["profiles"].get(alias)
        if item:
            item = dict(item)
            if base_url and normalize_base_url(base_url) != item["base_url"]:
                raise ValueError("接口地址与已保存的配置不一致，请另存一份配置。")
            try:
                item["api_key"] = _dpapi(base64.b64decode(item.pop("key_dpapi")), True).decode("utf-8")
            except Exception as error:
                raise RuntimeError("密钥只能由保存它的 Windows 用户解密，请重新保存密钥。") from error
            return item
        key = os.environ.get("BBAPI_API_KEY", "").strip()
        if key:
            return {"api_key": key, "base_url": normalize_base_url(base_url or "https://bbapi.ainivox.cn"),
                    "default_model": ""}
        raise ValueError("尚未保存此配置的密钥，请在连接节点输入并保存 API Key。")

    def public_profiles(self) -> list[dict]:
        with _LOCK:
            data = self._read()
        return [{"name": name, "base_url": item["base_url"],
                 "default_model": item.get("default_model", ""), "configured": True}
                for name, item in data["profiles"].items()]

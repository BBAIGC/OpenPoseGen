"""Optional single-image continuity copies using the installed DLSS5/VOSR2 APIs.

No weights are downloaded here. Source images are immutable; completed stages
are content-addressed so an interrupted VOSR pass can reuse its DLSS input.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import inspect
import json
from pathlib import Path
import sys
import uuid

from .resource_control import release_resources

ALGORITHMS = ("VOSR2", "DLSS5→VOSR2")
DLSS_MODES = {"1": "1x (DLAA / native)", "1.5": "1.5x (Quality)",
              "1.724": "1.724x (Balanced)", "2": "2x (Performance)", "3": "3x (Ultra Performance)"}
DEFAULT_SETTINGS = {
    "enabled": False, "algorithm": "VOSR2", "total_scale": 2, "vosr_scale": 2, "dlss_scale": 1,
    "vosr_model": "VOSR2", "vosr_dtype": "bf16", "seed": 42, "vosr_color_alignment": "wavelet",
    "vosr_tile_size": 512, "vosr_tile_overlap": 64, "vosr_vae_tile_size": 1024, "vosr_vae_tile_overlap": 128,
    "dlss_style": "natural", "dlss_preset": 0, "dlss_intensity": 1.0, "dlss_tone": 1.0,
    "dlss_structure": 1.5, "dlss_skin": -1.0, "dlss_auto_mask": "on", "dlss_global_tone": -1.0,
    "dlss_detail": 1.0, "dlss_color": 1.0,
}
SCHEMA = "h3-reference-upscale/1"


def _decimal(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}应为有效数字。")
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}应为有限数字。") from exc


def normalize_settings(settings=None):
    if isinstance(settings, str):
        try:
            if len(settings) > 20000:
                raise ValueError
            settings = json.loads(settings)
        except (TypeError, ValueError) as exc:
            raise ValueError("衔接参考超分设置不是有效JSON。") from exc
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("衔接参考超分设置应为对象。")
    values = dict(DEFAULT_SETTINGS)
    if settings:
        if set(settings) - set(values):
            raise ValueError("衔接参考超分包含未知设置：" + "、".join(sorted(map(str, set(settings)-set(values)))))
        values.update(settings)
    if type(values["enabled"]) is not bool:
        raise ValueError("衔接参考超分开关应为布尔值。")
    for key, options in (("algorithm", ALGORITHMS), ("vosr_dtype", ("default", "fp16", "bf16")),
                         ("vosr_color_alignment", ("wavelet", "adain", "none")),
                         ("dlss_style", ("default", "natural", "cinematic")), ("dlss_auto_mask", ("on", "off"))):
        if not isinstance(values[key], str) or values[key] not in options:
            raise ValueError(f"衔接参考超分的{key}选项无效。")
    model = values["vosr_model"]
    if (not isinstance(model, str) or not model.strip() or len(model) > 128 or model in (".", "..") or
            any(char in model for char in ("/", "\\", ":", "\0"))):
        raise ValueError("VOSR2模型应为本机模型库中的文件夹名称。")
    for key, low, high, step in (("vosr_scale", 1, 4, 1), ("seed", 0, 2**63-1, 1),
                                 ("vosr_tile_size", 0, 4096, 64), ("vosr_tile_overlap", 0, 512, 8),
                                 ("vosr_vae_tile_size", 0, 8192, 64), ("vosr_vae_tile_overlap", 0, 512, 8),
                                 ("dlss_preset", 0, 9, 1)):
        value = values[key]
        if type(value) is not int or not low <= value <= high or value % step:
            raise ValueError(f"衔接参考超分的{key}应为{low}～{high}、步长{step}的整数。")
    for tile, overlap in (("vosr_tile_size", "vosr_tile_overlap"), ("vosr_vae_tile_size", "vosr_vae_tile_overlap")):
        if values[tile] and values[overlap] >= values[tile]:
            raise ValueError("VOSR2分块重叠必须小于对应的分块尺寸。")
    for key, low, high in (("dlss_intensity", 0, 2), ("dlss_tone", 0, 2), ("dlss_structure", 0, 2),
                           ("dlss_skin", -1, 2), ("dlss_global_tone", -1, 2), ("dlss_detail", 0, 2), ("dlss_color", 0, 1)):
        number = _decimal(values[key], key)
        if not Decimal(low) <= number <= Decimal(high):
            raise ValueError(f"{key}应在{low}～{high}之间。")
        values[key] = float(number)
    dlss = _decimal(values["dlss_scale"], "DLSS倍率")
    if dlss not in map(Decimal, DLSS_MODES):
        raise ValueError("DLSS倍率只能选择1、1.5、1.724、2或3。")
    total = _decimal(values["total_scale"], "总倍率")
    if values["enabled"] and values["algorithm"] == "VOSR2" and dlss != 1:
        raise ValueError("单独VOSR2模式的DLSS倍率应为1。")
    if values["enabled"] and total != dlss * values["vosr_scale"]:
        raise ValueError("总倍率必须准确等于DLSS倍率与VOSR2倍率的乘积。")
    values["dlss_scale"] = int(dlss) if dlss == dlss.to_integral_value() else float(dlss)
    values["total_scale"] = int(total) if total == total.to_integral_value() else float(total)
    return values


def validation_bounds():
    """Application guardrails, distinct from the nodes' wider native ranges."""
    return {"max_long_edge": 8192, "max_pixels": 64*1024*1024,
            "dlss_max_long_edge": 7680, "dlss_max_short_edge": 4320}


def _bound_size(width, height, label):
    bounds = validation_bounds()
    if type(width) is not int or type(height) is not int or min(width, height) < 1:
        raise ValueError(f"{label}尺寸无效。")
    if max(width, height) > bounds["max_long_edge"] or width*height > bounds["max_pixels"]:
        raise ValueError(f"{label}超过衔接超分的8192长边或64MP限制，请降低倍率。")


def expected_dimensions(width, height, settings=None):
    values = normalize_settings(settings)
    _bound_size(width, height, "参考图")
    stage = None
    output_width, output_height = width, height
    if values["enabled"]:
        if values["algorithm"] == ALGORITHMS[1]:
            factor = values["dlss_scale"]
            if factor != 1:
                def even(value):
                    rounded = int(round(value))
                    return rounded-rounded % 2
                output_width, output_height = even(width*factor), even(height*factor)
                if min(output_width, output_height) < 1 or abs(output_width/width-output_height/height) > 0.02:
                    raise ValueError("DLSS偶数取整后的宽高倍率差异过大，请使用较大的参考图。")
            bounds = validation_bounds()
            if max(output_width, output_height) > bounds["dlss_max_long_edge"] or min(output_width, output_height) > bounds["dlss_max_short_edge"]:
                raise ValueError("DLSS中间图超过插件7680长边或4320短边范围，请降低DLSS倍率。")
            stage = [output_width, output_height]
        output_width *= values["vosr_scale"]
        output_height *= values["vosr_scale"]
    _bound_size(output_width, output_height, "超分结果")
    return {"input": [width, height], "dlss": stage, "output": [output_width, output_height],
            "nominal_scale": values["total_scale"] if values["enabled"] else 1,
            "actual_scale_x": output_width/width, "actual_scale_y": output_height/height}


def _outputs(value):
    if hasattr(value, "result"):
        return value.result
    return value["result"] if isinstance(value, dict) and "result" in value else value


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _identity(path, full_hash=False):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"衔接超分缺少本机文件：{path.name}；此功能不会下载模型。")
    stat = path.stat()
    result = {"file": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if full_hash:
        result["sha256"] = _hash_file(path)
    return result


def _node(name):
    import nodes
    cls = nodes.NODE_CLASS_MAPPINGS.get(name)
    if cls is None:
        raise RuntimeError(f"缺少已安装的{name}节点，请重启ComfyUI检查插件。")
    return cls


def _backend_identity(settings):
    loader_cls, upscale_cls = _node("VOSR2ModelLoader"), _node("VOSR2Upscale")
    module = sys.modules.get(loader_cls.__module__)
    loader = getattr(module, "loader", None)
    if loader is None or not hasattr(loader, "_VOSR2_ROOT"):
        raise RuntimeError("本机VOSR2加载接口不兼容，尚未加载模型。")
    bundle = (Path(loader._VOSR2_ROOT) / settings["vosr_model"]).resolve()
    if not bundle.is_relative_to(Path(loader._VOSR2_ROOT).resolve()):
        raise ValueError("VOSR2模型目录无效。")
    dit = next((bundle/path for path in ("clean_weights/ema_model.safetensors", "checkpoints/ema_model.safetensors", "ema_model.safetensors")
                if (bundle/path).is_file()), bundle/"checkpoints/ema_model.safetensors")
    # Check every required component BEFORE calling the installed loader,
    # whose native missing-file behavior would otherwise download weights.
    files = [bundle/"args.json", dit, bundle/"dinov2_vitl14.safetensors",
             bundle/"Qwen-Image-vae-2d/config.json", bundle/"Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors"]
    identity = {"adapter": _identity(__file__, True), "vosr": {"model": settings["vosr_model"], "files": [_identity(path, path.suffix == ".json") for path in files],
                         "node": _identity(inspect.getfile(upscale_cls), True), "loader": _identity(inspect.getfile(loader_cls), True)}}
    vosr_root = Path(inspect.getfile(loader_cls)).parent
    vosr_sources = [vosr_root / name for name in ("loader.py", "inference.py", "color.py", "tiled_vae.py")]
    vosr_sources += sorted((vosr_root / "models").glob("*.py"))
    identity["vosr"]["implementation"] = [_identity(path, True) for path in vosr_sources if path.is_file()]
    inspect.signature(loader_cls.execute).bind(model=settings["vosr_model"], dtype=settings["vosr_dtype"])
    inspect.signature(upscale_cls.execute).bind(model=None, image=None, upscale=settings["vosr_scale"], seed=settings["seed"],
        color_alignment=settings["vosr_color_alignment"], tile_size=settings["vosr_tile_size"], tile_overlap=settings["vosr_tile_overlap"],
        vae_tile_size=settings["vosr_vae_tile_size"], vae_tile_overlap=settings["vosr_vae_tile_overlap"])
    if settings["algorithm"] == ALGORITHMS[1]:
        cls = _node("RH_DLSS5Enhance")
        dlss_module = sys.modules.get(cls.__module__)
        common = getattr(dlss_module, "common", None)
        if common is None:
            raise RuntimeError("本机DLSS5接口不兼容，尚未加载模型。")
        runtime = Path(common.resolve_runtime_dir(""))
        common.check_runtime_files(runtime, settings["dlss_scale"])
        assets = sorted(runtime.glob("*.dll")) + sorted((runtime/"caller").glob("*.dll"))
        assets += [Path(inspect.getfile(cls)).parent/"native/bin/dlss5nr_bridge.dll"]
        identity["dlss"] = {"files": [_identity(path) for path in assets], "node": _identity(inspect.getfile(cls), True),
                            "adapter": identity["adapter"]}
        dlss_root = Path(inspect.getfile(cls)).parent / "dlss5nr"
        identity["dlss"]["implementation"] = [_identity(path, True) for path in sorted(dlss_root.glob("*.py"))]
    return identity


def _shutdown_dlss():
    """DLSS holds D3D12 resources outside Comfy's PyTorch model manager."""
    import nodes
    cls = nodes.NODE_CLASS_MAPPINGS.get("RH_DLSS5Enhance")
    if cls is None:
        return
    package = cls.__module__.rsplit(".", 1)[0]
    backend = sys.modules.get(package + ".dlss5nr.windows_backend")
    if backend is not None and callable(getattr(backend, "shutdown", None)):
        backend.shutdown()


def _notify(callback, phase, **data):
    if callback:
        try:
            callback({"phase": phase, **data})
        except Exception:
            pass


def _release_stage(shutdown_dlss=False):
    try:
        if shutdown_dlss:
            _shutdown_dlss()
    finally:
        release_resources(full=True)


def _size(path):
    from PIL import Image
    with Image.open(path) as image:
        # Read the header before decoding pixels so oversized inputs fail the
        # application bounds without first allocating the full RGB surface.
        width, height = image.size
        return (height, width) if image.getexif().get(274) in (5, 6, 7, 8) else (width, height)


def _read_image(path):
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        pixels = np.asarray(ImageOps.exif_transpose(source).convert("RGB"), dtype=np.float32)/255
    return torch.from_numpy(pixels).unsqueeze(0)


def _save_image(tensor, destination, expected):
    import torch
    from PIL import Image
    if not isinstance(tensor, torch.Tensor) or tensor.ndim != 4 or tensor.shape[0] != 1 or tensor.shape[-1] < 3:
        raise RuntimeError("衔接超分没有返回单张RGB图片。")
    actual = [int(tensor.shape[2]), int(tensor.shape[1])]
    if actual != list(expected):
        raise RuntimeError(f"衔接超分实际输出{actual[0]}×{actual[1]}与预期{expected[0]}×{expected[1]}不同，未缓存异常结果。")
    pixels = tensor[0, ..., :3].detach().to(device="cpu", dtype=torch.float32)
    if not bool(torch.isfinite(pixels).all()):
        raise RuntimeError("衔接超分结果含无效像素，未缓存异常结果。")
    pixels = pixels.clamp(0, 1).mul(255).round().to(torch.uint8).numpy()
    temporary = destination.with_name(destination.stem+"-"+uuid.uuid4().hex+".tmp.png")
    Image.fromarray(pixels, "RGB").save(temporary)
    temporary.replace(destination)


def _write_receipt(path, receipt):
    temporary = path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _cached(directory, key, expected):
    image, record = directory/(key+".png"), directory/(key+".json")
    try:
        receipt = json.loads(record.read_text(encoding="utf-8"))
        if (receipt.get("schema") == SCHEMA and receipt.get("key") == key and image.is_file() and
                list(_size(image)) == list(expected) and receipt.get("output_sha256") == _hash_file(image)):
            return {"path": str(image), "receipt": {**receipt, "cache_hit": True, "status": "cached"}}
    except (OSError, ValueError, TypeError):
        pass
    return None


def upscale_reference(path, settings, cache_directory, progress_callback=None):
    values = normalize_settings(settings)
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError("待超分的衔接参考图不存在。")
    if not values["enabled"]:
        return {"path": str(source), "receipt": {"schema": SCHEMA, "status": "disabled", "enabled": False, "cache_hit": False}}
    dimensions = expected_dimensions(*_size(source), values)
    identity = _backend_identity(values)
    input_hash = _hash_file(source)
    key = _digest({"schema": SCHEMA, "input_sha256": input_hash, "settings": values, "identity": identity})
    directory = Path(cache_directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    hit = _cached(directory, key, dimensions["output"])
    if hit:
        _notify(progress_callback, "upscale_cached", receipt=hit["receipt"])
        return hit
    import comfy.model_management as mm
    mm.throw_exception_if_processing_interrupted()
    stages = []
    current = source
    if values["algorithm"] == ALGORITHMS[1]:
        dlss_values = {key: value for key, value in values.items() if key.startswith("dlss_")}
        dlss_key = _digest({"schema": SCHEMA, "stage": "dlss", "input_sha256": input_hash,
                            "settings": dlss_values, "identity": identity["dlss"]})
        stage_dir = directory/"dlss"
        stage_dir.mkdir(exist_ok=True)
        partial = _cached(stage_dir, dlss_key, dimensions["dlss"])
        if not partial:
            _notify(progress_callback, "upscale_dlss", status="running", dimensions=dimensions["dlss"])
            result = image = None
            release_resources(full=True)
            try:
                mm.throw_exception_if_processing_interrupted()
                kwargs = {name.removeprefix("dlss_"): values[name] for name in dlss_values if name != "dlss_scale"}
                kwargs.update(upscaling_mode=DLSS_MODES[str(Decimal(str(values["dlss_scale"])).normalize())],
                              motion="none", batch_mode="still images", warmup_frames=0, keep_audio="off",
                              backend="windows-bridge", channel_order="auto", hdr="off", runtime_dir="", wine_prefix="")
                destination = stage_dir/(dlss_key+".png")
                from .isolated_dlss import run_dlss
                run_dlss(source,destination,kwargs,Path(inspect.getfile(_node('RH_DLSS5Enhance'))).parent,mm.throw_exception_if_processing_interrupted)
                if list(_size(destination))!=dimensions['dlss']:raise RuntimeError('DLSS5实际输出尺寸与倍率不符。')
                receipt = {"schema": SCHEMA, "key": dlss_key, "stage": "DLSS5", "status": "completed", "cache_hit": False,
                           "input_sha256": input_hash, "output_sha256": _hash_file(destination), "dimensions": dimensions["dlss"]}
                _write_receipt(stage_dir/(dlss_key+".json"), receipt)
                partial = {"path": str(destination), "receipt": receipt}
            finally:
                result = image = None
                # The disposable child owns NGX. Calling shutdown in Comfy
                # can block inside the DLL and ignore the interrupt flag.
                _release_stage(shutdown_dlss=False)
        current = Path(partial["path"])
        stages.append(partial["receipt"])
        _notify(progress_callback, "upscale_dlss", status="completed", receipt=partial["receipt"])
    mm.throw_exception_if_processing_interrupted()
    _notify(progress_callback, "upscale_vosr", status="running", dimensions=dimensions["output"])
    bundle = image = result = None
    _release_stage(shutdown_dlss=False)
    try:
        mm.throw_exception_if_processing_interrupted()
        image = _read_image(current)
        bundle = _outputs(_node("VOSR2ModelLoader").execute(model=values["vosr_model"], dtype=values["vosr_dtype"]))[0]
        result = _outputs(_node("VOSR2Upscale").execute(
            model=bundle, image=image, upscale=values["vosr_scale"], seed=values["seed"],
            color_alignment=values["vosr_color_alignment"], tile_size=values["vosr_tile_size"],
            tile_overlap=values["vosr_tile_overlap"], vae_tile_size=values["vosr_vae_tile_size"],
            vae_tile_overlap=values["vosr_vae_tile_overlap"]))[0]
        destination = directory/(key+".png")
        _save_image(result, destination, dimensions["output"])
        receipt = {"schema": SCHEMA, "key": key, "status": "completed", "cache_hit": False, "enabled": True,
                   "input_sha256": input_hash, "output_sha256": _hash_file(destination), "settings": values,
                   "model_identity": identity, "dimensions": dimensions, "stages": stages}
        _write_receipt(directory/(key+".json"), receipt)
    finally:
        bundle = image = result = None
        release_resources(full=True)
    _notify(progress_callback, "upscale_completed", receipt=receipt)
    return {"path": str(destination), "receipt": receipt}

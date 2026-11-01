"""Compact, sequential MiniMax H3 shot nodes using the installed Comfy core.

The video bundle is deliberately gated by the completed prompt plan. Shots
return an encoded temporary video and one CPU frame, never their sampling
latents or decoded frame batches. All generation primitives remain ComfyUI's
own nodes; this module does not replace the sampler or model implementations.
"""

from __future__ import annotations

import gc
import json
import logging
import math
import os
from pathlib import Path
import time
import uuid

import folder_paths
import nodes
import torch


log = logging.getLogger(__name__)
FPS = 24
BASE_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
TURBO_LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
TURBO_LORA_4STEP = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
TEXT_ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_int8_convrot.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"


def _outputs(value):
    """Accept both the core's V3 NodeOutput and conventional V1 tuples."""
    return value.result if hasattr(value, "result") else value


def _combo(folder, default):
    return (folder_paths.get_filename_list(folder), {"default": default})


def _registered_node(node_id):
    cls = nodes.NODE_CLASS_MAPPINGS.get(node_id)
    if cls is None:
        raise RuntimeError(f"缺少节点 {node_id}，请启用对应的已安装节点并重启 ComfyUI。")
    return cls


def _execute_v3(node_id, **kwargs):
    cls = _registered_node(node_id)
    return _outputs(cls.execute(**kwargs))


def _is_plan_silent(plan):
    try:
        data = json.loads(plan)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}
    values = [data.get("speech_mode"), data.get("audio_mode"),
              settings.get("speech_mode"), settings.get("audio_mode")]
    return any(isinstance(value, str) and value.strip().lower() in
               {"静音", "无音轨", "silent", "mute", "none"}
               for value in values)


def _frame_counts(seconds):
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds < 0.2 or seconds > 15.0:
        raise ValueError("单镜时长应为 0.2～15 秒。")
    output_frames = max(1, round(seconds * FPS))
    generated_frames = max(5, output_frames)
    generated_frames += (5 - generated_frames % 17) % 17
    return output_frames, generated_frames


def _end_keyframe_at_export(positive, target_frames, generated_frames):
    """Keep the FL2V endpoint inside the exported, trimmed frame clock."""
    import node_helpers
    keyframes = positive[0][1].get("minimax_keyframes", [])
    relocated = [{**item, "resolved_frame_index": target_frames - 1}
                 if item.get("resolved_frame_index") == generated_frames - 1
                 else dict(item) for item in keyframes]
    return node_helpers.conditioning_set_values(positive, {"minimax_keyframes": relocated})


def _audio_for_export(raw_audio, frame_count, silent):
    sample_rate = int(raw_audio["sample_rate"]) if raw_audio is not None else 32000
    sample_count = round(frame_count / FPS * sample_rate)
    if silent:
        waveform = torch.zeros((1, 2, sample_count), dtype=torch.float32, device="cpu")
    else:
        if raw_audio is None:
            raise RuntimeError("视频生成未返回音轨，无法按“模型音频”导出。")
        waveform = raw_audio["waveform"].detach().to(device="cpu", dtype=torch.float32)
        if waveform.shape[1] == 1:
            waveform = waveform.repeat(1, 2, 1)
        waveform = waveform[..., :sample_count].contiguous()
        if waveform.shape[-1] < sample_count:
            waveform = torch.nn.functional.pad(waveform, (0, sample_count - waveform.shape[-1]))
    return {"waveform": waveform, "sample_rate": sample_rate}


class H3VideoModelBundle:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "ready_plan": ("STRING", {"forceInput": True}),
            "unet_name": _combo("diffusion_models", BASE_MODEL),
            "clip_name": _combo("text_encoders", TEXT_ENCODER),
            "video_vae_name": _combo("vae", VIDEO_VAE),
            "audio_vae_name": _combo("vae", AUDIO_VAE),
            "turbo_enabled": ("BOOLEAN", {"default": True}),
            "accelerated_steps": ("INT", {"default": 8, "forceInput": True, "min": 4, "max": 8, "step": 4}),
            "turbo_lora": _combo("loras", TURBO_LORA),
            "turbo_lora_4step": _combo("loras", TURBO_LORA_4STEP),
            "low_vram": ("BOOLEAN", {"default": True}),
            "sparse_attention": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("H3_VIDEO_BUNDLE",)
    RETURN_NAMES = ("视频模型",)
    FUNCTION = "load"
    CATEGORY = "MiniMax H3/提示词与分镜"
    DESCRIPTION = "提示词优化和语言模型卸载完成后，才加载视频模型。关闭加速 LoRA 使用基础模型。"

    def load(self, ready_plan, unet_name=BASE_MODEL, clip_name=TEXT_ENCODER,
             video_vae_name=VIDEO_VAE, audio_vae_name=AUDIO_VAE,
             turbo_enabled=True, turbo_lora=TURBO_LORA, low_vram=True,
             sparse_attention=False, accelerated_steps=8,
             turbo_lora_4step=TURBO_LORA_4STEP, input_mode="I2V"):
        from comfy_extras.nodes_minimax_h3 import MiniMaxH3SigmaShift
        if input_mode not in ("I2V", "T2VA"):
            raise ValueError("视频输入模式应为图生视频或文生视频。")
        if not isinstance(ready_plan, str) or not ready_plan.strip():
            raise ValueError("请先连接提示词优化器输出的完整计划。")
        try:
            plan = json.loads(ready_plan)
        except (ValueError, TypeError) as exc:
            raise ValueError("提示词计划格式无效，请重新运行优化器。") from exc
        if not isinstance(plan, dict) or plan.get("status") != "ready" or plan.get("llm_released") is not True:
            raise ValueError("提示词优化与语言模型卸载尚未完成，视频模型不会加载。")
        base_filename = Path(str(unet_name).replace("\\", "/")).name.lower()
        if (not base_filename.startswith("minimax_h3_fl2va") or
                any(marker in base_filename for marker in ("fused", "turbo", "refdelta"))):
            raise ValueError("此流程只接受未融合的 MiniMax H3 FL2VA 基础模型，避免重复叠加加速权重。")
        actual_lora = None
        turbo_steps = None
        if turbo_enabled:
            if accelerated_steps not in (4, 8):
                raise ValueError("加速采样仅支持配套的 4 步或 8 步。")
            turbo_steps = int(accelerated_steps)
            actual_lora = turbo_lora_4step if turbo_steps == 4 else turbo_lora
            lora_filename = Path(str(actual_lora).replace("\\", "/")).name.lower()
            if "fl2v_turbo" not in lora_filename or f"_{turbo_steps}step" not in lora_filename:
                raise ValueError(f"{turbo_steps} 步采样必须使用对应的 FL2V {turbo_steps} 步加速 LoRA。")

        model = nodes.UNETLoader().load_unet(unet_name, "default")[0]
        if turbo_enabled:
            model = nodes.LoraLoaderModelOnly().load_lora_model_only(model, actual_lora, 1.0)[0]
        if low_vram:
            model = _execute_v3("MiniMaxChunkFeedForward", model=model, chunks=4, seq_threshold=4096)[0]
        # The sparse kernel is an acceleration for the distilled Turbo path.
        # A plain base run uses the core's dense attention instead.
        use_sparse = bool(sparse_attention and turbo_enabled)
        if use_sparse:
            model = _execute_v3("H3SLAAttention", model=model, sparsity_ratio=0.90,
                                block_size="64", min_seq_len=8192,
                                dense_last_steps=0, protect_audio=True, enabled=True)[0]
        # The 768p four-step LoRA was distilled with 6/3 shifts. Apply that
        # documented schedule to the new text-only route; existing I2V keeps
        # its saved 12/3 behavior.
        video_shift = 6.0 if (input_mode == "T2VA" and turbo_enabled and turbo_steps == 4 and
                              "768p" in str(actual_lora).lower()) else 12.0
        model = _outputs(MiniMaxH3SigmaShift.execute(model, video_shift, 3.0))[0]
        clip = nodes.CLIPLoader().load_clip(clip_name, "minimax", "default")[0]
        vae = nodes.VAELoader().load_vae(video_vae_name)[0]
        audio_vae = nodes.VAELoader().load_vae(audio_vae_name)[0]
        return ({"model": model, "clip": clip, "vae": vae, "audio_vae": audio_vae,
                 "ready_plan": ready_plan, "turbo_enabled": bool(turbo_enabled),
                 "turbo_steps": turbo_steps, "turbo_lora": actual_lora,
                 "cfg": 1.0,
                 "sparse_attention": use_sparse, "model_name": unet_name,
                 "input_mode": input_mode, "sigma_shift_video": video_shift, "sigma_shift_audio": 3.0},)


class H3SamplingSteps:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "turbo_enabled": ("BOOLEAN", {"default": True}),
            "accelerated_steps": ("INT", {"default": 4, "forceInput": True, "min": 1, "max": 100}),
            "standard_steps": ("INT", {"default": 20, "min": 20, "max": 100, "step": 1}),
        }}

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("采样步数",)
    FUNCTION = "select"
    CATEGORY = "MiniMax H3/提示词与分镜"

    def select(self, turbo_enabled, accelerated_steps=4, standard_steps=20):
        return (int(accelerated_steps) if turbo_enabled else max(20, int(standard_steps)),)


class H3EightStepSwitch:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"base_steps": ("INT", {"default": 4, "forceInput": True, "min": 1, "max": 100})}}

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("采样步数",)
    FUNCTION = "eight_steps"
    CATEGORY = "MiniMax H3/提示词与分镜"
    DESCRIPTION = "开启时为 8 步；旁路此节点后使用接入的基础步数。"

    def eight_steps(self, base_steps=4):
        return (8,)


class H3GenerateShot:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "bundle": ("H3_VIDEO_BUNDLE",),
            "prompt": ("STRING", {"forceInput": True}),
            "width": ("INT", {"default": 640, "min": 64, "max": 2048, "step": 32}),
            "height": ("INT", {"default": 384, "min": 64, "max": 2048, "step": 32}),
            "seconds": ("FLOAT", {"default": 10.0, "min": 0.2, "max": 15.0, "step": 0.1}),
            "steps": ("INT", {"default": 4, "min": 1, "max": 100, "forceInput": True}),
            "seed": ("INT", {"default": 42, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
            "audio_mode": ("STRING", {"default": "模型音频", "forceInput": True}),
            "continue_previous": ("BOOLEAN", {"default": True}),
        }, "optional": {
            "first_frame": ("IMAGE",),
            "last_frame": ("IMAGE",),
            "previous_video": ("VIDEO",),
            "previous_last_frame": ("IMAGE",),
        }, "hidden": {"execution_prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}}

    RETURN_TYPES = ("VIDEO", "IMAGE", "STRING")
    RETURN_NAMES = ("镜头视频", "末帧", "生成记录")
    FUNCTION = "generate"
    CATEGORY = "MiniMax H3/提示词与分镜"
    DESCRIPTION = "顺序生成一个镜头并立即编码。接入上一镜头视频可确保上一镜头全部完成后才开始。"

    def generate(self, bundle, prompt, width=640, height=384, seconds=10.0,
                 steps=4, seed=42, audio_mode="模型音频", continue_previous=True,
                 first_frame=None, previous_video=None, previous_last_frame=None, last_frame=None,
                 sampler_name="res_multistep", scheduler="simple", execution_prompt=None, extra_pnginfo=None):
        from .output_metadata import execution_metadata
        # Internal studio calls inherit their execution's snapshot. A standalone
        # shot receives its own hidden metadata without changing the text prompt.
        with execution_metadata(prompt=execution_prompt, extra_pnginfo=extra_pnginfo):
            return self._generate(bundle, prompt, width, height, seconds, steps, seed, audio_mode,
                                  continue_previous, first_frame, previous_video, previous_last_frame,
                                  last_frame, sampler_name, scheduler)

    def _generate(self, bundle, prompt, width=640, height=384, seconds=10.0,
                  steps=4, seed=42, audio_mode="模型音频", continue_previous=True,
                  first_frame=None, previous_video=None, previous_last_frame=None, last_frame=None,
                  sampler_name="res_multistep", scheduler="simple"):
        input_mode = bundle.get("input_mode", "I2V")
        if input_mode not in ("I2V", "T2VA"):
            raise ValueError("视频输入模式无效，请重新选择图生视频或文生视频。")
        if input_mode == "T2VA" and any(value is not None for value in
                                        (first_frame, last_frame, previous_video, previous_last_frame)):
            raise ValueError("文生视频不能接入首帧、尾帧或上一镜视频，请关闭图像约束后重试。")
        import comfy.model_management as mm
        from comfy_api.latest import InputImpl, Types
        from comfy_extras.nodes_audio import VAEDecodeAudio
        from comfy_extras.nodes_custom_sampler import (
            BasicGuider, BasicScheduler, KSamplerSelect,
            RandomNoise, SamplerCustomAdvanced,
        )
        from comfy_extras.nodes_minimax_h3 import MiniMaxH3ImageToVideo
        from comfy_extras.nodes_video import CreateVideo
        import comfy.samplers

        if sampler_name not in comfy.samplers.KSampler.SAMPLERS or scheduler not in comfy.samplers.KSampler.SCHEDULERS:
            raise ValueError("视频采样器或调度器无效，请检查视频模型配置节点。")

        if not prompt or not str(prompt).strip():
            raise ValueError("镜头提示词不能为空。")
        if audio_mode not in ("模型音频", "静音"):
            raise ValueError("音频模式应为“模型音频”或“静音”。")
        if width % 32 or height % 32 or width < 64 or height < 64:
            raise ValueError("宽高必须是至少 64 的 32 倍数。")
        target_frames, generated_frames = _frame_counts(seconds)
        try:
            plan = json.loads(bundle["ready_plan"])
            planned_seconds = float(plan["seconds_per_scene"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("提示词计划缺少有效镜头时长，请重新运行提示词优化器。") from exc
        if (not math.isfinite(planned_seconds) or
                not math.isclose(float(seconds), planned_seconds, rel_tol=0.0, abs_tol=1e-6)):
            raise ValueError("生成时长与提示词计划不一致；请使用计划中的镜头时长后再运行。")
        actual_seconds = target_frames / FPS
        if bundle["turbo_enabled"] and int(steps) != bundle.get("turbo_steps"):
            raise ValueError("采样步数与当前加速 LoRA 不匹配；请将同一个 4/8 步开关连接到模型和采样步数。")
        effective_steps = int(steps) if bundle["turbo_enabled"] else max(20, int(steps))
        dialogue_track = bundle.get("_dialogue_track")
        explicit_silent = bool(bundle.get("_user_silent", audio_mode == "静音"))
        using_dialogue = not explicit_silent and bool(dialogue_track and dialogue_track.get("enabled"))
        # An explicitly connected recording is authoritative; model audio and
        # automatic silence inference cannot replace its words or timing.
        silent = explicit_silent or not using_dialogue and (audio_mode == "静音" or _is_plan_silent(bundle.get("ready_plan", "")))
        reference = first_frame
        if previous_video is not None and continue_previous:
            reference = previous_last_frame
            if reference is None:
                tail = previous_video.as_trimmed(-1 / FPS, 1 / FPS, strict_duration=True)
                if tail is None:
                    raise ValueError("无法读取上一镜头的末帧。")
                reference = tail.get_components().images[-1:].detach().cpu().contiguous()
        if reference is not None:
            reference = reference[:1].detach().to("cpu").contiguous()
        target_last_frame = last_frame
        if target_last_frame is not None:
            target_last_frame = target_last_frame[:1].detach().to("cpu").contiguous()

        start = time.perf_counter()
        temporary_path = None
        guided_audio = None
        local = {}
        try:
            mm.throw_exception_if_processing_interrupted()
            with torch.inference_mode():
                from .voice_nodes import select_scene_voices, voice_conditioning
                voices = [] if silent or using_dialogue else select_scene_voices(
                    bundle.get("_voice_settings"), plan, bundle.get("_voice_scene") or {})
                if voices:
                    if "refdelta" not in str(bundle.get("model_name", "")).lower():
                        raise ValueError("参考音色需要支持音频参考的RefDelta模型。")
                    positive, latent = voice_conditioning(
                        bundle["clip"], bundle["vae"], bundle["audio_vae"], str(prompt),
                        int(width), int(height), generated_frames, reference, target_last_frame, voices)
                else:
                    positive, latent = _outputs(MiniMaxH3ImageToVideo.execute(
                        clip=bundle["clip"], vae=bundle["vae"], prompt=str(prompt),
                        width=int(width), height=int(height), length=generated_frames,
                        first_frame=reference, last_frame=target_last_frame))
                if target_last_frame is not None:
                    # Native H3 rounds up to its 17k+5 grid. Anchoring only at
                    # that padded end would trim the desired endpoint away.
                    positive = _end_keyframe_at_export(positive, target_frames, generated_frames)
                if using_dialogue:
                    from .dialogue_audio import dialogue_segment
                    from comfy_extras.nodes_minimax_h3 import MiniMaxH3AddGuide
                    guided_audio = dialogue_segment(dialogue_track, bundle.get("_dialogue_offset_seconds", 0.0), target_frames, FPS)
                    positive = _outputs(MiniMaxH3AddGuide.execute(
                        positive=positive, latent=latent, frame_idx=0,
                        audio_vae=bundle["audio_vae"], audio=guided_audio))[0]
                local.update(positive=positive, latent=latent)
                # H3 is already CFG-distilled. The official native workflow
                # uses BasicGuider (CFG 1), including the 20-step base path.
                guider = _outputs(BasicGuider.execute(bundle["model"], positive))[0]
                sigmas = _outputs(BasicScheduler.execute(bundle["model"], scheduler, effective_steps, 1.0))[0]
                noise = _outputs(RandomNoise.execute(int(seed)))[0]
                sampler = _outputs(KSamplerSelect.execute(sampler_name))[0]
                sampler_result = SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent)
                samples = _outputs(sampler_result)[0]
                local.update(guider=guider, sigmas=sigmas, noise=noise,
                             sampler=sampler, sampler_result=sampler_result, samples=samples)
                mm.throw_exception_if_processing_interrupted()
                frames = nodes.VAEDecode().decode(bundle["vae"], samples)[0]
                frames = frames.detach().to(device="cpu", dtype=torch.float32)
                if len(frames) < target_frames:
                    raise RuntimeError(f"生成帧数不足：需要 {target_frames} 帧，实际 {len(frames)} 帧。")
                # Trim on the pixel clock before encoding, including the audio.
                frames = frames[:target_frames].contiguous()
                last_frame = frames[-1:].clone()
                raw_audio = None if silent or using_dialogue else _outputs(VAEDecodeAudio.execute(bundle["audio_vae"], samples))[0]
                audio = _audio_for_export(guided_audio if using_dialogue else raw_audio, target_frames, silent)
                local.update(frames=frames, raw_audio=raw_audio, audio=audio)
                unencoded = _outputs(CreateVideo.execute(
                    images=frames, fps=float(FPS), audio=audio, bit_depth=8,
                    color_space="sRGB", codec="none"))[0]
                local["unencoded"] = unencoded
                destination_dir = Path(folder_paths.get_temp_directory()) / "h3_prompt_director"
                destination_dir.mkdir(parents=True, exist_ok=True)
                destination = destination_dir / f"shot_{uuid.uuid4().hex}.mp4"
                temporary_path = destination.with_suffix(".partial.mp4")
                from .output_metadata import current_metadata
                unencoded.save_to(str(temporary_path), format=Types.VideoContainer.MP4,
                                  codec=Types.VideoCodec.H264, metadata=current_metadata())
                mm.throw_exception_if_processing_interrupted()
                os.replace(temporary_path, destination)
                temporary_path = None
                video = InputImpl.VideoFromFile(str(destination))
            report = json.dumps({
                "seed": int(seed),
                "seconds": actual_seconds, "output_frames": target_frames,
                "generated_frames": generated_frames, "fps": FPS,
                "steps": effective_steps, "cfg": bundle["cfg"],
                "sampler": sampler_name, "scheduler": scheduler,
                "turbo_enabled": bundle["turbo_enabled"],
                "turbo_steps": bundle.get("turbo_steps"),
                "turbo_lora": bundle.get("turbo_lora"),
                "input_mode": input_mode,
                "sigma_shift_video": bundle.get("sigma_shift_video", 12.0),
                "sigma_shift_audio": bundle.get("sigma_shift_audio", 3.0),
                "audio_mode": "对白音轨" if using_dialogue else "静音" if silent else "模型音频",
                "dialogue_offset_seconds": bundle.get("_dialogue_offset_seconds", 0.0) if using_dialogue else None,
                "dialogue_source_seconds": dialogue_track.get("seconds") if using_dialogue else None,
                "dialogue_padded_seconds": round(max(0.0, actual_seconds - max(0.0, dialogue_track["seconds"] - bundle.get("_dialogue_offset_seconds", 0.0))), 6) if using_dialogue else None,
                "continued_from_previous": bool(previous_video is not None and continue_previous),
                "end_frame_constrained": target_last_frame is not None,
                "end_frame_index": target_frames - 1 if target_last_frame is not None else None,
                "elapsed_seconds": round(time.perf_counter() - start, 3),
                "temporary_video": str(destination),
            }, ensure_ascii=False)
            log.info("H3 shot complete: %s", report)
            return video, last_frame, report
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    log.warning("Could not remove interrupted shot encoding %s", temporary_path)
            # Locals, not graph outputs, own all large frame/latent batches.
            local.clear()
            positive = latent = guider = sigmas = noise = sampler = None
            sampler_result = samples = frames = raw_audio = audio = unencoded = None
            target_last_frame = None
            guided_audio = None
            gc.collect()
            mm.soft_empty_cache()


SHOT_NODE_CLASS_MAPPINGS = {
    "H3VideoModelBundle": H3VideoModelBundle,
    "H3GenerateShot": H3GenerateShot,
    "H3EightStepSwitch": H3EightStepSwitch,
    "H3SamplingSteps": H3SamplingSteps,
}

SHOT_NODE_DISPLAY_NAME_MAPPINGS = {
    "H3VideoModelBundle": "H3 视频模型",
    "H3GenerateShot": "H3 单镜头生成",
    "H3EightStepSwitch": "8 步采样",
    "H3SamplingSteps": "H3 采样步数",
}

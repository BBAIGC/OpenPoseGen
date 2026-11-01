"""Image2.1 sampling with the official Turbo noise schedule when selected."""

import math


TURBO_SIGMAS = (
    1.0, 0.978453, 0.95418, 0.926626, 0.89508,
    0.845148, 0.704534, 0.414568, 0.0,
)


def build_turbo_sigmas(steps, denoise=1.0):
    """Resample the official curve, then retain its img2img tail.

    Only the requested tail is materialized, so low strengths do not allocate
    an unnecessarily large intermediate schedule. Zero strength has no steps.
    """
    if isinstance(steps, bool) or int(steps) != steps or steps < 1:
        raise ValueError("采样步数应为正整数。")
    steps = int(steps)
    strength = float(denoise)
    if not math.isfinite(strength) or not 0.0 <= strength <= 1.0:
        raise ValueError("降噪应为0～1之间的有限数值。")
    if strength == 0.0:
        return []

    full_steps = int(steps / strength)
    curve_steps = len(TURBO_SIGMAS) - 1
    sigmas = []
    for index in range(full_steps - steps, full_steps):
        position = index * curve_steps / full_steps
        left = min(int(position), curve_steps - 1)
        fraction = position - left
        sigmas.append(TURBO_SIGMAS[left] +
                      (TURBO_SIGMAS[left + 1] - TURBO_SIGMAS[left]) * fraction)
    sigmas.append(0.0)
    return sigmas


def sample_image2(model, seed, steps, cfg, sampler, scheduler, positive,
                  negative, latent, denoise=1.0, model_name=""):
    """Return a one-latent tuple, matching ``nodes.KSampler.sample``.

    Ordinary Image2.1 and explicit non-simple schedulers retain the existing
    KSampler entry point and its installed hooks. Imports stay lazy so loading
    this helper or bypassing zero-strength sampling never initializes Torch.
    """
    if denoise == 0.0:
        return (latent,)

    filename = str(model_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "turbo" not in filename.casefold() or scheduler != "simple":
        import nodes

        return nodes.KSampler().sample(
            model, seed, steps, cfg, sampler, scheduler, positive, negative,
            latent, denoise=denoise,
        )

    sigma_values = build_turbo_sigmas(steps, denoise)
    import torch
    import comfy.samplers
    from comfy_extras.nodes_custom_sampler import SamplerCustom

    result = SamplerCustom.execute(
        model, True, seed, cfg, positive, negative,
        comfy.samplers.sampler_object(sampler),
        torch.tensor(sigma_values, dtype=torch.float32), latent,
    )
    return (result[0],)

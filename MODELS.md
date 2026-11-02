# 模型与运行节点

模型权重不随OpenPoseGen发布。以下来源、精确字节数与SHA-256核对日期为2026-11-01；机器可读清单为[install/models.json](install/models.json)。下载体积不等于显存占用。

## 本地生成

| 路线 | 文件 | 字节数 | 来源与安装目录 |
| --- | --- | ---: | --- |
| 千问2.1普通25步 | `qwen-image-2.1-UC-Q6_K.gguf` | 5,876,556,576 | [KasugaiSakura UC GGUF](https://huggingface.co/KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF/blob/main/qwen-image-2.1-UC-Q6_K.gguf)，`models/diffusion_models/` |
| 千问2.1 Turbo8步 | `qwen_image_2.1_turbo_int8_convrot.safetensors` | 7,256,787,829 | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors)，`models/diffusion_models/` |
| 两条千问路线共用编码器 | `qwen3vl_8b_int8_convrot.safetensors` | 9,350,798,360 | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors)，`models/text_encoders/` |
| 两条千问路线共用VAE | `qwen_image_2.1_vae_bf16.safetensors` | 675,509,688 | [同仓库VAE](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/vae/qwen_image_2.1_vae_bf16.safetensors)，`models/vae/` |
| MOSS默认Jib V2写实 | `jibMixMossRealistic_v2RealityRenew.safetensors` | 5,414,035,750 | [Jib Mix MOSS Realistic V2](https://civitai.com/models/3016339/jib-mix-moss-realistic?modelVersionId=3435334)，`models/diffusion_models/` |
| MOSS可选官方SFT | `moss_image_sft_bf16.safetensors` | 10,164,655,808 | [Comfy-Org/MOSS-Image](https://huggingface.co/Comfy-Org/MOSS-Image/blob/main/diffusion_models/moss_image_sft_bf16.safetensors)，`models/diffusion_models/` |
| MOSS编码器 | `qwen3.5_9b_bf16.safetensors` | 19,306,312,328 | [Comfy-Org/MOSS-Image](https://huggingface.co/Comfy-Org/MOSS-Image/blob/main/text_encoders/qwen3.5_9b_bf16.safetensors)，`models/text_encoders/` |
| MOSS VAE | `flux2-vae_small.safetensors` | 249,521,340 | [同仓库VAE](https://huggingface.co/Comfy-Org/MOSS-Image/blob/main/vae/flux2-vae_small.safetensors)，`models/vae/` |

默认三条路线去重合计48,129,521,871字节，约48.13GB；语音模型另约0.94GB。不要下载H3视频模型、Krea或其他不在姿绘库入口中的路线。GGUF是不同格式，不能把其他权重改扩展名或改文件名冒充同款。

普通千问与Turbo使用不同主模型；Turbo采用已验证的固定8步日程，不是普通权重直接减为8步。普通多图参考按成熟工作流保留原参考几何，生成宽高由用户画幅决定。

MOSS原生文生图；这里多参考经单张有序拼图重绘，可能保留分区布局。它不能与千问共用编码器或VAE。服装LoRA等必须匹配模型架构，预留槽默认关闭，可按需选择兼容权重。

## 语音

[SenseVoiceSmall-onnx](https://huggingface.co/kautism/SenseVoiceSmall-onnx)的`model.onnx`、`config.yaml`、`tokens.json`、`am.mvn`放在`models/sensevoice-small/`。CPU推理依赖`onnxruntime`、`kaldi-native-fbank`、`PyYAML`、`numpy`；`model.onnx`为940,009,685字节。四文件字节与SHA见机器清单。

## 节点

| 依赖 | 来源 | 用途 |
| --- | --- | --- |
| BBAIGC共享核心 | [ComfyUI-BBAIGC-MiniMax-H3](https://github.com/BBAIGC/ComfyUI-BBAIGC-MiniMax-H3) | 多图引用、千问编码、画幅、语音与结果操作；发布包固定源码快照，只注册一份 |
| BBAIGC BBAPI | [ComfyUI-BBAIGC-BBAPI](https://github.com/BBAIGC/ComfyUI-BBAIGC-BBAPI) | 本机加密密钥、真实云端图像接口、API多图参考 |
| GGUF加载器 | [city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) | 普通千问UC GGUF主模型 |
| 当前ComfyUI核心 | [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI) | 原生Qwen2.1、MOSS、缓存及采样节点 |

要求实际`object_info`具有`TextEncodeQwenImage21`、`QwenImage21Cache`、相关模型加载与采样节点。作者环境ComfyUI0.43.0／前端1.57.4、RTX5060Ti16GB、64GB内存仅为验证基准，不是所有分辨率与设备的最低配置保证。已有环境能工作时不要盲目重装PyTorch。

## 可选文字优化

可复用已配置的API文字服务，或使用[Ollama](https://ollama.com/download)与公开的[qwen3.5:9b](https://ollama.com/library/qwen3.5)。安装器参数`-TextModel 'qwen3.5:9b'`在已安装Ollama后拉取这一明确选择的模型；默认`none`不下载文字权重。作者自建`uncensored-*`别名不作为公开安装依赖。

## API路线

设置页默认提供[BBAPI](https://bbapi.ainivox.cn)入口，使用用户自己申请的密钥；也可配置符合现有传输协议的服务。模型列表按密钥实际返回内容显示，参考图、比例和分辨率支持以接口服务实际能力为准。连接检查仅查询模型列表，不自动提交收费生成。

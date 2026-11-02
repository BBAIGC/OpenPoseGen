# 安装姿绘库

支持Windows上的ComfyUI Desktop、便携版与手动安装。先确定当前运行实例，勿把另一个Python环境与模型目录混用。基准环境为ComfyUI0.43.0／前端1.57.4；实际兼容性以所需节点枚举为准。

## 完整发布包

从 [GitHub Releases](https://github.com/BBAIGC/OpenPoseGen/releases) 获取完整ZIP。包内包含姿绘库源码及版本固定的BBAIGC共享节点。模型权重与参考图库需单独准备。

```powershell
.\install\install.ps1 -ComfyBase '实例目录' -ComfyCore 'ComfyUI核心目录' -ComfyPython '当前实例python.exe' -Plan
```

核心目录应存在`folder_paths.py`，实例目录应含`custom_nodes`和`user`。安装在`custom_nodes`下时可自动识别实例；单一Desktop安装可读取其安装记录。多个候选实例不会猜测，需明确传入路径。

确认计划后去掉`-Plan`。默认仅安装代码、共享节点和所需Python包，保留现有PyTorch组件。安装器先用`pip --dry-run`核对，若将替换torch／torchvision／torchaudio则停止；不会盲目降级。

## 模型方案

| 参数 | 下载路线 |
| --- | --- |
| `-Models qwen` | 普通千问2.1 UC Q6_K及配套编码器、VAE |
| `-Models qwen-turbo` | Turbo8步独立主模型及相同编码器、VAE |
| `-Models moss` | Jib V2写实MOSS、Qwen3.5编码器及MOSS VAE |
| `-Models moss-official` | 官方SFT版本与MOSS配套文件，供显式选用 |
| `-WithSpeech` | SenseVoice本机CPU语音四文件 |

多项用逗号分隔。完整默认组合`qwen,qwen-turbo,moss`加语音约48GB，不包含素材。下载有断点文件；完成后核对精确字节数及SHA-256。同名已有文件与清单不匹配时不覆盖；可通过`-ModelsRoot`指定已有共享模型目录，避免重复下载。

模型来源、许可与精确文件名见[MODELS.md](MODELS.md)。上游登录或许可要求由用户完成；安装器只读取本机`HF_TOKEN`／`CIVITAI_API_KEY`，不会写进工作流或仓库。

文字优化是可选功能，不包含在默认图像模型下载中。可复用已有API服务或本机Ollama。先从[Ollama官网](https://ollama.com/download)安装并按官方方式启动；希望下载公开文字模型时，在上面命令增加`-TextModel 'qwen3.5:9b'`。该参数执行所选模型的`ollama pull`；请使用Ollama模型库中可拉取的模型名。安装器不会自动安装或启动Ollama。保持默认`none`不下载文字权重。

## 共享节点与首次启动

依赖包含BBAIGC共享核心、BBAPI私有节点和GGUF加载器。已有同来源干净Git安装可备份后更新；已有本地修改或无法识别的非Git版本会保留并停止对应步骤，避免重复注册。不要另装第二份同名共享核心。

安装器写入插件内的本机`runtime.json`：实例、核心、Python、独立素材目录和本机地址。该文件不公开提交。素材默认放在实例旁的`OpenPoseGen-Assets/RuiC-PoseAtlas`；可用`-AssetRoot`指定其他磁盘，不能放进插件源码树。

新增Python节点需要在ComfyUI下次正常启动后加载；安装器不结束正在运行的生成任务。启动后在左栏“姿绘库”进入。独立预览可执行插件内`启动资产库.ps1`，它复用已有本机8191服务。

## 参考素材

从[素材包下载页](https://pan.baidu.com/s/1_Ah1Zu3Cc_VUpPkCRfc1tg?pwd=8wny)获取原图ZIP后，可使用本机导入工具按ID校验并迁入独立素材目录：姿态1,439张、形象1,499张、服装1,407张。

公开源导入：

```powershell
& '当前实例python.exe' -X utf8 .\tools\downloader_import.py all --root '独立素材目录' --workers 8
```

工具按次序准备缩略图与原图，支持断点续传。首次下载完成前可用外接模式导入自己的素材。素材各自遵循来源许可，代码MIT不包含图片许可。

## 手动部署

1. 将完整发布包中的插件源码放到当前实例`custom_nodes/ComfyUI-BBAIGC-AssetLibrary`。
2. 依据`install/dependencies.json`只安装一份共享核心和BBAPI节点；缺少GGUF时安装[city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)。
3. 在当前实例Python环境安装`aiohttp Pillow requests numpy safetensors gguf`。语音另需`onnxruntime kaldi-native-fbank PyYAML`及SenseVoice模型。
4. 写入本机`runtime.json`，字段为`comfy_base`、`comfy_core`、`python`、`asset_root`、`comfy_url`、`port`（默认8191）。不要公开该文件。
5. 将`workflows`内3份模板复制到`user/default/workflows/OpenPoseGen`，已有改动模板另存，不能直接覆盖。
6. 对照模型清单放置真实文件，正常启动ComfyUI检查节点与模型枚举；不要仅修改模型扩展名冒充其他精度版本。

## 更新与恢复

更新前后的拥有文件清单与SHA-256保存在安装记录中，被替换文件备份到实例`.openposegen-install/日期/`。工具不删除目标独有文件，也不覆盖素材、密钥、用户预设、任务记录或输出。发生模型下载失败时保留`.part`重试，不需要重装全部插件。

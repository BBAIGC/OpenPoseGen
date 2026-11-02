# 智能体完整部署

本页面向具有本地终端与网络权限的安装智能体。用户明确要求“安装并开箱使用”即授权准备必要代码、依赖和所选模型；不应停在一份计划或只安装空界面。保留成功阶段，只有登录、验证码、接受许可、目标实例不明确或现有本地修改冲突时请求用户处理。

## 识别当前实例

查看当前运行ComfyUI进程参数、Desktop安装记录及工作目录。分别记录：含`folder_paths.py`的核心、含`custom_nodes/user`的实例、真正运行的Python、模型实际目录（可能是共享链接）、输入与输出、端口。不要全盘扫描模型权重；先按精确文件名检查。

核对NVIDIA显存、系统内存和磁盘空间。默认路线为普通千问2.1、Turbo8步、MOSS、CPU实时语音；按`install/models.json`计算缺失文件去重后的下载体积，已有模型通过大小和SHA-256校验后复用。完整组合约49.1GB权重，另预留8GB以上素材和缓存空间。硬件或空间不足时一次性说明真实缺口与可运行路线，用户已有明确路线则直接执行。

## 安装代码与共享依赖

下载`release.json`指定的完整ZIP并检查其SHA-256，不把HTML错误页当压缩包。解压暂存后运行：

```powershell
.\install\install.ps1 -ComfyBase '实际实例目录' -ComfyCore '实际核心目录' -ComfyPython '实际python.exe' -AssetRoot '独立素材目录' -Models 'qwen,qwen-turbo,moss' -WithSpeech -Plan
.\install\install.ps1 -ComfyBase '实际实例目录' -ComfyCore '实际核心目录' -ComfyPython '实际python.exe' -AssetRoot '独立素材目录' -Models 'qwen,qwen-turbo,moss' -WithSpeech
```

共享节点只装一份；匹配已安装的官方BBAIGC来源，发现本地改动时保留并定位必要合并，不能`reset --hard`或复制出第二份注册相同节点。安装器保留torch栈，依赖解析若会替换其版本则分析具体缺包，不能直接重装GPU环境。

`runtime.json`必须使用当前机器路径，不照抄作者路径。环境变量`OPENPOSEGEN_COMFY_BASE`、`OPENPOSEGEN_COMFY_CORE`、`OPENPOSEGEN_PYTHON`、`OPENPOSEGEN_ASSET_ROOT`、`OPENPOSEGEN_COMFY_URL`、`OPENPOSEGEN_PORT`可覆盖本机配置。

## 准备素材与服务

参考图库及用户数据放独立素材目录，不在插件源码目录。从原作者页面获取素材包后，使用`tools/import_local_archives.py`校验导入；也可运行公开来源导入工具，先完成缩略图，继续后台原图下载。不要把用户下载的ZIP、参考原图、输出、密钥或任务记录上传GitHub。

API路线使用用户自己的服务。可复用已存在的本机BBAPI加密配置，设置页只显示已配置状态；没有配置密钥时，提示用户在设置页填写自己的API Key。读取模型列表仅是连接检查，不能擅自提交收费生图来“测试”。

文字提示词优化为可选步骤。优先复用当前可用的API文字服务或已装Ollama模型；没有可用服务而用户选择本地文字优化时，从Ollama官方安装来源准备运行时，再使用`-TextModel 'qwen3.5:9b'`拉取公开模型。使用Ollama模型库中可拉取的名称；自定义模型需在当前机器另行创建。该可选模型需要另计下载与内存，不将“尚未准备文字服务”误报为图像生成不可用；遇环境策略禁止启动服务时说明实际阻塞，不换路径绕过。

## 聚焦验收

完成以下无推理、无付费检查后再汇报：

- 插件、共享节点和GGUF只有一份，当前ComfyUI`object_info`中所需节点齐全。
- 模型枚举与实际文件名对应；普通25步和独立Turbo8步不能混用权重。
- 姿绘库入口、分类／搭配／外接、参数弹层、预设和结果面板加载正常。
- 缩略图可显示，原图路径真实存在，高清生成参考不会指向缩略图。
- 语音依赖可导入，SenseVoice四文件齐全；无用户讲话时不伪造识别结果。
- 本机配置、任务及输出与安装前保持；记录真实缺失项。

如果新增节点必须重启ComfyUI，应在没有活跃任务且用户已授权重启时再操作；否则告诉用户下次正常启动生效。最终报告版本、安装目录、可用路线、已验证范围与仍需用户完成的登录／许可，不把Schema成功描述成所有模型和高分辨率都已实际生成。

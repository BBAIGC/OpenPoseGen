# 开源、模型与素材来源

OpenPoseGen「姿绘库」由BBAIGC实现。项目自己的软件按[MIT](LICENSE)发布；下列来源仍受其各自许可、使用条款及商标规则约束，不能将代码许可套用到模型或图片。

## 参考图库与中文信息

- 原始项目：[HRuiCcc/RuiC-PoseAtlas](https://github.com/HRuiCcc/RuiC-PoseAtlas)，原站[姿态图鉴](https://hruiccc.github.io/RuiC-PoseAtlas/)。原作者提供姿态与服装整理以及人像索引。本项目保留来源链接，不把整理工具的MIT误称为整套图片的再分发许可。
- 人像原始图站：[AI Portrait Gallery](https://ai-portrait-gallery.pages.dev/)。其图片来源许可及站点对再转载的请求应分别核对。
- 中文名称与补充元数据：[anhao/ai-pose-gallery-zh](https://github.com/anhao/ai-pose-gallery-zh)，站点[AI姿势图库](https://ai-pose-gallery.anhao.app/)。它是中文改版，不是RuiC原作者站点。
- 原图ZIP和本地导入清单不在本代码仓库再分发。用户自行取得素材后使用导入工具；百度云链接由作者之后提供时再补充。下游使用与传播须核对各图片来源许可。

## 运行代码

- [BBAIGC/ComfyUI-BBAIGC-MiniMax-H3](https://github.com/BBAIGC/ComfyUI-BBAIGC-MiniMax-H3)：共享图像工作台与语音节点。发布包的固定快照与本项目一起用于安装，来源、版本和逐文件SHA记录在依赖清单中。
- [BBAIGC/ComfyUI-BBAIGC-BBAPI](https://github.com/BBAIGC/ComfyUI-BBAIGC-BBAPI)：本机API配置与OpenAI兼容图片传输。密钥与用户配置从不随快照发布。
- [city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)：按上游仓库许可单独安装，未复制模型权重。
- [bubkoo/html-to-image](https://github.com/bubkoo/html-to-image)：共享前端中如包含其构建，保留对应MIT许可证文件。
- OpenAI结形图标使用Simple Icons的公开品牌图标数据，保留页面资源旁来源声明；图标数据为CC0，商标权不因此转移。本项目不声称由OpenAI赞助或发布。

## 模型

模型文件、精度变体、体积和精确SHA见[MODELS.md](MODELS.md)与[install/models.json](install/models.json)。Qwen、MOSS、Jib社区变体及SenseVoice各自保留作者署名与许可；本仓库只提供来源和安装清单，不再发布权重，也不修改许可要求。

## 截图与联系入口

文档截图来自本工具真实界面，发布前应去除密钥、个人目录、未授权第三方内容与私人生成历史。联系作者及自愿支持二维码沿用BBAIGC近期已公开仓库，不由插件自动发送任何消息或付款。

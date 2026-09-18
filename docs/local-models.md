# 本地模型接入

画布可以把**你自己机器上的模型**接到节点里，不绑定某一家权重或固定磁盘路径。

两条路可以同时用：

1. **ComfyUI**：启动你的 ComfyUI，在管理后台一键同步它扫到的本机文件；或在个人中心手动填写文件名。
2. **OpenAI 兼容接口**：Ollama / vLLM / SGLang 等，在个人中心填 API Base 和 model id。

## 管理后台：一键同步 ComfyUI

1. 本机启动 ComfyUI（默认 `http://127.0.0.1:8188`）。  
   若画布 API 跑在 Docker 里，地址填 `http://host.docker.internal:8188`。
2. 打开管理后台 → **模型开关** → 「从本机 ComfyUI 同步模型」。
3. 填写你的 ComfyUI 地址 → **探测本机模型** → 按需改「图片 / 视频」分类 → **导入到画布目录**。
4. 导入后的模型会出现在画布图片 / 视频节点的「ComfyUI / 本地模型」列表。

探测只读取该 ComfyUI 实例的 `/models/{目录}` 和 `/object_info`，因此每人电脑上有什么模型就同步什么。

## 个人中心：自己挂本地模型

登录后打开 **个人中心 → 本地模型**：

| 接入方式 | 填写 |
|----------|------|
| ComfyUI | 显示名、图片或视频、权重**文件名**（与 ComfyUI 里看到的一致）、ComfyUI 地址 |
| OpenAI 兼容 | 显示名、图片或视频、上游 model id、API Base（如 `http://127.0.0.1:11434/v1`） |

自建模型只对自己可见，不会写进别人的目录。

### 视频模型（ComfyUI）

各家视频工作流节点不同，画布**不会**用某一个官方模板代替你的图。请在 ComfyUI 里调通后，用「Save (API Format)」导出 JSON，贴到模型配置里。可用占位符：

- `{{PROMPT}}` 提示词
- `{{NEGATIVE}}` 负向
- `{{MODEL}}` 权重文件名
- `{{IMAGE}}` 已上传到该 ComfyUI 的参考图文件名
- `{{WIDTH}}` / `{{HEIGHT}}`

图片类若是普通 Checkpoint（SD/SDXL），不贴工作流也可以走内置文生图图。

## 供应商密钥（实例默认）

管理后台 → 供应商密钥：

- **local**：默认 OpenAI 兼容地址；个人模型上的 API Base 优先。
- **comfyui**：不必填 Key；地址以同步时填写的为准，也可写在模型的 `comfyBaseUrl`。

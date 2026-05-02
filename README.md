# 小主播互动机

一个智能直播互动助手，自动监听主播语音，生成虚拟观众提问弹幕，提升直播间互动氛围。

## 功能特点

- **实时语音识别** - 使用 faster-whisper 本地模型，低延迟高准确率
- **智能问题生成** - 基于主播内容自动生成相关提问，支持多种 LLM API
- **透明弹幕覆盖** - 桌面透明气泡显示，支持 OBS 采集
- **灵活触发机制** - 字数阈值触发 + 关键词截断触发
- **多种显示模式** - 定时消失、保留显示、堆叠显示
- **可视化配置** - 托盘菜单 + 分页设置面板，无需编辑配置文件

## 快速开始

### 1. 环境要求

- Python 3.10+
- Windows 10/11
- 麦克风

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置文件

复制示例配置并填入你的 API Key：

```bash
copy config.yaml.example config.yaml
```

编辑 `config.yaml`，设置你的 LLM API：

```yaml
LLM_API_BASE: https://api.siliconflow.cn/v1
LLM_API_KEY: your-api-key-here
LLM_MODEL: Qwen/Qwen3-8B
```

### 4. 一键启动

双击 `start.bat` 即可启动：

- 自动启动后端服务（语音识别 + LLM）
- 自动启动前端覆盖层（弹幕显示）

## 使用说明

### 托盘菜单

启动后会在系统托盘显示红色圆形图标：

- **左键双击** - 打开设置面板
- **右键点击** - 显示菜单
  - 设置(S) - 打开设置面板
  - 退出(Q) - 退出程序

### 设置面板

设置面板分为 6 个标签页：

#### 显示设置
| 选项 | 说明 |
|------|------|
| 字体 | 弹幕字体选择 |
| 字号 | 弹幕字号 (12-72) |
| 禁用 emoji | 是否隐藏弹幕前的灯泡图标 |
| 消失模式 | 定时消失 / 保留显示 / 堆叠显示 |
| 停留时间 | 气泡停留秒数 |
| 堆叠数量 | 堆叠模式下最大气泡数 |
| 每行字符数 | 气泡宽度（按字符数计算） |

#### 触发条件
| 选项 | 说明 |
|------|------|
| 累积字数阈值 | 累积多少字后触发问题生成 |
| 最大强制触发 | 达到多少字强制触发（防止无限累积） |
| 截断提示词 | 主播说到这些词时立即触发（每行一个） |

#### LLM 配置
| 选项 | 说明 |
|------|------|
| API URL | LLM API 地址 |
| API Key | 你的 API 密钥（密码模式隐藏显示） |
| 模型名称 | 使用的模型名称 |

#### 语音识别
| 选项 | 说明 |
|------|------|
| 模型大小 | tiny/base/small/medium/large |
| 设备 | CPU 或 CUDA (NVIDIA GPU) |
| 计算类型 | int8/float16/float32 |

#### 服务器
| 选项 | 说明 |
|------|------|
| 主机地址 | WebSocket 服务地址 |
| 端口 | WebSocket 服务端口 |

### 气泡操作

- **Ctrl + 左键拖动** - 移动气泡位置，自动保存

### 三种消失模式

| 模式 | 行为 |
|------|------|
| 定时消失 | 气泡停留 N 秒后自动消失 |
| 保留显示 | 气泡一直保留，新问题出现时旧气泡消失 |
| 堆叠显示 | 最多显示 N 个气泡，超过时挤掉最旧的 |

## 推荐配置

### 低配电脑
```yaml
WHISPER_MODEL_SIZE: tiny
WHISPER_DEVICE: cpu
WHISPER_COMPUTE_TYPE: int8
MIN_WORDS_FOR_QUESTION: 100
```

### 标准配置
```yaml
WHISPER_MODEL_SIZE: small
WHISPER_DEVICE: cpu
WHISPER_COMPUTE_TYPE: int8
MIN_WORDS_FOR_QUESTION: 50
```

### 高配电脑 (NVIDIA GPU)
```yaml
WHISPER_MODEL_SIZE: medium
WHISPER_DEVICE: cuda
WHISPER_COMPUTE_TYPE: float16
MIN_WORDS_FOR_QUESTION: 30
```

## 支持的 LLM API

任何兼容 OpenAI 格式的 API 都可使用：

- [硅基流动](https://siliconflow.cn/) - 推荐，国内可用
- [DeepSeek](https://www.deepseek.com/)
- [OpenAI](https://openai.com/)
- 本地部署的 Ollama / vLLM 等

## OBS 设置

1. 添加「窗口采集」源
2. 选择「Python」窗口
3. 或添加「游戏采集」源，模式选择「捕获特定窗口」

## 常见问题

### Q: 启动后没有反应？
A: 检查麦克风是否正常工作，查看后端窗口是否有错误信息。

### Q: 无法生成问题？
A: 检查 API Key 是否正确，网络是否通畅。

### Q: 气泡位置不对？
A: 按住 Ctrl + 左键拖动气泡到合适位置。

### Q: 语音识别效果差？
A: 尝试使用更大的 Whisper 模型（small/medium），或使用 CUDA 加速。

## 项目结构

```
小主播互动机/
├── backend/
│   ├── main.py          # FastAPI 主服务
│   ├── config.py        # 配置加载
│   ├── audio_capture.py # 音频采集
│   ├── stt.py           # 语音识别
│   └── llm.py           # LLM 调用
├── frontend/
│   └── index.html       # OBS 浏览器源页面
├── frontend_overlay.py  # 桌面覆盖层
├── config.yaml          # 配置文件（需自行创建）
├── config.yaml.example  # 配置示例
├── start.bat            # 一键启动
├── start_backend.bat    # 仅启动后端
├── start_overlay.bat    # 仅启动前端
└── requirements.txt     # Python 依赖
```

## 安全提示

- `config.yaml` 包含你的 API Key，**请勿上传到公开仓库**
- 项目已在 `.gitignore` 中排除 `config.yaml`
- 使用 `config.yaml.example` 作为配置模板

## License

MIT License

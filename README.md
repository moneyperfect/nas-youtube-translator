# NAS YouTube Translator

> 本地优先的视频字幕翻译工作台 — 数据不出本机，翻译质量可控。

**作者**: [NasBuild](https://nasbuild.dev) | **技术栈**: Python + FastAPI + 原生 JS

**GitHub**: https://github.com/moneyperfect/nas-youtube-translator

## 功能特性

- 🎬 **视频下载** — 输入 YouTube 链接，自动下载视频和字幕
- 🌐 **智能字幕** — 优先复用 YouTube 人工英文字幕；没有时回退到自动字幕或本地 `faster-whisper`
- 🔄 **多 LLM 翻译** — 支持 DeepSeek、OpenAI、通义千问、Moonshot、Ollama 等多种翻译引擎
- 📝 **字幕编辑** — 单句编辑、重译、锁句、批量术语替换
- 🎯 **质量控制** — 自动生成质量报告，检测残留英文和术语不一致
- 🎬 **视频导出** — 烧录中文字幕或双语字幕到视频
- 📊 **批量处理** — 支持多链接批量入队、后台任务队列
- 💾 **配置持久化** — 翻译配置自动保存，下次打开直接使用

## 快速开始

### 环境要求

- Python 3.11+
- ffmpeg（用于视频处理）
- mpv（用于本地播放，可选）
- NVIDIA GPU（推荐，用于加速转写）

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd YTSubViewer

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# 安装依赖
pip install -r requirements.txt
```

### 启动

```bash
# 方式一：使用启动脚本
.\start.ps1

# 方式二：直接运行
python app.py
```

应用会自动打开浏览器访问 http://127.0.0.1:8000

### 首次配置

1. 打开浏览器访问应用
2. 点击左侧导航栏「模型配置」
3. 选择翻译引擎（如 DeepSeek Test）
4. 填写 API Key
5. 点击「保存设置」

## 使用方法

1. **粘贴链接** — 在「任务工作台」粘贴 YouTube 链接
2. **分析视频** — 点击「分析视频」获取视频信息
3. **生成字幕** — 点击「生成字幕」开始翻译
4. **查看结果** — 翻译完成后可播放、导出或编辑

## 项目结构

```
src/ytsubviewer/
├── config/              # 配置模块
│   ├── settings.py      # Settings 数据类
│   └── crypto.py        # 加密工具
├── routes/              # API 路由
│   ├── serializers.py   # 序列化函数
│   └── helpers.py       # 辅助函数
├── services/            # 服务层
│   ├── base.py          # 服务基类
│   ├── youtube.py       # YouTube 服务
│   ├── translate.py     # 翻译服务
│   ├── transcribe.py    # 转写服务
│   ├── export.py        # 导出服务
│   └── player.py        # 播放器服务
├── web/                 # 前端资源
│   ├── index.html       # 主页面
│   ├── app.js           # 前端逻辑
│   └── styles.css       # 样式表
├── webapp.py            # FastAPI 应用
├── pipeline.py          # 翻译流程编排
├── background_jobs.py   # 后台任务管理
└── models.py            # 数据模型
```

## 开发

### 运行测试

```bash
# 运行全部测试
python -m unittest discover -s tests -v

# 运行单个测试
python -m unittest tests.test_config -v
```

### 构建便携版

```powershell
.\build_portable.ps1
```

构建完成后，可执行文件位于 `dist\YTSubViewer\`

### Docker 部署

```bash
docker compose up -d
```

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | - |
| `WHISPER_MODEL` | 转写模型 | `distil-large-v3` |
| `YTSUBVIEWER_DATA_ROOT` | 数据存储目录 | 项目根目录 |
| `MAX_CONCURRENT_TASKS` | 最大并发任务数 | `3` |

完整配置请参考 `.env.example`

## 技术栈

- **后端**: Python, FastAPI, uvicorn
- **前端**: 原生 HTML/CSS/JavaScript
- **视频处理**: ffmpeg, yt-dlp
- **语音转写**: faster-whisper
- **翻译**: OpenAI 兼容 API

## 许可证

详见 LICENSE 文件

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

## 下载即用（推荐）

**无需安装 Python 或其他依赖，双击即可使用。**

1. 下载 [nas-youtube-translator.zip](https://github.com/moneyperfect/nas-youtube-translator/releases)
2. 解压到任意目录
3. 双击 `YTSubViewer.exe`
4. 浏览器自动打开，开始使用

## 首次使用

### 配置 API Key

1. 打开浏览器访问应用
2. 点击左侧导航栏「设置」
3. 选择翻译引擎（如 DeepSeek）
4. 填写 API Key
5. 点击「保存」

### API Key 获取

| 服务商 | 注册地址 | 价格 |
|--------|----------|------|
| DeepSeek | https://platform.deepseek.com | ¥1/百万 tokens |
| OpenAI | https://platform.openai.com | $2.5/百万 tokens |
| 通义千问 | https://dashscope.aliyun.com | ¥2/百万 tokens |

## 使用方法

1. **粘贴链接** — 在「工作台」粘贴 YouTube 链接
2. **分析视频** — 点击「分析」获取视频信息
3. **生成字幕** — 点击「生成字幕」开始翻译
4. **导出视频** — 翻译完成后点击「中文」或「双语」导出

## 源码运行（开发者）

### 环境要求

- Python 3.11+
- ffmpeg（用于视频处理）
- mpv（用于本地播放，可选）

### 安装

```bash
git clone https://github.com/moneyperfect/nas-youtube-translator.git
cd nas-youtube-translator
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 启动

```bash
python app.py
```

## 项目结构

```
src/ytsubviewer/
├── config/              # 配置模块
├── routes/              # API 路由
├── services/            # 服务层
│   ├── youtube.py       # YouTube 服务
│   ├── translate.py     # 翻译服务
│   ├── transcribe.py    # 转写服务
│   └── export.py        # 导出服务
├── web/                 # 前端资源
├── webapp.py            # FastAPI 应用
├── pipeline.py          # 翻译流程编排
└── background_jobs.py   # 后台任务管理
```

## 开发

```bash
# 运行测试
python -m unittest discover -s tests -v

# 构建便携版
.\build_portable.ps1
```

## 技术栈

- **后端**: Python, FastAPI, uvicorn
- **前端**: 原生 HTML/CSS/JavaScript
- **视频处理**: ffmpeg, yt-dlp
- **语音转写**: faster-whisper
- **翻译**: OpenAI 兼容 API

## 许可证

MIT License - 详见 LICENSE 文件

---

**作者**: [NasBuild](https://nasbuild.dev)

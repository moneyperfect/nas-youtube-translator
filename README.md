# YTSubViewer

本项目是一个 Windows 本地优先的 YouTube 长视频字幕本地化工具，当前支持：

- 输入 YouTube 链接并分析视频信息
- 支持批量链接入队、后台任务队列、刷新后自动恢复
- 新壳任务历史、任务详情、失败重试、取消任务
- 优先复用 YouTube 人工英文字幕；没有时回退到本地 `faster-whisper`
- 生成简体中文字幕 `SRT`
- 生成纯中文 `ASS` 和双语 `ASS`
- 本地 `mpv` 播放器一键挂载字幕播放
- 导出烧录好的 `中文字幕 MP4`、`双语 MP4`，以及快速预览版
- 单句字幕编辑、单句重译、锁句、批量术语替换、质量报告
- 频道/创作者级术语和风格记忆
- 本地授权状态、更新状态与安装器脚手架

## 当前启动方式

开发环境可直接运行：

```powershell
.\start.ps1
```

或：

```powershell
.venv\Scripts\python.exe app.py
```

应用会自动：

- 读取用户配置文件
- 选择可用端口
- 初始化数据目录与缓存目录
- 自动打开浏览器访问本地页面

## 首次使用

首次打开页面后，先在“应用设置与环境”里完成这一步：

1. 填写 `DeepSeek API key`
2. 点击“保存设置”

设置会保存到：

- 配置文件：`%LOCALAPPDATA%\YTSubViewer\settings.json`

数据默认保存到：

- 优先：`D:\YTSubViewerData`
- 回退：`%LOCALAPPDATA%\YTSubViewer`

后续模型缓存、任务输出、日志都会写入应用数据目录，而不是项目目录。

## 运行依赖

当前项目依赖：

- Python 3.11+
- `ffmpeg`
- `mpv`
- NVIDIA GPU（推荐，但不是强制）

如果使用源码模式，请先安装依赖：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 便携版打包

项目已提供 PyInstaller 的 onedir 便携打包配置。

构建命令：

```powershell
.\build_portable.ps1
```

构建完成后，可交付目录为：

```text
dist\YTSubViewer
```

其中可直接双击：

```text
dist\YTSubViewer\YTSubViewer.exe
```

或：

```text
dist\YTSubViewer\启动应用.cmd
```

## 安装器打包

项目已补充 Inno Setup 安装器脚手架。

构建命令：

```powershell
.\build_installer.ps1
```

前提：

- 已安装 `Inno Setup 6`
- 能找到 `ISCC.exe`

安装器脚本位于：

```text
installer\YTSubViewer.iss
```

## 授权与更新

- 本地授权状态文件：`%LOCALAPPDATA%\YTSubViewer\license_state.json`
- 授权 token 生成脚本：`scripts\generate_license.py`
- 更新源示例：`installer\update-feed.sample.json`

如果设置了环境变量 `YTSUBVIEWER_LICENSE_SECRET`，应用会按本地签名规则校验授权码；未设置时，可用 `DEV-LICENSE` 走开发模式激活。

## 测试

```powershell
python -m unittest discover -s tests
python -m compileall app.py src tests
```

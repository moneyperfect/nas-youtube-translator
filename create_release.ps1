# 在 PowerShell 中执行此脚本
# 右键点击 create_release.ps1 -> "Run with PowerShell"

Set-Location "D:\油管翻译"

# 创建 Release
gh release create v1.0.0 `
  nas-youtube-translator.zip `
  --title "NAS YouTube Translator v1.0.0" `
  --notes "## 下载即用

- 解压后双击 YTSubViewer.exe 即可使用
- 无需安装 Python 或其他依赖
- 首次运行自动打开浏览器

## 功能特性

- YouTube 视频字幕自动翻译
- 支持 5 种 LLM 后端（DeepSeek、OpenAI、通义千问、Moonshot、Ollama）
- 字幕编辑器（单句编辑、重译、批量替换）
- 视频导出（中文字幕/双语字幕）
- 配置持久化

## 使用方法

1. 下载 nas-youtube-translator.zip
2. 解压到任意目录
3. 双击 YTSubViewer.exe
4. 配置 API Key 后即可使用

## 作者

[NasBuild](https://nasbuild.dev)"

Write-Host "Release 创建完成！" -ForegroundColor Green
Write-Host "访问: https://github.com/moneyperfect/nas-youtube-translator/releases" -ForegroundColor Cyan

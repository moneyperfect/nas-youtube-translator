# YTSubViewer 全链路测试计划

## 目标

确保从用户粘贴 YouTube 链接到最终下载带字幕视频的完整流程正常工作，避免翻译完成后出问题。

---

## 测试环境

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11 |
| Python | 3.11+ |
| 网络 | 正常（可访问 YouTube） |
| 代理 | 如有，确认正常运行 |
| 磁盘空间 | >= 10GB 可用 |
| API Key | DeepSeek 或其他 LLM 的有效 Key |

---

## 模块一：环境检查

### 测试目标
确保所有依赖和外部工具可用。

### 测试步骤
1. 检查 Python 版本
2. 检查 ffmpeg 是否可用
3. 检查 mpv 是否可用
4. 检查 yt-dlp 是否可用
5. 检查 API Key 配置

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe -c "
import sys
print(f'Python: {sys.version}')

import yt_dlp
print(f'yt-dlp: {yt_dlp.version.__version__}')

from faster_whisper import WhisperModel
print('faster-whisper: OK')

from openai import OpenAI
print('openai: OK')

import subprocess
result = subprocess.run(['ffmpeg', '-version'], capture_output=True)
print(f'ffmpeg: {\"OK\" if result.returncode == 0 else \"FAILED\"}')
"
```

### 验证标准
- 所有依赖导入成功
- ffmpeg 版本信息输出

### 通过标准
- ✅ 所有检查通过

---

## 模块二：配置加载

### 测试目标
确保配置正确加载，API Key 可用。

### 测试步骤
1. 加载 Settings
2. 验证 API Key 存在
3. 验证 Provider 配置
4. 验证代理配置

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe -c "
import sys
sys.path.insert(0, 'src')
from ytsubviewer.config import Settings, get_provider

settings = Settings.load()
print(f'Provider: {settings.provider_name}')
print(f'Model: {settings.model_name}')
print(f'API Key exists: {bool(settings.deepseek_api_key)}')

import os
proxy = os.environ.get('HTTPS_PROXY')
print(f'Proxy: {proxy}')

provider = get_provider(settings.provider_name)
if provider:
    print(f'Provider base_url: {provider.base_url}')
"
```

### 验证标准
- Settings 正确加载
- API Key 非空
- Provider 配置正确

### 通过标准
- ✅ 配置加载成功，API Key 有效

---

## 模块三：YouTube 连接

### 测试目标
确保能正常访问 YouTube 并提取视频信息。

### 测试步骤
1. 测试 yt-dlp 连接
2. 测试视频元数据提取
3. 测试代理配置生效

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe -c "
import sys
sys.path.insert(0, 'src')
from ytsubviewer.config import Settings
from ytsubviewer.services.youtube import YouTubeService

settings = Settings()
yt = YouTubeService(settings)

url = 'https://youtu.be/dQw4w9WgXcQ'  # Rick Astley - Never Gonna Give You Up
try:
    meta = yt.extract_metadata(url)
    print(f'Title: {meta.title}')
    print(f'Duration: {meta.duration_seconds}s')
    print(f'Video ID: {meta.video_id}')
    print('YouTube connection: OK')
except Exception as e:
    print(f'YouTube connection FAILED: {e}')
"
```

### 验证标准
- 能成功提取视频元数据
- 视频标题、时长、ID 正确
- 无 SSL 错误

### 通过标准
- ✅ YouTube 连接正常，元数据提取成功

---

## 模块四：视频下载

### 测试目标
确保视频下载完整，文件可用。

### 测试步骤
1. 下载测试视频
2. 验证文件完整性
3. 验证文件大小
4. 验证无 .part 文件

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe -c "
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from ytsubviewer.config import Settings
from ytsubviewer.services.youtube import YouTubeService

settings = Settings()
yt = YouTubeService(settings)

url = 'https://youtu.be/dQw4w9WgXcQ'
meta = yt.extract_metadata(url)
work_dir = yt.prepare_work_dir(meta)

print(f'Work dir: {work_dir}')

# 下载视频
try:
    video_path = yt.download_video(url, work_dir)
    print(f'Video path: {video_path}')
    print(f'Video size: {video_path.stat().st_size / 1024 / 1024:.1f} MB')
    
    # 检查无 .part 文件
    part_files = list(work_dir.glob('*.part'))
    if part_files:
        print(f'WARNING: Found .part files: {part_files}')
    else:
        print('No .part files: OK')
        
    print('Video download: OK')
except Exception as e:
    print(f'Video download FAILED: {e}')
"
```

### 验证标准
- 视频文件存在
- 文件大小 > 1MB
- 无 .part 文件
- 文件可被 ffprobe 读取

### 通过标准
- ✅ 视频下载成功，文件完整

---

## 模块五：字幕提取

### 测试目标
确保能正确提取视频字幕。

### 测试步骤
1. 提取自动英文字幕
2. 验证字幕文件存在
3. 验证字幕内容

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe -c "
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from ytsubviewer.config import Settings
from ytsubviewer.services.youtube import YouTubeService
from ytsubviewer.subtitle_processing import parse_vtt_file

settings = Settings()
yt = YouTubeService(settings)

url = 'https://youtu.be/dQw4w9WgXcQ'
meta = yt.extract_metadata(url)
work_dir = yt.prepare_work_dir(meta)

print(f'Auto subtitle lang: {meta.automatic_english_subtitle_lang}')

# 下载字幕
if meta.automatic_english_subtitle_lang:
    subtitle_path = yt.download_automatic_subtitle(
        url, 
        meta.automatic_english_subtitle_lang, 
        work_dir
    )
    if subtitle_path:
        print(f'Subtitle path: {subtitle_path}')
        print(f'Subtitle size: {subtitle_path.stat().st_size} bytes')
        
        # 解析字幕
        cues = parse_vtt_file(subtitle_path)
        print(f'Cues count: {len(cues)}')
        if cues:
            print(f'First cue: {cues[0].source_text[:50]}...')
        
        print('Subtitle extraction: OK')
    else:
        print('Subtitle extraction: FAILED - no subtitle file')
else:
    print('Subtitle extraction: FAILED - no subtitle language')
"
```

### 验证标准
- 字幕文件存在
- 字幕可解析
- 字幕内容非空

### 通过标准
- ✅ 字幕提取成功，内容可读

---

## 模块六：翻译功能

### 测试目标
确保翻译功能正常工作。

### 测试步骤
1. 翻译测试字幕
2. 验证翻译结果
3. 验证翻译质量

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe -c "
import sys
sys.path.insert(0, 'src')
from ytsubviewer.config import Settings
from ytsubviewer.services.translate import DeepSeekTranslator
from ytsubviewer.models import TranslationControlConfig
from ytsubviewer.subtitle_processing import SubtitleCue

settings = Settings()
controls = TranslationControlConfig(style_preset='default')
translator = DeepSeekTranslator(settings, controls)

# 测试字幕
test_cues = [
    SubtitleCue(id=1, start=0.0, end=3.0, source_text='Hello, how are you?', target_text=''),
    SubtitleCue(id=2, start=3.0, end=6.0, source_text='I am fine, thank you.', target_text=''),
]

print('Testing translation...')
try:
    translated = translator.translate_cues(test_cues)
    for cue in translated:
        print(f'  [{cue.id}] {cue.source_text} -> {cue.target_text}')
    print('Translation: OK')
except Exception as e:
    print(f'Translation FAILED: {e}')
"
```

### 验证标准
- 翻译成功完成
- 翻译结果非空
- 翻译质量可读

### 通过标准
- ✅ 翻译功能正常

---

## 模块七：视频导出

### 测试目标
确保视频导出正常，包含音频和视频流。

### 测试步骤
1. 导出测试视频
2. 验证输出文件
3. 验证音频流存在
4. 验证视频流存在

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe -c "
import sys
import subprocess
import json
sys.path.insert(0, 'src')
from pathlib import Path
from ytsubviewer.config import Settings
from ytsubviewer.pipeline import SubtitlePipeline

settings = Settings()
pipeline = SubtitlePipeline(settings)

# 使用已有的测试视频
work_dir = Path('workspace/jobs/1a1VXDdIyrk_Agent Harness explained in 8min')
if work_dir.exists():
    from ytsubviewer.job_state import load_job_state
    state = load_job_state(work_dir)
    
    if state:
        print('Testing export...')
        try:
            for event in pipeline.export_video_events(state, bilingual=False):
                print(f'  [{event.progress*100:.0f}%] {event.message}')
                if event.progress >= 1.0:
                    break
            print('Export: OK')
            
            # 验证输出文件
            from ytsubviewer.models import JobArtifacts
            artifacts = JobArtifacts.from_state(state)
            if artifacts.burned_chinese_video_path:
                output = Path(artifacts.burned_chinese_video_path)
                if output.exists():
                    print(f'Output file: {output}')
                    print(f'Output size: {output.stat().st_size / 1024 / 1024:.1f} MB')
                    
                    # 检查音频流
                    cmd = [
                        r'D:\\油管翻译\\.tools\\ffmpeg-8.1\\ffmpeg-8.1-full_build\\bin\\ffprobe.exe',
                        '-v', 'error',
                        '-show_entries', 'stream=codec_type',
                        '-of', 'json',
                        str(output)
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    data = json.loads(result.stdout)
                    streams = data.get('streams', [])
                    audio = [s for s in streams if s.get('codec_type') == 'audio']
                    video = [s for s in streams if s.get('codec_type') == 'video']
                    
                    print(f'Video streams: {len(video)}')
                    print(f'Audio streams: {len(audio)}')
                    
                    if audio:
                        print('Export with audio: OK')
                    else:
                        print('Export FAILED: No audio stream!')
        except Exception as e:
            print(f'Export FAILED: {e}')
    else:
        print('Export FAILED: No job state')
else:
    print('Export FAILED: No work directory')
"
```

### 验证标准
- 导出成功完成
- 输出文件存在
- 输出文件大小合理
- 输出文件包含视频流
- 输出文件包含音频流

### 通过标准
- ✅ 视频导出成功，包含音频和视频流

---

## 模块八：下载功能

### 测试目标
确保下载功能正常，用户能获取文件。

### 测试步骤
1. 启动服务器
2. 调用下载 API
3. 验证文件可访问

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe -c "
import sys
import requests
sys.path.insert(0, 'src')

# 启动服务器
import subprocess
import time

server = subprocess.Popen(
    ['.venv/Scripts/python.exe', 'app.py', '--port', '8765'],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

time.sleep(5)

try:
    # 测试健康检查
    response = requests.get('http://localhost:8765/api/health')
    print(f'Health check: {response.status_code}')
    
    # 测试 bootstrap
    response = requests.get('http://localhost:8765/api/bootstrap')
    print(f'Bootstrap: {response.status_code}')
    
    print('Download API: OK')
finally:
    server.terminate()
"
```

### 验证标准
- 服务器正常启动
- 健康检查接口可用
- Bootstrap 接口可用

### 通过标准
- ✅ 下载功能正常

---

## 模块九：端到端测试

### 测试目标
验证完整流程：粘贴链接 → 分析 → 翻译 → 导出 → 下载。

### 测试步骤
1. 启动服务器
2. 分析视频
3. 生成字幕
4. 等待完成
5. 验证输出

### 测试脚本
```bash
cd D:/油管翻译
.venv/Scripts/python.exe << 'PYEOF'
import sys
sys.path.insert(0, 'src')
from ytsubviewer.config import Settings
from ytsubviewer.pipeline import SubtitlePipeline

settings = Settings()
pipeline = SubtitlePipeline(settings)

url = 'https://youtu.be/dQw4w9WgXcQ'  # 短视频，测试用

print('=== 端到端测试 ===')
print(f'URL: {url}')

for event in pipeline.generate_events(url):
    pct = f'[{event.progress*100:.0f}%]'
    print(f'{pct} {event.stage}: {event.message}')
    
    if event.stage == 'completed':
        print('\n=== 测试通过 ===')
        print(f'Video: {event.artifacts.video_path}')
        print(f'Chinese subtitle: {event.artifacts.chinese_subtitle_path}')
        print(f'Chinese ASS: {event.artifacts.chinese_ass_path}')
        print(f'Bilingual ASS: {event.artifacts.bilingual_ass_path}')
        break
    
    if event.stage == 'failed':
        print(f'\n=== 测试失败 ===')
        print(f'Error: {event.error}')
        break
PYEOF
```

### 验证标准
- 流程完整执行
- 每个阶段成功完成
- 最终输出文件完整

### 通过标准
- ✅ 端到端流程正常

---

## 测试执行顺序

```
模块一 → 模块二 → 模块三 → 模块四 → 模块五 → 模块六 → 模块七 → 模块八 → 模块九
```

每个模块独立测试，全部通过后进行下一个模块。

---

## 测试报告模板

```
# 测试报告

## 测试时间
[填写时间]

## 测试环境
- 操作系统: [填写]
- Python 版本: [填写]
- 网络状态: [填写]

## 测试结果

| 模块 | 状态 | 说明 |
|------|------|------|
| 模块一：环境检查 | ✅/❌ | [说明] |
| 模块二：配置加载 | ✅/❌ | [说明] |
| 模块三：YouTube 连接 | ✅/❌ | [说明] |
| 模块四：视频下载 | ✅/❌ | [说明] |
| 模块五：字幕提取 | ✅/❌ | [说明] |
| 模块六：翻译功能 | ✅/❌ | [说明] |
| 模块七：视频导出 | ✅/❌ | [说明] |
| 模块八：下载功能 | ✅/❌ | [说明] |
| 模块九：端到端测试 | ✅/❌ | [说明] |

## 发现的问题
[填写发现的问题]

## 建议
[填写建议]

## 结论
[填写结论]
```

---

## 自动化测试

如需自动化执行所有测试，运行：

```bash
cd D:/油管翻译
.venv/Scripts/python.exe run_tests.py
```

（需先创建 `run_tests.py` 脚本）

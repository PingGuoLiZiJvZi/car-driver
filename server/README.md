# 车端远程控制服务

本目录提供一个可直接运行的车端 HTTP 服务，默认监听 `0.0.0.0:3725`，实现：

- 浏览器实时查看小车 USB 摄像头画面（MJPEG）
- 浏览器按键控制底盘（W/A/S/D）
- 小车麦克风音频实时下行到浏览器
- 浏览器按住说话，上行音频实时播放到小车扬声器
- 文本转语音（TTS）并在小车端播放

## 1. 依赖

Python 依赖：

```bash
python3 -m pip install -r server/requirements.txt
```

系统依赖（Debian/Ubuntu）：

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg alsa-utils
```

说明：TTS 仅使用豆包语音合成（V3 WebSocket 单向流式）。请提前在环境变量中设置 `TTS_KEY`（Access Key）。

## 2. 启动

先确保底盘库已编译：

```bash
make -C gpio
```

启动服务：

```bash
uvicorn server.app:app --host 0.0.0.0 --port 3725
```

浏览器访问：

```text
http://<小车IP>:3725
```

## 2.1 低负载启动建议（避免 SSH 断联）

在弱性能板卡上，建议先降低视频和音频参数：

```bash
MIC_DEVICE=plughw:CARD=Device_1,DEV=0 \
SPEAKER_DEVICE=plughw:CARD=Device,DEV=0 \
TTS_APP_ID=你的APPID \
TTS_KEY=你的AccessKey \
CAMERA_DEVICE=/dev/video0 \
CAMERA_WIDTH=320 \
CAMERA_HEIGHT=240 \
CAMERA_FPS=12 \
MIC_SAMPLE_RATE=8000 \
CONTROL_HZ=20 \
uvicorn server.app:app --host 0.0.0.0 --port 3725
```

说明：

- 320x240@12fps 可显著降低摄像头转码和网络负载。
- 8kHz 麦克风采样可降低上行/下行音频带宽和 CPU 占用。
- 控制循环 20Hz 已能满足手动操控，CPU 占用更低。
- `MIC_DEVICE` 请替换为你实测可采集的设备名称。
- `SPEAKER_DEVICE` 默认已设为 `plughw:CARD=Device,DEV=0`（USB 扬声器）。

## 2.2 不启动服务的静态验收

如果当前不方便启动服务，可以先做静态检查：

```bash
# 1) Python 语法检查
python3 -m py_compile server/app.py server/chassis_driver.py server/media.py tts/engine.py

# 2) 底盘库存在性
ls -l gpio/libchassis.so

# 3) 前端资源完整性
ls -l server/static/index.html server/static/app.js server/static/styles.css
```

可选：先做空跑模式验证配置（不驱动电机）：

```bash
CHASSIS_DRY_RUN=1 uvicorn server.app:app --host 0.0.0.0 --port 3725
```

## 3. 环境变量

可选配置：

- `CHASSIS_LIB`：底盘库路径，默认 `gpio/libchassis.so`
- `CHASSIS_DRY_RUN`：设为 `1` 时不实际驱动电机，仅打印日志
- `CHASSIS_LOG_LEVEL`：底盘日志级别，默认 `1`
- `CAMERA_DEVICE`：摄像头设备，默认 `/dev/video0`
- `CAMERA_WIDTH`：视频宽度，默认 `640`
- `CAMERA_HEIGHT`：视频高度，默认 `480`
- `CAMERA_FPS`：视频帧率，默认 `25`
- `CAMERA_INPUT_FORMAT`：默认 `mjpeg`
- `MIC_DEVICE`：麦克风设备，默认 `default`
- `SPEAKER_DEVICE`：扬声器输出设备，默认 `plughw:CARD=Device,DEV=0`
- `MIC_SAMPLE_RATE`：默认 `16000`
- `MIC_CHANNELS`：默认 `1`
- `CONTROL_HZ`：控制循环频率，默认 `30`
- `CONTROL_TIMEOUT`：控制心跳超时秒数，默认 `1.0`
- `CONTROL_LR_FIX`：左右修正，`1` 启用（与 wasd_control 保持一致）
- `TTS_KEY`：豆包 Access Key（必填）；也支持 `TTS_KEY=<APPID>:<AccessKey>` 的单变量格式
- `TTS_APP_ID`：豆包 APP ID（当 `TTS_KEY` 仅包含 Access Key 时必填）
- `TTS_RESOURCE_ID`：资源信息 ID，默认 `seed-tts-1.0`
- `TTS_SPEAKER`：音色 ID，默认 `zh_female_cancan_mars_bigtts`
- `TTS_MODEL`：模型版本（可选），例如 `seed-tts-1.1` 或 `seed-tts-2.0`
- `TTS_SAMPLE_RATE`：采样率，默认 `24000`
- `TTS_SPEECH_RATE`：语速，范围 `[-50,100]`，默认 `0`
- `TTS_LOUDNESS_RATE`：音量，范围 `[-50,100]`，默认 `0`
- `TTS_WS_URL`：豆包单向流式地址，默认 `wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream`
- `TTS_TIMEOUT`：单次合成超时秒数，默认 `20`

说明：服务会校验 `CAMERA_DEVICE` 是否具备采集能力；如果该节点不是 capture 设备，会自动回退到首个可采集的 `/dev/videoX`。

## 4. 摄像头黑屏排查

如果出现如下报错：

```text
Device '/dev/video1' is not a capture device
```

通常表示当前节点不是采集节点（可能是 metadata 节点或编解码节点）。

建议按以下步骤确认：

```bash
v4l2-ctl --list-devices
for d in /dev/video*; do
	echo "=== $d ==="
	ffmpeg -hide_banner -f v4l2 -list_formats all -i "$d" 2>&1 | head -n 12
done
```

在 OrangePi Zero3 上，常见情况是：

- `/dev/video0`：USB 摄像头 capture 节点
- `/dev/video1`：同摄像头的非 capture 节点（不能用于采集）
- `/dev/video2`：cedrus 编解码节点（不能用于采集）

可显式指定：

```bash
CAMERA_DEVICE=/dev/video0 uvicorn server.app:app --host 0.0.0.0 --port 3725
```

## 5. 麦克风无声排查

如果网页端点击“开启监听音频”后依然听不到声音，通常是 `MIC_DEVICE=default` 绑定到错误音频源。

先列出设备：

```bash
arecord -l
arecord -L
```

再做 2 秒录音与幅度检查：

```bash
for dev in default "plughw:CARD=Device,DEV=0" "plughw:CARD=Device_1,DEV=0"; do
	safe=$(echo "$dev" | tr ':,=' '___')
	out="/tmp/mic_${safe}.wav"
	echo "=== TEST $dev ==="
	ffmpeg -hide_banner -loglevel error -f alsa -i "$dev" -t 2 -ac 1 -ar 16000 -y "$out"
	ffmpeg -hide_banner -i "$out" -af astats=metadata=1:reset=1 -f null - 2>&1 | grep -E "RMS level dB|Peak level dB" | head -n 4
	echo
done
```

判定规则：

- 出现 `RMS level dB: -inf` 基本就是静音源。
- 出现例如 `-30dB` 到 `-60dB` 的数值，说明有真实输入。

本机实测（OrangePi Zero3 当前环境）：

- `default`：`RMS level dB: -inf`（静音）
- `plughw:CARD=Device_1,DEV=0`：有有效输入（可用）

建议启动命令：

```bash
sudo MIC_DEVICE=plughw:CARD=Device_1,DEV=0 \
SPEAKER_DEVICE=plughw:CARD=Device,DEV=0 \
TTS_APP_ID=你的APPID \
TTS_KEY=你的AccessKey \
CAMERA_DEVICE=/dev/video0 \
CAMERA_WIDTH=320 CAMERA_HEIGHT=240 CAMERA_FPS=12 \
MIC_SAMPLE_RATE=8000 CONTROL_HZ=20 \
uvicorn server.app:app --host 0.0.0.0 --port 3725
```

浏览器权限说明：

- 点击“开启监听音频”不会弹麦克风权限，这是正常的（只做播放）。
- 监听音量可在页面“监听音量”滑块调节，必要时可使用“静音/取消静音”按钮。
- 点击“按住说话”才会申请麦克风权限。
- 如果页面是 `http://<IP>:3725`，很多浏览器不会弹授权（非安全上下文），请改用 HTTPS 或 localhost 测试对讲。

## 6. 备注

1. GPIO 控制通常需要 root 权限或 `/dev/gpiomem` 访问权限。
2. 如果浏览器无法播放下行音频，请先点击页面上的“开启监听音频”（浏览器自动播放策略要求用户手势）。
3. 对讲采用 16kHz 单声道 PCM，上行按钮为“按住说话”。

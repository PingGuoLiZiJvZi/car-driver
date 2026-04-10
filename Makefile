include .env
export

run:
	sudo uvicorn server.app:app --host 0.0.0.0 --port 3725

run_simple:
	sudo MIC_DEVICE=plughw:CARD=Device_1,DEV=0 \
	SPEAKER_DEVICE=plughw:CARD=Device,DEV=0 \
	CAMERA_DEVICE=/dev/video0 \
	CAMERA_WIDTH=320 \
	CAMERA_HEIGHT=240 \
	CAMERA_FPS=12 \
	MIC_SAMPLE_RATE=8000 \
	CONTROL_HZ=20 \
	TTS_KEY=$(TTS_KEY) \
	TTS_APP_ID=$(TTS_APP_ID) \
	uvicorn server.app:app --host 0.0.0.0 --port 3725

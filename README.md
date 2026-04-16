# AutoVideo

AutoVideo biến nội dung text thành video dọc 9:16 theo pipeline tự động:
- tách nội dung thành micro-scenes (`narration`, `onScreenText`, `imageQuery`)
- lấy media (ảnh/video stock)
- TTS theo scene
- render MP4 + (tuỳ chọn) upload YouTube

## Quick Start

### 1) Cài dependencies

```bash
pip install -r requirements.txt
pip install -e .
```

### 2) Cấu hình

- Chỉnh `config.yaml` (không chứa secret)
- Tạo `.env` để chứa API keys
- Mặc định output ở `output/`

Các mục hay dùng trong `config.yaml`:
- `pexels.media`: `photo` hoặc `video`
- `tts.enabled`: bật/tắt TTS
- `audio.bgm_enabled`: bật/tắt nhạc nền
- `audio.bgm_dir`: thư mục nhạc nền (mặc định `asset/songs`)
- `audio.bgm_volume`: âm lượng nhạc nền khi mix với giọng đọc
- `youtube.upload`: bật/tắt upload YouTube

### 3) Chạy API

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Swagger docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## Công nghệ sử dụng

**Mặc định đang dùng**
- Backend API: `FastAPI`, `Uvicorn`
- Data model/validation: `Pydantic`
- Config: `PyYAML`, `python-dotenv`
- HTTP client: `requests`
- Video render: `MoviePy`, `ffmpeg`/`ffprobe` (qua `imageio-ffmpeg`)
- Vẽ text/caption: `Pillow`
- TTS mặc định: `edge-tts`
- Media provider: Pexels/Unsplash integration
- YouTube upload: `google-api-python-client`, `google-auth`, `google-auth-oauthlib`

**Tùy chọn (bật theo config/env)**
- TTS chất lượng cao: `ElevenLabs` (`elevenlabs`)
- LLM backend: `Ollama` hoặc `Gemini` cho bước tạo micro-story

## API chính

- `POST /api/v1/jobs/build-script-from-txt`: upload file txt, tạo micro story
- `POST /api/v1/jobs/full-from-txt`: chạy full pipeline (build -> media -> tts -> render)
- `POST /api/v1/jobs/{job_id}/fetch-media`
- `POST /api/v1/jobs/{job_id}/tts`
- `POST /api/v1/jobs/{job_id}/render`
- `POST /api/v1/jobs/{job_id}/upload-youtube`

## Notes

- Nếu YouTube báo `invalid_grant`, xoá token cũ: `~/.cache/murphy_api/youtube/token.json` rồi upload lại để OAuth cấp mới.
- Nhạc nền sẽ tự nối/lặp playlist trong `audio.bgm_dir` nếu tổng thời lượng bài hát ngắn hơn video.


# AutoVideo Implementation

Mục tiêu: biến quote kiểu Murphy's Law thành YouTube Shorts dạng micro-scenes (mỗi scene ~`2–3s`), có:
- Visual: ảnh theo `imageQuery` (Pexels Photos / Unsplash hoặc fallback), hoặc **video stock** qua [Pexels Videos API](https://www.pexels.com/api/documentation/#videos-search) khi `PEXELS_MEDIA=video`
- Voice: TTS theo từng scene (`edge-tts`, có fallback silence)
- Text: caption overlay theo `onScreenText`
- Render: video 9:16 (1080x1920) bằng MoviePy
- (Tùy chọn) Upload: YouTube Data API

## Project structure

```text
AutoVideo/
  app.py                 # entry: python app.py → uvicorn
  pyproject.toml
  config.yaml
  Dockerfile
  requirements.txt
  .env.example
  input/
    example_quotes.json
  IMPLEMENTATION.md
  src/
    api/
      app.py
      schemas.py
      __main__.py
    config_loader.py
    pipeline.py
    pipeline_steps.py
    script_schema.py
    utils/ffmpeg.py
    utils/audio_duration.py
    llm/ollama_client.py
    images/pexels_unsplash.py
    tts/
    video/render_moviepy.py
    youtube/auth.py
    youtube/upload.py
```

## Các bước pipeline (end-to-end)

```mermaid
flowchart LR
  A[Input quotes JSON qua API] --> B[LLM build micro_story JSON]
  B --> C[Fetch ảnh hoặc video stock theo imageQuery]
  C --> D[TTS theo narration từng scene]
  D --> E[Render 9:16 MoviePy + captions + KenBurns zoom]
  E --> F[(Optional) Upload YouTube]
```

## Cấu hình: `config.yaml` + `.env`

- **`config.yaml`** (ở root repo): Ollama, Pexels/Unsplash *behavior* (media, orientation…), đường dẫn output/cache, TTS, YouTube metadata, v.v. Không chứa API key.
- **`.env`**: chỉ secrets — xem [`.env.example`](.env.example):
  - `PEXELS_API_KEY`
  - `UNSPLASH_ACCESS_KEY` (optional)

Đường dẫn file YAML khác: `AUTOVIDEO_CONFIG=/path/to/other.yaml`.

**Override tạm bằng env (tương thích ngược):** nếu bạn vẫn export các biến cũ (`OLLAMA_URL`, `PEXELS_MEDIA`, `TTS_ENABLED`, …), chúng **ưu tiên hơn** giá trị trong `config.yaml`.

Gợi ý nội dung chính trong `config.yaml`:

- `ollama`: `url`, `model`
- `pexels`: `media` (`photo` | `video`), `video_orientation`, `video_size`, `video_per_page`
- `paths`: `output_dir`, `image_cache_dir` (để trống = mặc định trong home)
- `image`: `provider`
- `tts`: `enabled`, `voice`
- `youtube`: `upload`, `client_secret_path`, `channel_id`, `title_prefix`, `description`, `tags`, `privacy_status`

## Input format (quotes JSON)

`input/example_quotes.json`:

```json
[
  {
    "id": "murphy_001",
    "quote": "Anything that can go wrong will go wrong.",
    "meaning_vi": "Mọi thứ có thể sai sẽ sai."
  }
]
```

## Micro-story JSON contract

`src/video/render_moviepy.py` consume `MicroStory.scenes[]`:

- `narration`: text để TTS
- `onScreenText`: text caption ngắn
- `imageQuery`: prompt để tìm ảnh hoặc video (tùy `pexels.media` trong `config.yaml`)
- `duration_seconds`: tùy chọn trong model; file `micro_story.json` lưu disk **không** ghi field này; độ dài đồng bộ từ audio khi cần.

## Chạy local (FastAPI)

1. Cài deps:

```bash
pip install -r requirements.txt
pip install -e .
```

2. Chỉnh `config.yaml`; tạo `.env` với API keys nếu dùng Pexels/Unsplash.

3. Chạy server:

```bash
python app.py
# hoặc:
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
# hoặc:
python -m api
```

OpenAPI / Swagger: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

**End-to-end (một POST):**

- `POST /api/v1/pipeline/full` — body JSON:
  - `quotes`: mảng `{ "id", "quote", "meaning_vi"? }`
  - `quote_id` (optional): chỉ xử lý một id
  - `use_llm`, `upload` (boolean)

Ghi ra thư mục `paths.output_dir` trong `config.yaml` (layout `output/<quote_id>/...`), **không** dùng `api_jobs/`.

**Từng bước (job trong `output/api_jobs/<job_id>/`):**

1. `POST /api/v1/jobs/build-script` — body `{ "quote": { ... }, "use_llm": true }` → trả về `job_id` + `micro_story`
2. `POST /api/v1/jobs/{job_id}/fetch-media`
3. `POST /api/v1/jobs/{job_id}/tts` — optional query `?tts_enabled=true|false` (mặc định theo config)
4. `POST /api/v1/jobs/{job_id}/render`
5. `POST /api/v1/jobs/{job_id}/upload` — chỉ khi `youtube.upload: true`

**Đọc trạng thái / tải file:**

- `GET /api/v1/jobs/{job_id}` — cờ `has_script`, `has_media`, …
- `GET /api/v1/jobs/{job_id}/video` — file MP4 (sau khi render)
- `GET /health`

Các bước tách chạy **đồng bộ** (request chờ xong mới trả về); video dài có thể mất vài phút — production nên cân nhắc hàng đợi / worker.

## Docker

Build image:

```bash
docker build -t autovideo .
```

Chạy API (mặc định `CMD` là `python app.py`):

```bash
docker run --rm -p 8000:8000 \
  -v "$PWD/output:/app/output" \
  -v "$PWD/.env:/app/.env:ro" \
  autovideo
```

## YouTube upload (optional)

Bật trong `config.yaml` (`youtube.upload: true`) hoặc env `YOUTUBE_UPLOAD=true`, rồi gọi `POST /api/v1/jobs/{job_id}/upload` sau khi render (hoặc `upload: true` với `POST /api/v1/pipeline/full`).

Yêu cầu: có `client_secret.json` đúng đường dẫn (mặc định `GOOGLE_CLIENT_SECRET_PATH=client_secret.json`).

## Notes bản quyền (khuyến nghị)

- Tránh copy nguyên văn dài từ sách.
- Với Murphy's Law, ưu tiên dùng quote rất ngắn + diễn giải/viết lại để giảm rủi ro.

from __future__ import annotations
import json
import re
from typing import Any
import requests
from src.utils.config_loader import cfg_str, env_api_key, cfg_bool
from src.utils.schemas import MicroStory, PipelinePaths
from src.utils.helper import _ensure_dirs
from pathlib import Path

def _build_micro_story(text: str) -> MicroStory:
    fixed_scenes = []
    text = text.replace("\n", " ")
    sentences = _split_sentences(text)
    keywords = _keywords_for_sentences(sentences)
    image_queries = _image_queries_for_sentences(keywords, sentences)
    for i in range(len(sentences)):
        keyword = keywords[i] if i < len(keywords) else _keyword_for_segment(sentences[i])
        image_query = (
            image_queries[i]
            if i < len(image_queries)
            else _image_query_for_segment(keyword, sentences[i])
        )
        fixed_scenes.append(
            {
                "narration": sentences[i],
                "onScreenText": keyword,
                "imageQuery": image_query,
            }
        )
    return MicroStory(
        title_hook="",
        voice_text_full=text,
        scenes=fixed_scenes,
        youtube_title="",
        youtube_description="",
        youtube_tags="",
        youtube_privacy_status="private",
    )


def _split_sentences(text):
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _keywords_for_sentences(sentences: list[str]) -> list[str]:
    if not sentences:
        return []
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
    prompt = f"""
        Tìm keyword cho từng câu theo đúng thứ tự.

        Danh sách câu:
        {numbered}

        Yêu cầu:
        - Trả về STRICT JSON duy nhất:
        [{{"idx": number, "keyword": string}}]
        - idx khớp số thứ tự câu.
        - keyword ngắn gọn (3-6 từ), <= 60 ký tự.
        - Không trả thêm chữ ngoài JSON.
    """
    try:
        parsed = _generate_json(prompt)
        if not isinstance(parsed, list):
            return [s[:60] for s in sentences]
        mapped: dict[int, str] = {}
        for x in parsed:
            if not isinstance(x, dict):
                continue
            idx = _coerce_sentence_idx(x.get("idx"), len(sentences))
            kw = str(x.get("keyword", "")).strip()
            if idx is not None and kw:
                mapped[idx] = kw[:60]
        return [mapped.get(i, sentences[i][:60]) for i in range(len(sentences))]
    except Exception:
        return [s[:60] for s in sentences]


def _image_queries_for_sentences(keywords: list[str], sentences: list[str]) -> list[str]:
    if not sentences:
        return []
    numbered = "\n".join(
        f'{i}. keyword="{keywords[i] if i < len(keywords) else ""}" | sentence="{sentences[i]}"'
        for i in range(len(sentences))
    )
    prompt = f"""
        Create image search query for each item in order.

        Input:
        {numbered}

        Requirements:
        - Output STRICT JSON only:
        [{{"idx": number, "imageQuery": string}}]
        - Keep order by idx.
        - imageQuery in English, max 5 words.
        - No extra text.
    """
    try:
        parsed = _generate_json(prompt)
        if not isinstance(parsed, list):
            return [f"{keywords[i] if i < len(keywords) else ''} {sentences[i]}"[:200] for i in range(len(sentences))]
        mapped: dict[int, str] = {}
        for x in parsed:
            if not isinstance(x, dict):
                continue
            idx = _coerce_sentence_idx(x.get("idx"), len(sentences))
            iq = str(x.get("imageQuery", "")).strip()
            if idx is not None and iq:
                mapped[idx] = " ".join(iq.split()[:5])[:200]
        return [mapped.get(i, f"{keywords[i] if i < len(keywords) else ''} {sentences[i]}"[:200]) for i in range(len(sentences))]
    except Exception:
        return [f"{keywords[i] if i < len(keywords) else ''} {sentences[i]}"[:200] for i in range(len(sentences))]


def _save_micro_story(paths: PipelinePaths, story: MicroStory, quote_id: str) -> Path:
    _ensure_dirs(paths)
    p = paths.assets_dir / cfg_str("micro_story", "json_file_name")
    data = {
        "quote_id": quote_id,
        **json.loads(story.model_dump_json()),
    }
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def _extract_json_object(text: str) -> Any:
    """
    Ollama may wrap JSON in extra text. We try to extract the first JSON object/array.

    Important: do not use greedy ``\\{.*\\}`` before arrays — for output like
    ``[{"idx":0,...},{"idx":1,...}]`` that captures from the first ``{`` to the *last*
    ``}``, which is invalid JSON and forces fallbacks (truncated sentence keywords).
    """
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
        raw = raw.strip()
    decoder = json.JSONDecoder()
    for start_ch in "[{":
        i = raw.find(start_ch)
        if i < 0:
            continue
        try:
            val, _ = decoder.raw_decode(raw[i:])
            return val
        except json.JSONDecodeError:
            continue
    raise ValueError("No JSON object/array found in LLM output.")


# def _sanitize_micro_story_payload(parsed: Any) -> Any:
#     """
#     Normalize/truncate model output before pydantic validation.
#     Prevents hard failures when providers return overlong strings.
#     """
#     if not isinstance(parsed, dict):
#         return parsed
#     scenes = parsed.get("scenes")
#     if not isinstance(scenes, list):
#         return parsed
#     for scene in scenes:
#         if not isinstance(scene, dict):
#             continue
#         if isinstance(scene.get("onScreenText"), str):
#             scene["onScreenText"] = scene["onScreenText"].strip()[:120]
#         if isinstance(scene.get("imageQuery"), str):
#             scene["imageQuery"] = scene["imageQuery"].strip()[:200]
#         if isinstance(scene.get("narration"), str):
#             scene["narration"] = scene["narration"].strip()
#     return parsed


def _coerce_sentence_idx(idx: Any, n: int) -> int | None:
    if idx is None or isinstance(idx, bool):
        return None
    if isinstance(idx, int) and 0 <= idx < n:
        return idx
    if isinstance(idx, float) and idx.is_integer():
        i = int(idx)
        return i if 0 <= i < n else None
    if isinstance(idx, str):
        try:
            i = int(idx.strip())
        except ValueError:
            return None
        return i if 0 <= i < n else None
    return None


def _generate_json(prompt: str) -> Any:
    """Generate JSON (object or list) via configured LLM provider."""
    if cfg_bool("gemini", "enabled", default=False):
        api_key = env_api_key("GEMINI_API_KEY").strip()
        model = cfg_str("gemini", "model", default="gemini-3-flash-preview")
        if not api_key:
            raise RuntimeError("Missing gemini.api_key in config.yaml (or GEMINI_API_KEY in environment).")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        }
        r = requests.post(url, json=payload, timeout=90)
        r.raise_for_status()
        out = r.json()
        text = (
            out.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            or ""
        )
    elif cfg_bool("ollama", "enabled", default=False):
        ollama_url = cfg_str("ollama", "url", env_legacy="OLLAMA_URL", default="").strip()
        model = cfg_str("ollama", "model", env_legacy="OLLAMA_MODEL", default="llama3")
        if not ollama_url:
            raise RuntimeError("Missing ollama.url in config.yaml (or OLLAMA_URL in environment).")
        payload = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}}
        r = requests.post(f"{ollama_url.rstrip('/')}/api/generate", json=payload, timeout=90)
        r.raise_for_status()
        out = r.json()
        text = out.get("response", "") or ""
    else:
        raise RuntimeError("No LLM provider configured. Enable gemini or ollama in config.")

    return _extract_json_object(text)


def _keyword_for_segment(seg: str) -> str:
    prompt = f"""
        Tóm tắt ý chính của câu sau thành 1 cụm ngắn (3-6 từ, tiếng Việt):

        Câu: {seg}

        Yêu cầu:
        - Ngắn gọn, đúng ý chính
        - Không giải thích
        - Xuất STRICT JSON:
        {{"keyword": string}}
    """

    try:
        parsed = _generate_json(prompt)
        if isinstance(parsed, dict):
            keyword = str(parsed.get("keyword", "")).strip()
            return keyword[:60] if keyword else seg[:60]
        return seg[:60]
    except Exception:
        return seg[:60]


def _image_query_for_segment(keyword: str, seg: str) -> str:
    prompt = f"""
        Create a short image search query in English.

        Context:
        {seg}

        Requirements:
        - Max 5 words
        - English only
        - Focus on visual elements (people, action, concept)
        - No explanation

        Output STRICT JSON:
        {{"imageQuery": string}}
    """

    try:
        parsed = _generate_json(prompt)
        if not isinstance(parsed, dict):
            return "human reflection psychology"
        query = str(parsed.get("imageQuery", "")).strip()

        # hard limit 5 words
        query_words = query.split()[:5]
        return " ".join(query_words)

    except Exception:
        # fallback
        return "human reflection psychology"

def _load_micro_story(paths: PipelinePaths) -> MicroStory:
    p = paths.assets_dir / cfg_str("micro_story", "json_file_name")
    if not p.exists():
        raise FileNotFoundError("micro_story.json not found")
    return MicroStory.model_validate_json(p.read_text(encoding="utf-8"))

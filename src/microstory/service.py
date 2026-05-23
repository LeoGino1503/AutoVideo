from __future__ import annotations
import json
import re
from typing import Any
import requests
from src.utils.config_loader import cfg_str, env_api_key, cfg_bool, cfg_float, cfg_int
from src.utils.schemas import MicroStory, PipelinePaths
from src.utils.helper import ensure_dirs
from pathlib import Path
import time

def _build_micro_story(text: str, *, quote_id: str | None = None) -> MicroStory:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Input text is empty.")
    fixed_scenes = []
    text = raw.replace("\n", " ")
    sentences = _split_and_merge_sentences(text)
    if not sentences:
        raise ValueError("No sentences found in input; cannot build micro story.")
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
        quote_id=quote_id,
        title_hook="",
        voice_text_full=text,
        scenes=fixed_scenes,
        youtube_title="",
        youtube_description="",
        youtube_tags="",
        youtube_privacy_status="private",
    )


# Placeholder for '.' inside abbreviations/initials (restored after sentence split).
_ABBREV_DOT = "\u00b7"


def _split_and_merge_sentences(text: str) -> list[str]:
    sentences = _split_sentences(text)
    if cfg_bool("micro_story", "merge_short_scenes", default=True):
        sentences = _merge_short_segments(sentences)
    return sentences


def _protect_abbreviations(text: str) -> str:
    """Mask periods that must not act as sentence boundaries."""
    # Name initials: "A. s. Lochins", "J. F. Kennedy"
    text = re.sub(
        r"(?:[A-Z]\.\s+)(?:[a-zà-ỹăâđêôơư]{1,3}\.\s+)+",
        lambda m: m.group(0).replace(". ", f"{_ABBREV_DOT} ").replace(".", _ABBREV_DOT),
        text,
    )
    # Trailing initial before lowercase dotted initial (e.g. "… Mỹ A. s.")
    text = re.sub(
        r"(?:^|[\s\"'(\[])([A-Z])\.\s+(?=[a-zà-ỹăâđêôơư]{1,3}\.)",
        lambda m: f"{m.group(1)}{m.group(2)}{_ABBREV_DOT} ",
        text,
    )
    # Common dotted abbreviations (v.v., Dr., U.S., …)
    for pat in (
        r"\bv\.\s*v\.",
        r"\b[Vv]\.[Vv]\.",
        r"\b[Dd]r\.",
        r"\b[Mm]r\.",
        r"\b[Mm]rs\.",
        r"\b[Mm]s\.",
        r"\b[Pp]rof\.",
        r"\b[Tt][Ss]\.",
        r"\b[Tt]h[Ss]\.",
        r"\b[Pp][Gg][Ss]\.",
        r"\b[Gg][Ss]\.",
        r"\bU\.\s*S\.",
        r"\b[A-Z]\.\s*[A-Z]\.",
    ):
        text = re.sub(pat, lambda m: m.group(0).replace(".", _ABBREV_DOT), text, flags=re.IGNORECASE)
    return text


def _restore_abbreviation_dots(text: str) -> str:
    return text.replace(_ABBREV_DOT, ".")


def _split_sentences(text: str) -> list[str]:
    raw = text.strip()
    if not raw:
        return []
    if cfg_bool("micro_story", "protect_abbreviations", default=True):
        raw = _protect_abbreviations(raw)
    sentences = re.split(r"(?<=[.!?])\s+", raw)
    out: list[str] = []
    for sentence in sentences:
        s = _restore_abbreviation_dots(sentence.strip())
        if s:
            out.append(s)
    return out


def _merge_short_segments(sentences: list[str]) -> list[str]:
    if not sentences:
        return []
    min_words = max(1, cfg_int("micro_story", "min_scene_words", default=4))
    min_chars = max(1, cfg_int("micro_story", "min_scene_chars", default=20))
    merged: list[str] = []
    for s in sentences:
        if not merged:
            merged.append(s)
            continue
        prev = merged[-1]
        if _should_merge_with_previous(prev, s, min_words=min_words, min_chars=min_chars):
            merged[-1] = _join_narration_segments(prev, s)
        else:
            merged.append(s)
    return merged


def _should_merge_with_previous(
    prev: str,
    cur: str,
    *,
    min_words: int,
    min_chars: int,
) -> bool:
    cur_stripped = cur.strip()
    prev_stripped = prev.strip()
    cur_words = len(cur_stripped.split())
    prev_words = len(prev_stripped.split())
    cur_chars = len(cur_stripped)

    if cur_words <= 2 and re.fullmatch(r"[a-zà-ỹăâđêôơư]{1,3}\.?", cur_stripped, re.IGNORECASE):
        return True
    if prev_words <= min_words or len(prev_stripped) < min_chars:
        return True
    if cur_words <= min_words or cur_chars < min_chars:
        return True
    if re.search(r"[A-ZĐ]\.\s*$", prev_stripped):
        return True
    return False


def _join_narration_segments(prev: str, cur: str) -> str:
    prev = prev.rstrip()
    cur = cur.lstrip()
    if prev.endswith((".", "!", "?")) and cur and cur[0].islower():
        return f"{prev} {cur}"
    return f"{prev} {cur}"


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
    except Exception as exc:
        raise RuntimeError("Failed to generate keywords for sentences.") from exc

    if not isinstance(parsed, list):
        raise ValueError("Invalid keywords response format: expected a JSON list.")

    mapped: dict[int, str] = {}
    for x in parsed:
        if not isinstance(x, dict):
            continue
        idx = _coerce_sentence_idx(x.get("idx"), len(sentences))
        kw = str(x.get("keyword", "")).strip()
        if idx is not None and kw:
            mapped[idx] = kw[:60]
    return [mapped.get(i, sentences[i][:60]) for i in range(len(sentences))]


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
    ensure_dirs(paths)
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
    request_delay_seconds = max(0.0, cfg_float("llm", "request_delay_seconds", default=0.0))

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
        if request_delay_seconds > 0:
            time.sleep(request_delay_seconds)
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
        if request_delay_seconds > 0:
            time.sleep(request_delay_seconds)
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

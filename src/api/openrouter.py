"""OpenRouter API client: OAuth PKCE, key validation, STT transcription and language study."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import re
import secrets
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .. import config
from ..i18n import _
from ..models import TranscriptSegment
from . import http
from .transcript_parser import parse_json_transcript

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
AUTH_URL = "https://openrouter.ai/auth"
KEYS_URL = "https://openrouter.ai/settings/keys"
CREDITS_URL = "https://openrouter.ai/settings/credits"

DEFAULT_STT_MODEL = "microsoft/mai-transcribe-2"
DEFAULT_STUDY_MODEL = "google/gemini-2.5-flash-lite"

STT_MODELS: tuple[tuple[str, str, str], ...] = (
    ("microsoft/mai-transcribe-2", "MAI-Transcribe 2",
     "Microsoft · rápido, timestamps por palavra e ótimo custo-benefício"),
    ("deepgram/nova-3", "Deepgram Nova 3",
     "Alta precisão em inglês · ~US$ 0,26/h"),
    ("openai/whisper-large-v3-turbo", "Whisper Large v3 Turbo",
     "OpenAI · multilíngue rápido · ~US$ 0,36/h"),
    ("openai/whisper-1", "Whisper 1",
     "OpenAI · clássico e estável · ~US$ 0,36/h"),
    ("elevenlabs/scribe-v2", "ElevenLabs Scribe v2",
     "Excelente em múltiplos sotaques"),
)

STUDY_MODELS: tuple[tuple[str, str, str], ...] = (
    ("google/gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite",
     "Google · ultrarrápido e muito econômico para tradução e estudo"),
    ("openai/gpt-4o-mini", "GPT-4o Mini",
     "OpenAI · ótimo equilíbrio entre clareza e custo"),
    ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5",
     "Anthropic · explicações naturais e detalhadas de expressões"),
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash",
     "Google · alta qualidade para contexto e gramática"),
)

SOURCE_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("auto", "Automático"),
    ("en", "Inglês"),
    ("pt", "Português"),
    ("es", "Espanhol"),
    ("fr", "Francês"),
    ("de", "Alemão"),
    ("it", "Italiano"),
    ("ja", "Japonês"),
)

TARGET_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("pt-BR", "Português (BR)"),
    ("en", "English"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
)


class OpenRouterError(Exception):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class KeyInfo:
    label: str = ""
    usage: float = 0.0
    limit: float | None = None
    limit_remaining: float | None = None
    is_free_tier: bool = False


@dataclass
class TranscriptionResult:
    segments: list[TranscriptSegment] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    language: str = ""


@dataclass
class StudyVocabItem:
    term: str
    meaning: str
    note: str = ""


@dataclass
class PhraseStudy:
    translation: str
    summary: str = ""
    vocabulary: list[StudyVocabItem] = field(default_factory=list)
    grammar_tip: str = ""

    def to_markdown(self) -> str:
        parts: list[str] = []
        if self.summary:
            parts.append(self.summary.strip())
        if self.vocabulary:
            lines = []
            for item in self.vocabulary:
                line = f"• {item.term} — {item.meaning}"
                if item.note:
                    line += f" ({item.note})"
                lines.append(line)
            parts.append("\n".join(lines))
        if self.grammar_tip:
            parts.append(_("Dica: {tip}").format(tip=self.grammar_tip.strip()))
        return "\n\n".join(parts)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "HTTP-Referer": config.WEBSITE,
        "X-Title": config.APP_NAME,
    }


def _extract_error_message(error: http.NetworkError) -> str:
    if error.body:
        try:
            payload = json.loads(error.body.decode("utf-8", errors="replace"))
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict) and err.get("message"):
                    return str(err["message"])
                if isinstance(err, str) and err:
                    return err
                if payload.get("message"):
                    return str(payload["message"])
        except ValueError:
            pass
    if error.status == 401:
        return _("Chave da API inválida ou revogada")
    if error.status == 402:
        return _("Créditos insuficientes na sua conta do OpenRouter")
    if error.status == 429:
        return _("Limite de requisições atingido no OpenRouter. Tente novamente em instantes")
    if error.status == 404:
        return _("Modelo não encontrado ou indisponível no OpenRouter")
    return str(error)


def _request_json(method: str, path: str, *, api_key: str | None = None,
                  body: Any = None, timeout: float = 45.0) -> dict[str, Any]:
    headers = _headers(api_key) if api_key else {
        "HTTP-Referer": config.WEBSITE,
        "X-Title": config.APP_NAME,
    }
    url = f"{BASE_URL}{path}"
    try:
        _status, raw = http.request(method, url, body=body, headers=headers,
                                    timeout=timeout, max_bytes=32 * 1024 * 1024)
    except http.OfflineError as error:
        raise OpenRouterError(_("Você está offline")) from error
    except http.NetworkError as error:
        raise OpenRouterError(_extract_error_message(error), status=error.status) from error
    try:
        data = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
    except ValueError as error:
        raise OpenRouterError(_("Resposta inválida do OpenRouter")) from error
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        msg = str(data["error"].get("message") or _("Erro do OpenRouter"))
        raise OpenRouterError(msg)
    return data if isinstance(data, dict) else {"data": data}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# -- Key validation & OAuth PKCE -------------------------------------------------


def validate_key(api_key: str) -> KeyInfo:
    """Verify an OpenRouter API key and fetch its label, usage and credit limit."""
    key = api_key.strip()
    if not key:
        raise OpenRouterError(_("Informe uma chave da API do OpenRouter"))
    try:
        payload = _request_json("GET", "/auth/key", api_key=key, timeout=15.0)
    except OpenRouterError as error:
        if error.status == 404:
            payload = _request_json("GET", "/key", api_key=key, timeout=15.0)
        else:
            raise
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return KeyInfo(
        label=str(data.get("label") or ""),
        usage=_float(data.get("usage"), 0.0),
        limit=_opt_float(data.get("limit")),
        limit_remaining=_opt_float(data.get("limit_remaining")),
        is_free_tier=bool(data.get("is_free_tier", False)),
    )


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using S256."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def exchange_pkce_code(code: str, code_verifier: str) -> str:
    """Exchange an OpenRouter OAuth PKCE authorization code for a user-owned API key."""
    payload = _request_json(
        "POST",
        "/auth/keys",
        body={
            "code": code.strip(),
            "code_verifier": code_verifier,
            "code_challenge_method": "S256",
        },
        timeout=20.0,
    )
    key = str(payload.get("key") or "").strip()
    if not key:
        raise OpenRouterError(_("O OpenRouter não retornou uma chave de API"))
    return key


class PKCECallbackServer:
    """Temporary loopback HTTP server to receive OpenRouter's OAuth PKCE callback."""

    def __init__(self, on_code):
        self.verifier, self.challenge = generate_pkce()
        self._on_code = on_code
        self._done = threading.Event()
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed.query)
                code = (params.get("code") or [""])[0].strip()
                error = (params.get("error") or [""])[0].strip()
                if code:
                    body = (
                        "<!doctype html><html><head><meta charset='utf-8'>"
                        "<title>PodFlow</title><style>"
                        "body{font-family:system-ui,sans-serif;display:flex;align-items:center;"
                        "justify-content:center;min-height:100vh;margin:0;background:#16161a;"
                        "color:#f5f5f7;text-align:center}main{max-width:420px;padding:32px;"
                        "border-radius:16px;background:#222228}h1{font-size:1.35rem;margin:0 0 8px}"
                        "p{color:#b6b6c2;line-height:1.5;margin:0}</style></head><body><main>"
                        "<h1>Conectado ao PodFlow</h1>"
                        "<p>Sua conta do OpenRouter foi autorizada. Você já pode fechar esta aba "
                        "e voltar para o aplicativo.</p></main></body></html>"
                    )
                    self.send_response(200)
                else:
                    msg = html.escape(error or "Autorização cancelada.")
                    body = (
                        "<!doctype html><html><head><meta charset='utf-8'><title>PodFlow</title>"
                        "</head><body style='font-family:system-ui;padding:40px;text-align:center'>"
                        f"<h2>Não foi possível conectar</h2><p>{msg}</p></body></html>"
                    )
                    self.send_response(400)
                encoded = body.encode("utf-8")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                if not parent._done.is_set():
                    parent._done.set()
                    parent._on_code(code, error)

            def log_message(self, _format, *_args):
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._server.timeout = 0.5
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="podflow-openrouter-pkce")
        self._thread.start()

    @property
    def callback_url(self) -> str:
        return f"http://localhost:{self.port}/callback"

    @property
    def auth_url(self) -> str:
        query = urllib.parse.urlencode({
            "callback_url": self.callback_url,
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
        })
        return f"{AUTH_URL}?{query}"

    def _serve(self) -> None:
        while not self._done.is_set():
            try:
                self._server.handle_request()
            except OSError:
                break
        try:
            self._server.server_close()
        except OSError:
            pass

    def stop(self) -> None:
        self._done.set()
        try:
            self._server.server_close()
        except OSError:
            pass


# -- Audio transcription (STT) ---------------------------------------------------


def transcribe_audio(api_key: str, audio_bytes: bytes, *,
                     model: str = DEFAULT_STT_MODEL,
                     audio_format: str = "wav",
                     offset_seconds: float = 0.0,
                     language: str = "auto",
                     prompt: str = "",
                     timeout: float = 60.0) -> TranscriptionResult:
    """Transcribe an audio slice via OpenRouter's `/api/v1/audio/transcriptions`."""
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    base_body: dict[str, Any] = {
        "model": model.strip() or DEFAULT_STT_MODEL,
        "input_audio": {"data": encoded, "format": audio_format},
    }
    if language and language != "auto":
        base_body["language"] = language
    if prompt:
        base_body["prompt"] = prompt[-240:]

    # Try richest timestamps first; gracefully downgrade if a provider rejects verbose_json/word.
    attempts = (
        {"response_format": "verbose_json", "timestamp_granularities": ["segment", "word"]},
        {"response_format": "verbose_json", "timestamp_granularities": ["segment"]},
        {"response_format": "verbose_json"},
        {},
    )
    payload: dict[str, Any] | None = None
    last_error: OpenRouterError | None = None
    for extra in attempts:
        body = dict(base_body)
        body.update(extra)
        try:
            payload = _request_json("POST", "/audio/transcriptions", api_key=api_key,
                                    body=body, timeout=timeout)
            break
        except OpenRouterError as error:
            last_error = error
            if error.status == 400 and extra:
                log.debug("OpenRouter STT recusou %s para %s (%s); tentando formato mais simples",
                          extra, model, error)
                continue
            raise
    if payload is None:
        assert last_error is not None
        raise last_error

    segments = parse_json_transcript(payload, offset=offset_seconds, source="openrouter")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    cost = _float(usage.get("cost"), 0.0)
    seconds = _float(usage.get("seconds"), _float(payload.get("duration"), 0.0))
    detected_lang = str(payload.get("language") or "")
    return TranscriptionResult(
        segments=segments,
        cost_usd=cost,
        duration_seconds=seconds,
        language=detected_lang,
    )


# -- Language study LLM helpers --------------------------------------------------


def _language_label(code: str) -> str:
    for key, label in TARGET_LANGUAGES:
        if key == code:
            return label
    return code or "Português (Brasil)"


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _chat_json(api_key: str, model: str, system_prompt: str, user_prompt: str, *,
               max_tokens: int = 1200, timeout: float = 40.0) -> tuple[Any, float]:
    body = {
        "model": model.strip() or DEFAULT_STUDY_MODEL,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    payload = _request_json("POST", "/chat/completions", api_key=api_key,
                            body=body, timeout=timeout)
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise OpenRouterError(_("O modelo não retornou resposta"))
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    cost = _float(usage.get("cost"), 0.0)
    cleaned = _strip_code_fence(str(content))
    try:
        return json.loads(cleaned), cost
    except ValueError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, re.S)
        if match:
            try:
                return json.loads(match.group(0)), cost
            except ValueError:
                pass
        return cleaned, cost


def translate_segments(api_key: str, texts: list[str], *,
                       model: str = DEFAULT_STUDY_MODEL,
                       target_lang: str = "pt-BR") -> tuple[list[str], float]:
    """Translate a batch of transcript lines into `target_lang` preserving order."""
    if not texts:
        return [], 0.0
    lang_name = _language_label(target_lang)
    numbered = [{"i": idx, "t": text} for idx, text in enumerate(texts)]
    system = (
        f"You are a concise, natural translator for podcast language learners. "
        f"Translate each podcast transcript line into {lang_name}. "
        f"Preserve natural spoken tone. Return ONLY valid JSON: "
        f'{{"translations": ["line 0", "line 1", ...]}} with the exact same number of items.'
    )
    user = json.dumps({"items": numbered}, ensure_ascii=False)
    parsed, cost = _chat_json(api_key, model, system, user,
                              max_tokens=max(400, len(texts) * 90))
    translations: list[str] = []
    if isinstance(parsed, dict) and isinstance(parsed.get("translations"), list):
        translations = [str(item).strip() for item in parsed["translations"]]
    elif isinstance(parsed, list):
        translations = [str(item).strip() for item in parsed]
    if len(translations) != len(texts):
        # Pad or trim safely so alignment never breaks
        if len(translations) < len(texts):
            translations.extend([""] * (len(texts) - len(translations)))
        else:
            translations = translations[:len(texts)]
    return translations, cost


def explain_phrase(api_key: str, phrase: str, *,
                   context: str = "",
                   model: str = DEFAULT_STUDY_MODEL,
                   target_lang: str = "pt-BR") -> tuple[PhraseStudy, float]:
    """Generate a study breakdown (translation, idioms/vocab, tip) for a spoken cue."""
    lang_name = _language_label(target_lang)
    system = (
        f"You are an expert language tutor helping a learner study a podcast transcript. "
        f"Respond in {lang_name}. Return ONLY valid JSON with this schema:\n"
        '{"translation": "natural translation of the phrase", '
        '"summary": "1 short sentence explaining the meaning/nuance in context", '
        '"vocabulary": [{"term": "word or idiom from the phrase", "meaning": "meaning", '
        '"note": "optional usage/pronunciation note"}], '
        '"grammar_tip": "1 short practical tip about grammar, phrasal verb, slang or connected speech"}'
    )
    user_parts = [f"Phrase: {phrase.strip()}"]
    if context.strip():
        user_parts.append(f"Surrounding context: {context.strip()}")
    parsed, cost = _chat_json(api_key, model, system, "\n".join(user_parts), max_tokens=700)

    if isinstance(parsed, dict):
        vocab: list[StudyVocabItem] = []
        raw_vocab = parsed.get("vocabulary")
        if isinstance(raw_vocab, list):
            for entry in raw_vocab[:6]:
                if isinstance(entry, dict) and entry.get("term"):
                    vocab.append(StudyVocabItem(
                        term=str(entry.get("term", "")).strip(),
                        meaning=str(entry.get("meaning", "")).strip(),
                        note=str(entry.get("note", "")).strip(),
                    ))
        return PhraseStudy(
            translation=str(parsed.get("translation") or "").strip(),
            summary=str(parsed.get("summary") or "").strip(),
            vocabulary=vocab,
            grammar_tip=str(parsed.get("grammar_tip") or "").strip(),
        ), cost

    return PhraseStudy(translation="", summary=str(parsed).strip()), cost

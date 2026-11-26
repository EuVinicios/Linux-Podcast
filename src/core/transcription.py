"""Transcript orchestration: free RSS transcripts, progressive live OpenRouter STT, and study tools.

Cascade for any episode:
  1. SQLite cache (instant, zero network, zero cost)
  2. Official `<podcast:transcript>` from RSS (free, works without any API key)
  3. Progressive look-ahead OpenRouter STT (opt-in BYOK, transcribes short slices
     ahead of the playback cursor and caches them permanently in SQLite)
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gi.repository import GObject, Gst

from .. import config
from ..api import http, openrouter
from ..api.transcript_parser import parse_transcript
from ..i18n import _
from ..models import Episode, StudyNote, TranscriptSegment
from ..utils import tasks
from .audio_chunks import AudioExtractionCancelled, AudioExtractionError, extract_wav_chunk

log = logging.getLogger(__name__)

CHUNK_SECONDS = 40.0
OVERLAP_SECONDS = 1.5
LOOKAHEAD_CHUNKS = 1  # transcribe current chunk + 1 chunk ahead
TRANSLATION_BATCH = 12


def make_study_note_id(episode_id: str, text: str) -> str:
    digest = hashlib.sha1(f"{episode_id}\n{text.strip().lower()}".encode("utf-8")).hexdigest()[:20]
    return f"sn_{digest}"


def _dedupe_segments(existing: list[TranscriptSegment],
                     incoming: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Filter out overlapping boundary duplicates when merging progressive chunks."""
    if not existing:
        return incoming
    result: list[TranscriptSegment] = []
    for seg in incoming:
        duplicate = False
        seg_norm = seg.text.strip().lower()
        for prev in existing:
            if abs(prev.start - seg.start) <= 1.2:
                prev_norm = prev.text.strip().lower()
                if prev_norm == seg_norm or seg_norm in prev_norm or prev_norm in seg_norm:
                    duplicate = True
                    break
            if prev.start <= seg.start < prev.end - 0.35 and seg_norm == prev.text.strip().lower():
                duplicate = True
                break
        if not duplicate:
            result.append(seg)
    return result


class TranscriptionManager(GObject.Object):
    __gtype_name__ = "PodFlowTranscriptionManager"

    __gsignals__ = {
        # episode_id
        "segments-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # episode_id, state ("idle", "loading", "transcribing", "ready", "error"), detail
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
        # episode_id
        "translation-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, db, ai, playback=None):
        super().__init__()
        self.db = db
        self.ai = ai
        self.playback = playback
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="podflow-stt")
        self._study_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="podflow-study")
        self._lock = threading.Lock()
        self._states: dict[str, tuple[str, str]] = {}
        self._rss_attempted: set[str] = set()
        self._live_episodes: set[str] = set()
        self._active_episode: Episode | None = None
        self._active_cancel: threading.Event | None = None
        self._active_generation = 0
        self._in_flight_chunks: set[tuple[str, int]] = set()
        self._translating: set[str] = set()

        if playback is not None:
            playback.connect("episode-changed", self._on_episode_changed)
            playback.connect("position-changed", self._on_position_changed)
            playback.connect("seeked", self._on_seeked)

    # -- status helpers --------------------------------------------------------------

    def state_for(self, episode_id: str) -> tuple[str, str]:
        return self._states.get(episode_id, ("idle", ""))

    def is_live_active(self, episode_id: str) -> bool:
        return episode_id in self._live_episodes

    def segments_for(self, episode_id: str) -> list[TranscriptSegment]:
        return self.db.list_transcript_segments(episode_id)

    def source_for(self, episode_id: str) -> str:
        return self.db.transcript_source(episode_id)

    def _set_state(self, episode_id: str, state: str, detail: str = "") -> None:
        self._states[episode_id] = (state, detail)
        if tasks.is_main_thread():
            self.emit("state-changed", episode_id, state, detail)
        else:
            tasks.idle(self.emit, "state-changed", episode_id, state, detail)

    # -- RSS free transcript (Layer 1 & 2) -------------------------------------------

    def ensure_free_transcript(self, episode: Episode) -> bool:
        """Load cached transcript or fetch `<podcast:transcript>` from RSS (free).

        Returns True if a transcript is already available or being fetched from RSS.
        """
        if self.db.has_transcript(episode.id):
            src = self.db.transcript_source(episode.id)
            if src == "rss":
                self._set_state(episode.id, "ready", _("Transcrição oficial do podcast"))
            return True
        if not episode.transcript_url or episode.id in self._rss_attempted:
            return False

        self._rss_attempted.add(episode.id)
        self._set_state(episode.id, "loading", _("Carregando transcrição do feed…"))

        def worker() -> list[TranscriptSegment]:
            data = http.get_bytes(episode.transcript_url, timeout=20.0,
                                  max_bytes=12 * 1024 * 1024)
            return parse_transcript(data, episode.transcript_type, episode.transcript_url,
                                    duration=float(episode.duration or 0), source="rss")

        def done(segments: list[TranscriptSegment]) -> None:
            if segments:
                self.db.save_transcript_segments(episode.id, segments, replace=True)
                self._set_state(episode.id, "ready", _("Transcrição oficial do podcast"))
                self.emit("segments-updated", episode.id)
            else:
                self._set_state(episode.id, "idle", "")

        def failed(error: BaseException) -> None:
            log.info("Não foi possível baixar transcrição RSS (%s): %s", episode.title, error)
            self._set_state(episode.id, "idle", "")

        tasks.run_async(worker, on_done=done, on_error=failed, pool=self._pool)
        return True

    # -- Live progressive OpenRouter transcription (Layer 3) -------------------------

    def start_live(self, episode: Episode, position: float | None = None) -> bool:
        """Activate progressive look-ahead AI transcription for ``episode``."""
        if not self.ai.configured:
            self._set_state(episode.id, "error",
                            _("Configure sua chave do OpenRouter nas Preferências"))
            return False
        if not episode.audio_url and not episode.is_downloaded:
            self._set_state(episode.id, "error", _("Este episódio não tem áudio disponível"))
            return False
        if self.db.transcript_source(episode.id) == "rss":
            self._set_state(episode.id, "ready", _("Transcrição oficial do podcast"))
            return True

        self._live_episodes.add(episode.id)
        self._active_episode = episode
        pos = float(position if position is not None else (
            self.playback.position if self.playback and self.playback.current
            and self.playback.current.id == episode.id else episode.position
        ))
        self._schedule_window(episode, pos, reset_cancel=True)
        return True

    def stop_live(self, episode_id: str | None = None) -> None:
        if episode_id is None:
            self._live_episodes.clear()
        else:
            self._live_episodes.discard(episode_id)
        with self._lock:
            self._active_generation += 1
            if self._active_cancel is not None:
                self._active_cancel.set()
                self._active_cancel = None
        if episode_id:
            if self.db.has_transcript(episode_id):
                self._set_state(episode_id, "ready", _("Transcrição em pausa"))
            else:
                self._set_state(episode_id, "idle", "")

    def _audio_uri(self, episode: Episode) -> str:
        if episode.is_downloaded and Path(episode.download_path).is_file():
            return Gst.filename_to_uri(episode.download_path)
        return episode.audio_url

    def _schedule_window(self, episode: Episode, position: float, *,
                         reset_cancel: bool = False) -> None:
        if episode.id not in self._live_episodes or not self.ai.configured:
            return
        duration = float(episode.duration or 0.0)
        if self.playback is not None and self.playback.current and self.playback.current.id == episode.id:
            duration = max(duration, float(self.playback.duration or 0.0))

        chunk_idx = max(0, int(position // CHUNK_SECONDS))
        pos_in_chunk = position - chunk_idx * CHUNK_SECONDS
        done_chunks = self.db.list_transcribed_chunks(episode.id)
        wanted: list[int] = []
        # Always ensure current chunk; prefetch next chunk only after 45% through current one
        # so pausing AI quickly never bills an unnecessary future slice.
        lookahead = LOOKAHEAD_CHUNKS if pos_in_chunk >= CHUNK_SECONDS * 0.45 else 0
        for idx in range(chunk_idx, chunk_idx + lookahead + 1):
            start_t = idx * CHUNK_SECONDS
            if duration > 0 and start_t >= duration - 0.5:
                break
            if idx not in done_chunks:
                wanted.append(idx)

        if not wanted:
            self._set_state(episode.id, "ready", _("OpenRouter · Ao vivo"))
            return

        with self._lock:
            if reset_cancel:
                self._active_generation += 1
                if self._active_cancel is not None:
                    self._active_cancel.set()
                self._active_cancel = threading.Event()
            elif self._active_cancel is None:
                self._active_cancel = threading.Event()
            cancel = self._active_cancel
            generation = self._active_generation

            to_submit: list[int] = []
            for idx in wanted:
                key = (episode.id, idx)
                if key not in self._in_flight_chunks:
                    self._in_flight_chunks.add(key)
                    to_submit.append(idx)

        if not to_submit:
            return

        self._set_state(episode.id, "transcribing", _("Transcrevendo áudio ao vivo…"))
        self._pool.submit(self._transcribe_chunks_worker, episode, to_submit, duration,
                          cancel, generation)

    def _transcribe_chunks_worker(self, episode: Episode, indices: list[int],
                                  duration: float, cancel: threading.Event,
                                  generation: int) -> None:
        uri = self._audio_uri(episode)
        api_key = self.ai.get_api_key()
        if not api_key:
            with self._lock:
                for idx in indices:
                    self._in_flight_chunks.discard((episode.id, idx))
            self._set_state(episode.id, "error", _("Chave do OpenRouter não encontrada"))
            return

        model = self.ai.stt_model
        language = self.ai.source_language
        had_error = False

        for idx in indices:
            try:
                if cancel.is_set() or generation != self._active_generation:
                    continue
                if idx in self.db.list_transcribed_chunks(episode.id):
                    continue

                chunk_start = idx * CHUNK_SECONDS
                extract_start = max(0.0, chunk_start - (OVERLAP_SECONDS if idx > 0 else 0.0))
                chunk_end = chunk_start + CHUNK_SECONDS
                if duration > 0:
                    chunk_end = min(chunk_end, duration)
                if chunk_end <= extract_start + 0.3:
                    continue

                wav_bytes = extract_wav_chunk(uri, extract_start, chunk_end, cancel=cancel)
                if cancel.is_set() or generation != self._active_generation:
                    continue
                if not wav_bytes:
                    self.db.mark_chunk_transcribed(episode.id, idx, chunk_start, chunk_end,
                                                   "openrouter")
                    continue

                existing = self.db.list_transcript_segments(episode.id)
                prompt = self._prompt_before(existing, extract_start)

                result = openrouter.transcribe_audio(
                    api_key,
                    wav_bytes,
                    model=model,
                    audio_format="wav",
                    offset_seconds=extract_start,
                    language=language,
                    prompt=prompt,
                )
                if cancel.is_set() or generation != self._active_generation:
                    continue

                merged = _dedupe_segments(existing, result.segments)
                if merged:
                    self.db.save_transcript_segments(episode.id, merged, replace=False)
                self.db.mark_chunk_transcribed(episode.id, idx, chunk_start, chunk_end,
                                               "openrouter")
                self.ai.record_usage(
                    result.cost_usd,
                    result.duration_seconds or (chunk_end - extract_start),
                )
                tasks.idle(self.emit, "segments-updated", episode.id)
            except AudioExtractionCancelled:
                break
            except (AudioExtractionError, openrouter.OpenRouterError) as error:
                had_error = True
                log.warning("Transcrição falhou (%s bloco %d): %s", episode.title, idx, error)
                self._set_state(episode.id, "error", str(error))
                # Stop live loop on fatal billing/auth errors so we don't spam requests
                if isinstance(error, openrouter.OpenRouterError) and error.status in (401, 402, 404):
                    self._live_episodes.discard(episode.id)
                    break
            except Exception as error:
                had_error = True
                log.exception("Erro inesperado na transcrição")
                self._set_state(episode.id, "error", str(error))
            finally:
                with self._lock:
                    self._in_flight_chunks.discard((episode.id, idx))

        if not had_error and episode.id in self._live_episodes and generation == self._active_generation:
            self._set_state(episode.id, "ready", _("OpenRouter · Ao vivo"))

    @staticmethod
    def _prompt_before(segments: list[TranscriptSegment], timestamp: float) -> str:
        prior = [s.text for s in segments if s.end <= timestamp + 2.0]
        if not prior:
            return ""
        tail = " ".join(prior[-2:])
        return tail[-200:]

    # -- Playback hooks --------------------------------------------------------------

    def _on_episode_changed(self, _playback, episode: Episode | None) -> None:
        if self._active_episode is not None and (episode is None or episode.id != self._active_episode.id):
            with self._lock:
                self._active_generation += 1
                if self._active_cancel is not None:
                    self._active_cancel.set()
                    self._active_cancel = None
        self._active_episode = episode
        if episode is None:
            return
        has_free = self.ensure_free_transcript(episode)
        if not has_free and self.ai.configured and self.ai.auto_transcribe:
            self.start_live(episode, episode.position)

    def _on_position_changed(self, _playback, position: float, _duration: float) -> None:
        episode = self.playback.current if self.playback else None
        if episode is None or episode.id not in self._live_episodes:
            return
        self._schedule_window(episode, position, reset_cancel=False)

    def _on_seeked(self, _playback, position: float) -> None:
        episode = self.playback.current if self.playback else None
        if episode is None or episode.id not in self._live_episodes:
            return
        self._schedule_window(episode, position, reset_cancel=True)

    # -- Study Mode: Bilingual Translation & Contextual Breakdown --------------------

    def translate_missing_segments(self, episode_id: str, *,
                                   around_time: float = 0.0,
                                   on_error: Callable[[BaseException], None] | None = None) -> None:
        """Translate untranslated segments near ``around_time`` in batches and cache them."""
        if not self.ai.configured:
            if on_error:
                on_error(openrouter.OpenRouterError(
                    _("Configure sua chave do OpenRouter nas Preferências para traduzir")))
            return
        with self._lock:
            if episode_id in self._translating:
                return
            self._translating.add(episode_id)

        segments = self.db.list_transcript_segments(episode_id)
        missing = [s for s in segments if not s.translation.strip()]
        if not missing:
            with self._lock:
                self._translating.discard(episode_id)
            return

        # Prioritize segments starting at/near the user's current listening position.
        missing.sort(key=lambda s: (0 if s.end >= around_time - 5.0 else 1, abs(s.start - around_time)))
        batch = missing[:TRANSLATION_BATCH]
        model = self.ai.study_model
        target_lang = self.ai.target_language

        def worker() -> dict[float, str]:
            api_key = self.ai.get_api_key()
            if not api_key:
                raise openrouter.OpenRouterError(_("Chave do OpenRouter não encontrada"))
            texts = [s.text for s in batch]
            translated, cost = openrouter.translate_segments(
                api_key, texts, model=model, target_lang=target_lang)
            self.ai.record_usage(cost)
            return {seg.start: tr for seg, tr in zip(batch, translated) if tr}

        def done(mapping: dict[float, str]) -> None:
            with self._lock:
                self._translating.discard(episode_id)
            if mapping:
                self.db.update_segment_translations(episode_id, mapping)
                self.emit("translation-updated", episode_id)

        def failed(error: BaseException) -> None:
            with self._lock:
                self._translating.discard(episode_id)
            log.warning("Tradução bilíngue falhou: %s", error)
            if on_error:
                on_error(error)

        tasks.run_async(worker, on_done=done, on_error=failed, pool=self._study_pool)

    def study_segment(self, episode_id: str, segment: TranscriptSegment,
                      context: str = "", *,
                      on_done: Callable[[StudyNote], None],
                      on_error: Callable[[BaseException], None]) -> None:
        """Fetch or generate a cached AI study explanation for a single transcript segment."""
        note_id = make_study_note_id(episode_id, segment.text)
        cached = self.db.get_study_note(note_id)
        if cached is not None and cached.explanation:
            on_done(cached)
            return

        if not self.ai.configured:
            on_error(openrouter.OpenRouterError(
                _("Configure sua chave do OpenRouter nas Preferências para estudar expressões")))
            return

        model = self.ai.study_model
        target_lang = self.ai.target_language

        def worker() -> StudyNote:
            api_key = self.ai.get_api_key()
            if not api_key:
                raise openrouter.OpenRouterError(_("Chave do OpenRouter não encontrada"))
            study, cost = openrouter.explain_phrase(
                api_key, segment.text, context=context, model=model, target_lang=target_lang)
            self.ai.record_usage(cost)
            note = StudyNote(
                id=note_id,
                episode_id=episode_id,
                text=segment.text,
                start=segment.start,
                end=segment.end,
                translation=study.translation or segment.translation,
                explanation=study.to_markdown(),
                saved=cached.saved if cached is not None else False,
                created_at=int(time.time()),
            )
            self.db.save_study_note(note)
            if note.translation and not segment.translation:
                self.db.update_segment_translations(episode_id, {segment.start: note.translation})
            return note

        tasks.run_async(worker, on_done=on_done, on_error=on_error, pool=self._study_pool)

    def toggle_save_note(self, note: StudyNote) -> StudyNote:
        updated = StudyNote(
            id=note.id,
            episode_id=note.episode_id,
            text=note.text,
            start=note.start,
            end=note.end,
            translation=note.translation,
            explanation=note.explanation,
            saved=not note.saved,
            created_at=note.created_at or int(time.time()),
        )
        self.db.save_study_note(updated)
        return updated

    def shutdown(self) -> None:
        self.stop_live()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._study_pool.shutdown(wait=False, cancel_futures=True)

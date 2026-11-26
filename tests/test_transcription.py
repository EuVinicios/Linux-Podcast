"""Tests for RSS transcripts, parsers, GStreamer chunking, OpenRouter BYOK, and study mode."""

import json
import math
import sqlite3
import struct
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import tests  # noqa: F401
from gi.repository import Gst

from src import config
from src.api import openrouter
from src.api.rss_parser import parse_feed
from src.api.transcript_parser import parse_json_transcript, parse_transcript, parse_vtt_or_srt
from src.core.ai import AIManager
from src.core.audio_chunks import extract_wav_chunk
from src.core.database import Database
from src.core.secrets import FILE, CredentialStore
from src.core.transcription import TranscriptionManager
from src.models import Episode, Podcast, TranscriptSegment, make_episode_id
from tests.helpers import run_until

API_KEY = "sk-or-v1-test-key-123456"

SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:04.500
<v Alice>Welcome back to the English podcast!</v>

00:00:05.000 --> 00:00:09.250
Today we are going to break down phrasal verbs.
"""

SAMPLE_SRT = """1
00:00:00,500 --> 00:00:03,200
Hello <i>everyone</i>!

2
00:00:03,500 --> 00:00:07,000
Host: Let's get started right away.
"""


def _make_wav(path: Path, duration_sec: float = 2.0, sample_rate: int = 16000) -> Path:
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            sample = int(12000 * math.sin(2 * math.pi * 440.0 * i / sample_rate))
            frames.extend(struct.pack("<h", sample))
        wf.writeframes(bytes(frames))
    return path


class FakeOpenRouterServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeOpenRouterHandler)
        self.stt_calls: list[dict] = []
        self.chat_calls: list[dict] = []
        self.reject_word_granularity = False

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class FakeOpenRouterHandler(BaseHTTPRequestHandler):
    server: FakeOpenRouterServer

    def log_message(self, *_args):
        pass

    def _send(self, status: int, payload=None, raw: bytes | None = None,
              content_type: str = "application/json") -> None:
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        if self.path == "/transcript.vtt":
            return self._send(200, raw=SAMPLE_VTT.encode("utf-8"), content_type="text/vtt")
        if self.path == "/auth/key":
            if self.headers.get("Authorization") != f"Bearer {API_KEY}":
                return self._send(401, {"error": {"message": "Invalid API key"}})
            return self._send(200, {
                "data": {
                    "label": "sk-or-v1-test...456",
                    "usage": 0.42,
                    "limit": 10.0,
                    "limit_remaining": 9.58,
                    "is_free_tier": False,
                }
            })
        return self._send(404, {"error": {"message": "Not found"}})

    def do_POST(self):
        body = self._json_body()
        if self.path == "/auth/keys":
            if body.get("code") == "auth_code_ok" and body.get("code_verifier"):
                return self._send(200, {"key": API_KEY})
            return self._send(400, {"error": {"message": "Bad PKCE code"}})

        if self.headers.get("Authorization") != f"Bearer {API_KEY}":
            return self._send(401, {"error": {"message": "Invalid API key"}})

        if self.path == "/audio/transcriptions":
            self.server.stt_calls.append(body)
            granularities = body.get("timestamp_granularities") or []
            if self.server.reject_word_granularity and "word" in granularities:
                return self._send(400, {"error": {"message": "word timestamps unsupported"}})
            return self._send(200, {
                "language": "en",
                "duration": 2.0,
                "text": "Let's break down this phrasal verb.",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.8,
                        "text": "Let's break down this phrasal verb.",
                        "words": [
                            {"word": "Let's", "start": 0.0, "end": 0.3},
                            {"word": "break", "start": 0.3, "end": 0.6},
                            {"word": "down", "start": 0.6, "end": 0.9},
                            {"word": "this", "start": 0.9, "end": 1.1},
                            {"word": "phrasal", "start": 1.1, "end": 1.5},
                            {"word": "verb.", "start": 1.5, "end": 1.8},
                        ],
                    }
                ],
                "usage": {"seconds": 2.0, "cost": 0.0002},
            })

        if self.path == "/chat/completions":
            self.server.chat_calls.append(body)
            user_msg = body["messages"][-1]["content"]
            if '"items"' in user_msg:
                req = json.loads(user_msg)
                translations = [f"PT: {item['t']}" for item in req["items"]]
                content = json.dumps({"translations": translations}, ensure_ascii=False)
            else:
                content = json.dumps({
                    "translation": "Vamos analisar esse phrasal verb.",
                    "summary": "Usado ao explicar algo em partes menores.",
                    "vocabulary": [
                        {"term": "break down", "meaning": "analisar em partes",
                         "note": "phrasal verb separável"},
                    ],
                    "grammar_tip": "Break down muda de sentido conforme o contexto.",
                }, ensure_ascii=False)
            return self._send(200, {
                "choices": [{"message": {"content": content}}],
                "usage": {"cost": 0.0001},
            })

        return self._send(404, {"error": {"message": "Unknown path"}})


class TranscriptParserTests(unittest.TestCase):
    def test_vtt_and_srt_parsing(self):
        vtt = parse_vtt_or_srt(SAMPLE_VTT)
        self.assertEqual(len(vtt), 2)
        self.assertEqual(vtt[0].speaker, "Alice")
        self.assertEqual(vtt[0].text, "Welcome back to the English podcast!")
        self.assertAlmostEqual(vtt[0].start, 1.0)
        self.assertAlmostEqual(vtt[0].end, 4.5)

        srt = parse_transcript(SAMPLE_SRT.encode("utf-8"), "application/x-subrip")
        self.assertEqual(len(srt), 2)
        self.assertEqual(srt[0].text, "Hello everyone!")
        self.assertEqual(srt[1].speaker, "Host")
        self.assertEqual(srt[1].text, "Let's get started right away.")

    def test_json_podcasting_2_and_verbose_whisper(self):
        p2 = {
            "version": "1.0.0",
            "segments": [
                {"startTime": 1.5, "endTime": 4.0, "body": "First line.", "speaker": "Bob"},
            ],
        }
        segs = parse_json_transcript(p2)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].speaker, "Bob")
        self.assertAlmostEqual(segs[0].start, 1.5)

        whisper = {
            "segments": [{"start": 0.5, "end": 2.0, "text": "Good morning."}],
            "words": [
                {"word": "Good", "start": 0.5, "end": 1.0},
                {"word": "morning.", "start": 1.0, "end": 2.0},
            ],
        }
        wsegs = parse_json_transcript(whisper, offset=40.0, source="openrouter")
        self.assertEqual(len(wsegs), 1)
        self.assertAlmostEqual(wsegs[0].start, 40.5)
        self.assertEqual([w.word for w in wsegs[0].words], ["Good", "morning."])
        self.assertAlmostEqual(wsegs[0].words[1].end, 42.0)

    def test_rss_prefers_timed_podcast_transcript(self):
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
          <channel>
            <title>English Daily</title>
            <item>
              <title>Lesson 1</title>
              <guid>l1</guid>
              <enclosure url="https://ex.com/l1.mp3" type="audio/mpeg" length="1000"/>
              <podcast:transcript url="https://ex.com/l1.txt" type="text/plain"/>
              <podcast:transcript url="https://ex.com/l1.vtt" type="text/vtt" rel="captions"/>
            </item>
          </channel>
        </rss>"""
        parsed = parse_feed(xml, "https://ex.com/feed.xml")
        ep = parsed.episodes[0]
        self.assertEqual(ep.transcript_url, "https://ex.com/l1.vtt")
        self.assertEqual(ep.transcript_type, "text/vtt")


class AudioChunkExtractionTests(unittest.TestCase):
    def test_extracts_16khz_mono_wav_slice(self):
        tmp = Path(tempfile.mkdtemp(prefix="podflow-wav-"))
        wav_path = _make_wav(tmp / "tone.wav", duration_sec=2.0)
        uri = Gst.filename_to_uri(str(wav_path))
        chunk = extract_wav_chunk(uri, 0.4, 1.4)
        self.assertGreater(len(chunk), 44)
        self.assertEqual(chunk[:4], b"RIFF")
        self.assertEqual(chunk[8:12], b"WAVE")
        # 1 second of 16kHz 16-bit mono PCM is ~32000 bytes
        self.assertGreater(len(chunk), 24000)


class OpenRouterAndTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeOpenRouterServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.patches = [
            mock.patch.object(config, "OFFLINE", False),
            mock.patch.object(openrouter, "BASE_URL", self.server.base),
        ]
        for p in self.patches:
            p.start()
        self.tmp = Path(tempfile.mkdtemp(prefix="podflow-ai-"))
        self.db = Database(":memory:", seed=False)
        self.store = CredentialStore(self.tmp / "credentials.json")
        self.ai = AIManager(self.db, store=self.store)
        self.manager = TranscriptionManager(self.db, self.ai)

        self.db.upsert_podcast(Podcast(id="feed:en", title="English Show",
                                       feed_url="https://en.example/feed"))
        self.wav_path = _make_wav(self.tmp / "ep.wav", duration_sec=2.0)
        self.ep_rss = Episode(
            id=make_episode_id("feed:en", "rss-1"),
            podcast_id="feed:en",
            guid="rss-1",
            title="Free RSS Transcript Ep",
            audio_url="https://en.example/rss1.mp3",
            transcript_url=self.server.base + "/transcript.vtt",
            transcript_type="text/vtt",
            duration=30,
        )
        self.ep_ai = Episode(
            id=make_episode_id("feed:en", "ai-1"),
            podcast_id="feed:en",
            guid="ai-1",
            title="Live AI Ep",
            audio_url="https://en.example/ai1.mp3",
            duration=2,
            download_state="done",
            download_path=str(self.wav_path),
        )
        self.db.upsert_episodes("feed:en", [self.ep_rss, self.ep_ai])
        self.db.set_download(self.ep_ai.id, "done", str(self.wav_path), self.wav_path.stat().st_size)

    def tearDown(self):
        self.manager.shutdown()
        self.db.close()
        self.server.shutdown()
        self.server.server_close()
        for p in self.patches:
            p.stop()

    def test_v2_database_migrates_to_v3(self):
        db_path = self.tmp / "v2.db"
        Database(db_path, seed=False).close()
        conn = sqlite3.connect(db_path)
        conn.execute("DROP TABLE transcript_segments")
        conn.execute("DROP TABLE transcript_chunks")
        conn.execute("DROP TABLE study_notes")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()

        upgraded = Database(db_path, seed=False)
        self.assertEqual(upgraded.count_transcribed_episodes(), 0)
        upgraded.close()

    def test_free_rss_transcript_works_without_api_key(self):
        self.assertFalse(self.ai.configured)
        updated = []
        self.manager.connect("segments-updated", lambda _m, eid: updated.append(eid))
        self.assertTrue(self.manager.ensure_free_transcript(self.ep_rss))
        self.assertTrue(run_until(lambda: self.ep_rss.id in updated, 5))
        segs = self.manager.segments_for(self.ep_rss.id)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].source, "rss")
        self.assertEqual(self.server.stt_calls, [])

    def test_pkce_loopback_and_exchange(self):
        received = {}
        srv = openrouter.PKCECallbackServer(lambda code, err: received.update(code=code, err=err))
        try:
            self.assertIn("code_challenge_method=S256", srv.auth_url)
            with urllib.request.urlopen(f"{srv.callback_url}?code=auth_code_ok", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
            self.assertTrue(run_until(lambda: "code" in received, 5))
            self.assertEqual(received["code"], "auth_code_ok")
            api_key = openrouter.exchange_pkce_code(received["code"], srv.verifier)
            self.assertEqual(api_key, API_KEY)
        finally:
            srv.stop()

    def test_byok_live_transcription_and_study_mode(self):
        # Connect BYOK key
        conn_result = {}
        self.ai.connect_with_key(
            API_KEY,
            on_done=lambda info: conn_result.setdefault("info", info),
            on_error=lambda err: conn_result.setdefault("err", err),
        )
        self.assertTrue(run_until(lambda: conn_result, 5))
        self.assertIn("info", conn_result)
        self.assertTrue(self.ai.configured)
        self.assertEqual(self.ai.storage, FILE)
        self.assertAlmostEqual(conn_result["info"].limit_remaining, 9.58)

        # Trigger progressive live transcription (also test 400 fallback for word granularity)
        self.server.reject_word_granularity = True
        updated = []
        self.manager.connect("segments-updated", lambda _m, eid: updated.append(eid))
        ep_ai = self.db.get_episode(self.ep_ai.id)
        self.assertTrue(self.manager.start_live(ep_ai, 0.0))
        self.assertTrue(run_until(lambda: self.ep_ai.id in updated, 10))

        segs = self.manager.segments_for(self.ep_ai.id)
        self.assertEqual(len(segs), 1)
        self.assertIn("phrasal verb", segs[0].text)
        self.assertEqual(len(segs[0].words), 6)
        self.assertEqual(len(self.server.stt_calls), 2)  # first 400 with word, second succeeded
        self.assertTrue(run_until(lambda: self.ai.total_cost_usd > 0, 3))

        # Bilingual batched translation
        translated = []
        self.manager.connect("translation-updated", lambda _m, eid: translated.append(eid))
        self.manager.translate_missing_segments(self.ep_ai.id, around_time=0.0)
        self.assertTrue(run_until(lambda: self.ep_ai.id in translated, 5))
        segs_after = self.manager.segments_for(self.ep_ai.id)
        self.assertTrue(segs_after[0].translation.startswith("PT:"))

        # Study breakdown + cache + save to notebook
        study_out = {}
        self.manager.study_segment(
            self.ep_ai.id, segs_after[0], segs_after[0].text,
            on_done=lambda note: study_out.setdefault("note", note),
            on_error=lambda err: study_out.setdefault("err", err),
        )
        self.assertTrue(run_until(lambda: study_out, 5))
        note = study_out["note"]
        self.assertIn("break down", note.explanation)
        saved_note = self.manager.toggle_save_note(note)
        self.assertTrue(saved_note.saved)
        self.assertEqual(len(self.db.list_saved_study_notes(self.ep_ai.id)), 1)

        # Disconnect cleanly wipes credential and disables AI without breaking cached transcript
        self.ai.sign_out()
        self.assertFalse(self.ai.configured)
        self.assertEqual(len(self.manager.segments_for(self.ep_ai.id)), 1)


if __name__ == "__main__":
    unittest.main()

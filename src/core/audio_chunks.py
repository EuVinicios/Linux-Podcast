"""Extract short 16 kHz mono WAV audio slices using GStreamer (no external tools)."""

from __future__ import annotations

import logging
import threading
import time

from gi.repository import Gst

log = logging.getLogger(__name__)


class AudioExtractionError(Exception):
    pass


class AudioExtractionCancelled(Exception):
    pass


def extract_wav_chunk(uri: str, start_sec: float, end_sec: float, *,
                      cancel: threading.Event | None = None,
                      timeout: float = 45.0) -> bytes:
    """Decode ``[start_sec, end_sec]`` from ``uri`` into 16 kHz mono 16-bit WAV bytes.

    Works on local ``file://`` URIs as well as HTTP/HTTPS podcast streams.
    Call this only from a background worker thread.
    """
    if not Gst.is_initialized():
        Gst.init(None)

    start_sec = max(0.0, float(start_sec))
    end_sec = max(start_sec + 0.5, float(end_sec))

    pipeline = Gst.Pipeline.new("podflow-chunk-extractor")
    src = Gst.ElementFactory.make("uridecodebin", "src")
    convert = Gst.ElementFactory.make("audioconvert", "convert")
    resample = Gst.ElementFactory.make("audioresample", "resample")
    capsfilter = Gst.ElementFactory.make("capsfilter", "caps")
    encoder = Gst.ElementFactory.make("wavenc", "wavenc")
    sink = Gst.ElementFactory.make("appsink", "sink")

    if not all((pipeline, src, convert, resample, capsfilter, encoder, sink)):
        raise AudioExtractionError("Elementos do GStreamer para áudio não disponíveis")

    src.set_property("uri", uri)
    caps = Gst.Caps.from_string("audio/x-raw,format=S16LE,channels=1,rate=16000")
    capsfilter.set_property("caps", caps)
    sink.set_property("sync", False)
    sink.set_property("emit-signals", False)
    sink.set_property("max-buffers", 32)
    sink.set_property("drop", False)

    for element in (src, convert, resample, capsfilter, encoder, sink):
        pipeline.add(element)

    if not (convert.link(resample) and resample.link(capsfilter)
            and capsfilter.link(encoder) and encoder.link(sink)):
        raise AudioExtractionError("Não foi possível montar o pipeline de extração")

    linked = threading.Event()

    def on_pad_added(_element, pad: Gst.Pad) -> None:
        if linked.is_set():
            return
        pad_caps = pad.get_current_caps() or pad.query_caps(None)
        if pad_caps is None or pad_caps.is_empty():
            return
        name = pad_caps.get_structure(0).get_name()
        if not name.startswith("audio/"):
            return
        sink_pad = convert.get_static_pad("sink")
        if sink_pad is not None and not sink_pad.is_linked():
            if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                linked.set()

    src.connect("pad-added", on_pad_added)
    bus = pipeline.get_bus()
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout

    try:
        ret = pipeline.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise AudioExtractionError("Não foi possível abrir o áudio para transcrição")

        # Wait for preroll so we can issue an accurate segment seek.
        prerolled = False
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise AudioExtractionCancelled()
            msg = bus.timed_pop_filtered(
                100 * Gst.MSECOND,
                Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR | Gst.MessageType.EOS,
            )
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ASYNC_DONE:
                prerolled = True
                break
            if msg.type == Gst.MessageType.ERROR:
                err, _dbg = msg.parse_error()
                raise AudioExtractionError(err.message)
            if msg.type == Gst.MessageType.EOS:
                break

        if not prerolled:
            raise AudioExtractionError("Tempo esgotado ao preparar o trecho de áudio")

        # Clamp to actual duration if available
        ok_dur, raw_dur = pipeline.query_duration(Gst.Format.TIME)
        if ok_dur and raw_dur > 0:
            dur_sec = raw_dur / Gst.SECOND
            if start_sec >= dur_sec - 0.15:
                return b""
            end_sec = min(end_sec, dur_sec)

        seek_ok = pipeline.seek(
            1.0,
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            Gst.SeekType.SET,
            int(start_sec * Gst.SECOND),
            Gst.SeekType.SET,
            int(end_sec * Gst.SECOND),
        )
        if not seek_ok and start_sec > 0.5:
            raise AudioExtractionError("Este stream não permite buscar o trecho solicitado")

        pipeline.set_state(Gst.State.PLAYING)

        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise AudioExtractionCancelled()
            sample = sink.emit("try-pull-sample", 100 * Gst.MSECOND)
            if sample is not None:
                buf = sample.get_buffer()
                if buf is not None:
                    ok_map, info = buf.map(Gst.MapFlags.READ)
                    if ok_map:
                        try:
                            chunks.append(bytes(info.data))
                        finally:
                            buf.unmap(info)
                continue

            msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg is not None:
                if msg.type == Gst.MessageType.ERROR:
                    err, _dbg = msg.parse_error()
                    raise AudioExtractionError(err.message)
                if msg.type == Gst.MessageType.EOS:
                    break

            if sink.get_property("eos"):
                break

        # Drain any final queued samples (including WAV header updates if sent)
        while True:
            sample = sink.emit("try-pull-sample", 0)
            if sample is None:
                break
            buf = sample.get_buffer()
            if buf is not None:
                ok_map, info = buf.map(Gst.MapFlags.READ)
                if ok_map:
                    try:
                        chunks.append(bytes(info.data))
                    finally:
                        buf.unmap(info)

        raw = b"".join(chunks)
        return _fix_wav_sizes(raw)
    finally:
        pipeline.set_state(Gst.State.NULL)


def _fix_wav_sizes(wav_bytes: bytes) -> bytes:
    """Ensure RIFF/data chunk sizes in streamed WAV header match actual payload length."""
    if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        return wav_bytes
    data_pos = wav_bytes.find(b"data", 12, min(len(wav_bytes), 256))
    if data_pos < 0 or data_pos + 8 > len(wav_bytes):
        return wav_bytes
    buf = bytearray(wav_bytes)
    riff_size = len(buf) - 8
    data_size = len(buf) - (data_pos + 8)
    buf[4:8] = riff_size.to_bytes(4, "little")
    buf[data_pos + 4:data_pos + 8] = data_size.to_bytes(4, "little")
    return bytes(buf)

"""Synchronized transcript & language-study panel for the Now Playing sheet."""

from __future__ import annotations

import time

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ...i18n import _
from ...models import Episode, StudyNote, TranscriptSegment
from ...utils.time_format import format_clock
from ..helpers import clear, get_app, label


def _escape(text: str) -> str:
    return GLib.markup_escape_text(text)


def _render_word_markup(segment: TranscriptSegment, position: float) -> str:
    """Smooth progressive word reveal without font-weight/underline line reflow."""
    if not segment.words:
        return _escape(segment.text)
    dark = Adw.StyleManager.get_default().get_dark()
    accent = "#c88cff" if dark else "#7224d8"
    parts: list[str] = []
    for word in segment.words:
        escaped = _escape(word.word)
        if word.start - 0.06 <= position <= word.end + 0.06:
            parts.append(f"<span foreground='{accent}'>{escaped}</span>")
        elif position > word.end + 0.06:
            parts.append(escaped)
        else:
            parts.append(f"<span alpha='48%'>{escaped}</span>")
    return " ".join(parts)


class _CueRow(Gtk.Box):
    """One clickable, study-enabled transcript cue."""

    def __init__(self, segment: TranscriptSegment, *, bilingual: bool,
                 on_seek, on_study, on_repeat):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         css_classes=["transcript-cue"])
        self.segment = segment
        self._active = False
        self._past = False
        self._last_word_key = ""

        top = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        time_lbl = label(format_clock(segment.start),
                         ("caption", "numeric", "dim-label", "transcript-time"), xalign=0.0)
        top.append(time_lbl)
        if segment.speaker:
            top.append(label(segment.speaker, ("caption-heading", "accent"),
                             ellipsize=True, max_width_chars=20))
        top.append(Gtk.Box(hexpand=True))

        self.repeat_btn = Gtk.Button(icon_name="media-playlist-repeat-symbolic",
                                     css_classes=["flat", "circular", "transcript-cue-btn"],
                                     tooltip_text=_("Repetir esta frase"),
                                     valign=Gtk.Align.CENTER)
        self.repeat_btn.connect("clicked", lambda *_: on_repeat(segment))
        top.append(self.repeat_btn)

        self.study_btn = Gtk.Button(icon_name="accessories-dictionary-symbolic",
                                    css_classes=["flat", "circular", "transcript-cue-btn"],
                                    tooltip_text=_("Estudar esta frase com IA"),
                                    valign=Gtk.Align.CENTER)
        self.study_btn.connect("clicked", lambda btn: on_study(segment, btn))
        top.append(self.study_btn)
        self.append(top)

        self.text_label = label(segment.text, ("transcript-text",), wrap=True,
                                xalign=0.0, max_width_chars=10)
        self.append(self.text_label)

        self.translation_label = label(segment.translation, ("transcript-translation", "dim-label"),
                                       wrap=True, xalign=0.0, max_width_chars=10)
        self.translation_label.set_visible(bilingual and bool(segment.translation))
        self.append(self.translation_label)

        click = Gtk.GestureClick()
        click.connect("released", lambda _g, _n, _x, _y: on_seek(segment))
        self.text_label.add_controller(click)
        self.text_label.set_cursor_from_name("pointer")

    def set_bilingual(self, enabled: bool) -> None:
        self.translation_label.set_text(self.segment.translation)
        self.translation_label.set_visible(enabled and bool(self.segment.translation))

    def set_state(self, *, active: bool, past: bool, position: float) -> None:
        if past != self._past:
            self._past = past
            if past:
                self.add_css_class("past")
            else:
                self.remove_css_class("past")
        if active != self._active:
            self._active = active
            if active:
                self.add_css_class("active")
            else:
                self.remove_css_class("active")
                self._last_word_key = ""
                self.text_label.set_text(self.segment.text)
        if active and self.segment.words:
            markup = _render_word_markup(self.segment, position)
            if markup != self._last_word_key:
                self._last_word_key = markup
                try:
                    self.text_label.set_markup(markup)
                except GLib.Error:
                    self.text_label.set_text(self.segment.text)


class TranscriptPanel(Gtk.Box):
    """Apple Podcasts-style synchronized transcript + language-study toolkit."""

    __gtype_name__ = "PodFlowTranscriptPanel"

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         vexpand=True, hexpand=True)
        app = get_app()
        self.playback = getattr(app, "playback", None)
        self.ai = getattr(app, "ai", None)
        self.transcription = getattr(app, "transcription", None)
        self.db = getattr(app, "db", None)

        self._episode: Episode | None = None
        self._segments: list[TranscriptSegment] = []
        self._rows: list[_CueRow] = []
        self._active_index = -1
        self._bilingual = bool(self.ai.bilingual_default) if self.ai else False
        self._loop_segment: TranscriptSegment | None = None
        self._auto_follow = True
        self._scroll_anim: Adw.TimedAnimation | None = None
        self._last_scroll_target: float | None = None
        self._search_query = ""
        self._view_mode = "transcript"  # "transcript" or "notebook"

        # -- Header row
        header = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        header.append(label(_("TRANSCRIÇÃO"), ("queue-section", "dim-label"), xalign=0.0))
        self.source_pill = label("", ("caption", "transcript-status-pill"), xalign=0.5)
        self.source_pill.set_visible(False)
        header.append(self.source_pill)
        header.append(Gtk.Box(hexpand=True))

        self.search_toggle = Gtk.ToggleButton(icon_name="system-search-symbolic",
                                              css_classes=["flat", "circular", "transcript-icon-btn"],
                                              tooltip_text=_("Buscar na transcrição"))
        self.search_toggle.connect("toggled", self._on_search_toggled)
        header.append(self.search_toggle)

        self.notebook_toggle = Gtk.ToggleButton(icon_name="starred-symbolic",
                                                css_classes=["flat", "circular", "transcript-icon-btn"],
                                                tooltip_text=_("Caderno de estudos"))
        self.notebook_toggle.connect("toggled", self._on_notebook_toggled)
        header.append(self.notebook_toggle)

        self.settings_btn = Gtk.Button(icon_name="emblem-system-symbolic",
                                       css_classes=["flat", "circular", "transcript-icon-btn"],
                                       action_name="app.ai-preferences",
                                       tooltip_text=_("Configurações de IA e OpenRouter"))
        header.append(self.settings_btn)
        self.append(header)

        # -- Study toolbar
        toolbar = Gtk.Box(spacing=6, css_classes=["transcript-toolbar"])
        self.live_btn = Gtk.Button(css_classes=["pill", "transcript-action-pill"],
                                   valign=Gtk.Align.CENTER)
        self.live_btn.connect("clicked", self._on_live_clicked)
        toolbar.append(self.live_btn)

        self.repeat_btn = Gtk.Button(icon_name="media-skip-backward-symbolic",
                                     css_classes=["flat", "circular", "transcript-icon-btn"],
                                     tooltip_text=_("Repetir frase atual"))
        self.repeat_btn.connect("clicked", self._on_repeat_current)
        toolbar.append(self.repeat_btn)

        self.loop_btn = Gtk.ToggleButton(icon_name="media-playlist-repeat-symbolic",
                                         css_classes=["flat", "circular", "transcript-icon-btn"],
                                         tooltip_text=_("Repetir frase atual em loop"))
        self.loop_btn.connect("toggled", self._on_loop_toggled)
        toolbar.append(self.loop_btn)

        self.bilingual_btn = Gtk.ToggleButton(label=_("Bilíngue"),
                                              active=self._bilingual,
                                              css_classes=["flat", "pill", "transcript-action-pill"],
                                              tooltip_text=_("Mostrar tradução abaixo das frases"))
        self.bilingual_btn.connect("toggled", self._on_bilingual_toggled)
        toolbar.append(self.bilingual_btn)
        self.append(toolbar)

        # -- Search bar
        self.search_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Filtrar palavras ou frases…"),
                                            hexpand=True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_revealer.set_child(self.search_entry)
        self.append(self.search_revealer)

        # -- Body stack (transcript vs study notebook)
        self.body_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                    vexpand=True, hexpand=True,
                                    hhomogeneous=True, vhomogeneous=True)

        # Transcript list inside an overlay (so we can show "Jump back to live audio" pill)
        overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        self.cues_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                                css_classes=["transcript-list"])
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                           propagate_natural_width=False,
                                           propagate_natural_height=False,
                                           child=self.cues_box, vexpand=True)
        scroll_ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_ctrl.connect("scroll", self._on_user_scrolled)
        self.scroller.add_controller(scroll_ctrl)
        overlay.set_child(self.scroller)

        self.follow_btn = Gtk.Button(label=_("Acompanhar áudio"),
                                     halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                                     visible=False,
                                     css_classes=["pill", "suggested-action", "transcript-follow-btn"])
        self.follow_btn.connect("clicked", self._on_resume_follow)
        overlay.add_overlay(self.follow_btn)
        self.body_stack.add_named(overlay, "transcript")

        # Study notebook view
        self.notebook_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                                    css_classes=["transcript-list"])
        notebook_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                             vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                             propagate_natural_width=False,
                                             propagate_natural_height=False,
                                             child=self.notebook_box, vexpand=True)
        self.body_stack.add_named(notebook_scroll, "notebook")
        self.append(self.body_stack)

        # -- Signals
        if self.transcription is not None:
            self.transcription.connect("segments-updated", self._on_segments_updated)
            self.transcription.connect("state-changed", self._on_state_changed)
            self.transcription.connect("translation-updated", self._on_translation_updated)
        if self.ai is not None:
            self.ai.connect("changed", lambda *_: self._sync_controls())
        if self.db is not None:
            self.db.connect("study-notes-changed", lambda *_: self._rebuild_notebook())

        self._sync_controls()

    # -- public API called by NowPlayingView -----------------------------------------

    def set_episode(self, episode: Episode | None) -> None:
        self._episode = episode
        self._loop_segment = None
        self.loop_btn.set_active(False)
        self._active_index = -1
        self._auto_follow = True
        self.follow_btn.set_visible(False)
        if episode is not None and self.transcription is not None:
            self.transcription.ensure_free_transcript(episode)
        self._reload_segments()
        self._rebuild_notebook()
        self._sync_controls()

    def on_panel_opened(self) -> None:
        """Called when the user opens the Transcript tab in Now Playing."""
        if self._episode is None or self.transcription is None:
            return
        has_free = self.transcription.ensure_free_transcript(self._episode)
        if not has_free and self.ai.configured and self.ai.auto_transcribe:
            self.transcription.start_live(self._episode, self.playback.position)
        if self._bilingual and self._segments:
            self._maybe_request_translations()
        self._sync_controls()
        if self._auto_follow:
            GLib.timeout_add(120, self._scroll_active_deferred)

    def update_position(self, position: float) -> None:
        if self._episode is None or not self._segments:
            return

        # Handle sentence loop mode for intensive listening practice
        if self._loop_segment is not None:
            if position >= self._loop_segment.end + 0.15 or position < self._loop_segment.start - 1.5:
                self.playback.seek(self._loop_segment.start)
                return

        idx = self._find_segment_index(position)
        if idx != self._active_index:
            self._active_index = idx
            for i, row in enumerate(self._rows):
                row.set_state(active=(i == idx), past=(idx >= 0 and i < idx), position=position)
            if 0 <= idx < len(self._rows):
                if self._auto_follow:
                    self._scroll_to_row(self._rows[idx], position=position, force=True)
                if self._bilingual:
                    self._maybe_request_translations(position)
        elif 0 <= idx < len(self._rows):
            row = self._rows[idx]
            row.set_state(active=True, past=False, position=position)
            if self._auto_follow:
                self._scroll_to_row(row, position=position, force=False)

    # -- rendering -------------------------------------------------------------------

    def _find_segment_index(self, position: float) -> int:
        # Segments are sorted by start time; binary search for the active cue.
        lo, hi = 0, len(self._segments) - 1
        found = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            seg = self._segments[mid]
            if seg.start <= position + 0.15:
                found = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if found >= 0 and position <= self._segments[found].end + 2.0:
            return found
        return found

    def _reload_segments(self) -> None:
        if self._episode is None or self.transcription is None:
            clear(self.cues_box)
            self._rows = []
            self._segments = []
            self._active_index = -1
            return

        all_segments = self.transcription.segments_for(self._episode.id)
        query = self._search_query.strip().lower()
        if query:
            filtered = [
                s for s in all_segments
                if query in s.text.lower() or query in s.translation.lower()
                or query in s.speaker.lower()
            ]
        else:
            filtered = all_segments

        # Incremental append when new chunks arrive at the end (no flicker, no scroll reset!)
        if (self._rows and len(filtered) >= len(self._segments)
                and all(abs(a.start - b.start) < 1e-3 and a.text == b.text
                        for a, b in zip(self._segments, filtered))):
            for seg, row in zip(filtered, self._rows):
                if seg.translation != row.segment.translation:
                    row.segment.translation = seg.translation
                    row.set_bilingual(self._bilingual)
            new_items = filtered[len(self._segments):]
            if new_items:
                self._segments = filtered
                for seg in new_items:
                    row = _CueRow(
                        seg,
                        bilingual=self._bilingual,
                        on_seek=self._on_seek_segment,
                        on_study=self._on_study_segment,
                        on_repeat=self._on_repeat_segment,
                    )
                    self._rows.append(row)
                    self.cues_box.append(row)
                self.update_position(self.playback.position)
            return

        clear(self.cues_box)
        self._rows = []
        self._active_index = -1
        self._segments = filtered

        if not self._segments:
            self._build_empty_state(bool(query))
            return

        for seg in self._segments:
            row = _CueRow(
                seg,
                bilingual=self._bilingual,
                on_seek=self._on_seek_segment,
                on_study=self._on_study_segment,
                on_repeat=self._on_repeat_segment,
            )
            self._rows.append(row)
            self.cues_box.append(row)

        self.update_position(self.playback.position)

    def _build_empty_state(self, is_search: bool) -> None:
        if is_search:
            empty = Adw.StatusPage(
                icon_name="system-search-symbolic",
                title=_("Nenhum trecho encontrado"),
                description=_("Tente buscar outra palavra da transcrição."),
                css_classes=["compact"], vexpand=True)
            self.cues_box.append(empty)
            return

        state, detail = (self.transcription.state_for(self._episode.id)
                         if self._episode and self.transcription else ("idle", ""))
        if state in ("loading", "transcribing"):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                          vexpand=True, css_classes=["transcript-empty"])
            box.append(Adw.Spinner(width_request=32, height_request=32))
            box.append(label(detail or _("Transcrevendo áudio…"), ("title-4",), xalign=0.5, wrap=True))
            box.append(label(_("Os primeiros trechos aparecem em poucos segundos."),
                             ("dim-label", "caption"), xalign=0.5, wrap=True))
            self.cues_box.append(box)
            return

        if state == "error" and detail:
            empty = Adw.StatusPage(
                icon_name="dialog-warning-symbolic",
                title=_("Não foi possível transcrever"),
                description=detail,
                css_classes=["compact"], vexpand=True)
            self.cues_box.append(empty)
            return

        if self.ai is not None and self.ai.configured:
            desc = _("Clique em “Transcrever ao vivo” para acompanhar o áudio com texto "
                     "sincronizado usando sua chave do OpenRouter.")
        else:
            desc = _("Este episódio não traz transcrição no feed RSS. Conecte sua chave do "
                     "OpenRouter nas Preferências para transcrever ao vivo e estudar idiomas.")
        empty = Adw.StatusPage(
            icon_name="accessories-dictionary-symbolic",
            title=_("Transcrição indisponível"),
            description=desc,
            css_classes=["compact"], vexpand=True)
        if self.ai is not None and not self.ai.configured:
            btn = Gtk.Button(label=_("Configurar OpenRouter"), halign=Gtk.Align.CENTER,
                             css_classes=["pill", "suggested-action"],
                             action_name="app.ai-preferences")
            empty.set_child(btn)
        self.cues_box.append(empty)

    def _sync_controls(self) -> None:
        if self._episode is None or self.transcription is None:
            self.live_btn.set_visible(False)
            self.source_pill.set_visible(False)
            return

        ep_id = self._episode.id
        source = self.transcription.source_for(ep_id)
        state, _detail = self.transcription.state_for(ep_id)
        live_on = self.transcription.is_live_active(ep_id)

        if source == "rss":
            self.source_pill.set_text(_("RSS Grátis"))
            self.source_pill.set_tooltip_text(_("Transcrição oficial incluída no feed RSS"))
            self.source_pill.set_visible(True)
            self.live_btn.set_visible(False)
        elif live_on or state == "transcribing":
            self.source_pill.set_text(_("Transcrevendo…") if state == "transcribing" else _("Ao vivo"))
            self.source_pill.set_tooltip_text(_("Transcrição progressiva ativa via OpenRouter"))
            self.source_pill.set_visible(True)
            self.live_btn.set_visible(True)
            self.live_btn.set_label(_("Pausar IA"))
            self.live_btn.remove_css_class("suggested-action")
        elif self.ai is not None and self.ai.configured:
            if source == "openrouter":
                self.source_pill.set_text(_("IA pausada"))
                self.source_pill.set_tooltip_text(
                    _("Nenhuma nova chamada à API está sendo feita. O texto já em cache "
                      "continua sincronizado até o fim do trecho transcrito."))
                self.source_pill.set_visible(True)
            else:
                self.source_pill.set_visible(False)
            self.live_btn.set_visible(True)
            self.live_btn.set_label(_("Continuar ao vivo") if source == "openrouter"
                                    else _("Transcrever ao vivo"))
            self.live_btn.add_css_class("suggested-action")
        else:
            self.source_pill.set_visible(False)
            self.live_btn.set_visible(True)
            self.live_btn.set_label(_("Conectar OpenRouter"))
            self.live_btn.add_css_class("suggested-action")

        has_cues = bool(self._segments)
        self.repeat_btn.set_sensitive(has_cues)
        self.loop_btn.set_sensitive(has_cues)
        self.bilingual_btn.set_sensitive(has_cues)

    # -- scrolling & follow ----------------------------------------------------------

    def _scroll_active_deferred(self) -> bool:
        if self._auto_follow and 0 <= self._active_index < len(self._rows):
            pos = self.playback.position if self.playback else 0.0
            self._scroll_to_row(self._rows[self._active_index], position=pos, force=True)
        return GLib.SOURCE_REMOVE

    def _intra_row_y_offset(self, row: _CueRow, position: float) -> float:
        """Estimate vertical pixel offset of the currently spoken word inside a tall cue."""
        seg = row.segment
        if seg.words:
            # Find active word byte offset in the plain label text for exact Pango layout Y.
            byte_offset = 0
            found = False
            for idx, word in enumerate(seg.words):
                if position <= word.end + 0.08:
                    found = True
                    break
                byte_offset += len(word.word.encode("utf-8")) + 1
            if not found:
                byte_offset = max(0, len(" ".join(w.word for w in seg.words).encode("utf-8")) - 1)
            layout = row.text_label.get_layout()
            if layout is not None:
                text_len = len(layout.get_text().encode("utf-8"))
                pos_rect = layout.index_to_pos(min(byte_offset, max(0, text_len - 1)))
                word_y = max(0.0, pos_rect.y / Pango.SCALE)
                ok_lbl, lbl_rect = row.text_label.compute_bounds(row)
                top_pad = lbl_rect.get_y() if ok_lbl else 24.0
                return top_pad + word_y
        # Proportional fallback when there are no word timestamps
        span = max(0.5, seg.end - seg.start)
        frac = max(0.0, min(1.0, (position - seg.start) / span))
        ok_row, row_rect = row.compute_bounds(self.cues_box)
        return (row_rect.get_height() * frac) if ok_row else 0.0

    def _scroll_to_row(self, row: _CueRow, *, position: float = 0.0, force: bool = True) -> None:
        if not row.get_mapped() or not self.cues_box.get_mapped():
            if force:
                GLib.timeout_add(100, self._scroll_active_deferred)
            return
        ok, rect = row.compute_bounds(self.cues_box)
        if not ok:
            return
        vadj = self.scroller.get_vadjustment()
        page = vadj.get_page_size()
        if page <= 0:
            if force:
                GLib.timeout_add(100, self._scroll_active_deferred)
            return

        # If the cue is taller than half the viewport, track the active line inside the cue!
        if rect.get_height() > page * 0.55:
            intra_y = self._intra_row_y_offset(row, position)
            focus_y = rect.get_y() + intra_y
            target = max(0.0, focus_y - page * 0.34)
        else:
            if not force:
                return
            target = max(0.0, rect.get_y() - page * 0.28)

        target = min(target, max(0.0, vadj.get_upper() - page))
        current = vadj.get_value()
        min_delta = 2.0 if force else 26.0
        if abs(target - current) < min_delta:
            return
        if not force and self._last_scroll_target is not None and abs(target - self._last_scroll_target) < 22.0:
            return
        self._last_scroll_target = target
        if self._scroll_anim is not None:
            self._scroll_anim.pause()
            self._scroll_anim = None
        anim_target = Adw.PropertyAnimationTarget.new(vadj, "value")
        anim = Adw.TimedAnimation.new(self.scroller, current, target, 320, anim_target)
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._scroll_anim = anim
        anim.play()

    def _on_user_scrolled(self, _ctrl, _dx: float, _dy: float) -> bool:
        if not self._segments:
            return False
        if self._scroll_anim is not None:
            self._scroll_anim.pause()
            self._scroll_anim = None
        if self._auto_follow:
            self._auto_follow = False
            self.follow_btn.set_visible(True)
        return False

    def _on_resume_follow(self, _button) -> None:
        self._auto_follow = True
        self._last_scroll_target = None
        self.follow_btn.set_visible(False)
        if 0 <= self._active_index < len(self._rows):
            pos = self.playback.position if self.playback else 0.0
            self._scroll_to_row(self._rows[self._active_index], position=pos, force=True)

    # -- user actions ----------------------------------------------------------------

    def _on_live_clicked(self, _button) -> None:
        if self._episode is None or self.transcription is None:
            return
        if not self.ai.configured:
            self.activate_action("app.ai-preferences", None)
            return
        if self.transcription.is_live_active(self._episode.id):
            self.transcription.stop_live(self._episode.id)
        else:
            self.transcription.start_live(self._episode, self.playback.position)
        self._sync_controls()

    def _on_seek_segment(self, segment: TranscriptSegment) -> None:
        if self._loop_segment is not None:
            self._loop_segment = segment
        self.playback.seek(segment.start)
        if not self.playback.is_playing:
            self.playback.play()

    def _on_repeat_segment(self, segment: TranscriptSegment) -> None:
        self.playback.seek(segment.start)
        if not self.playback.is_playing:
            self.playback.play()

    def _on_repeat_current(self, _button) -> None:
        if 0 <= self._active_index < len(self._segments):
            self._on_repeat_segment(self._segments[self._active_index])

    def _on_loop_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active() and 0 <= self._active_index < len(self._segments):
            self._loop_segment = self._segments[self._active_index]
            self.playback.seek(self._loop_segment.start)
        else:
            self._loop_segment = None
            if button.get_active():
                button.set_active(False)

    def _on_bilingual_toggled(self, button: Gtk.ToggleButton) -> None:
        self._bilingual = button.get_active()
        for row in self._rows:
            row.set_bilingual(self._bilingual)
        if self._bilingual:
            self._maybe_request_translations(self.playback.position)

    def _maybe_request_translations(self, around_time: float = 0.0) -> None:
        if self._episode is None or self.transcription is None or not self._bilingual:
            return
        missing = any(not s.translation.strip() for s in self._segments)
        if not missing:
            return
        if not self.ai.configured:
            app = get_app()
            if app is not None:
                app._toast(_("Conecte sua chave do OpenRouter nas Preferências para tradução bilíngue"))
            self.bilingual_btn.set_active(False)
            return
        self.transcription.translate_missing_segments(
            self._episode.id,
            around_time=around_time,
            on_error=lambda err: self._notify_error(str(err)),
        )

    def _on_search_toggled(self, button: Gtk.ToggleButton) -> None:
        active = button.get_active()
        self.search_revealer.set_reveal_child(active)
        if active:
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text("")

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._search_query = entry.get_text()
        self._reload_segments()

    def _on_notebook_toggled(self, button: Gtk.ToggleButton) -> None:
        self._view_mode = "notebook" if button.get_active() else "transcript"
        self.body_stack.set_visible_child_name(self._view_mode)
        if self._view_mode == "notebook":
            self._rebuild_notebook()

    # -- AI Study Popover ------------------------------------------------------------

    def _on_study_segment(self, segment: TranscriptSegment, anchor: Gtk.Widget) -> None:
        if self._episode is None or self.transcription is None:
            return
        if not self.ai.configured:
            app = get_app()
            if app is not None:
                app._toast(_("Conecte o OpenRouter nas Preferências para estudar expressões"))
            self.activate_action("app.ai-preferences", None)
            return

        popover = Gtk.Popover(has_arrow=True, css_classes=["study-popover"])
        popover.set_parent(anchor)
        popover.set_size_request(310, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      css_classes=["study-popover-body"])
        box.append(label(segment.text, ("heading",), wrap=True, selectable=True,
                         xalign=0.0, max_width_chars=12))

        status = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        status.append(Adw.Spinner(width_request=18, height_request=18))
        status.append(label(_("Analisando expressão com IA…"), ("dim-label", "caption")))
        box.append(status)

        popover.set_child(box)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

        # Gather surrounding lines as context for accurate idiom explanation
        context = self._context_for(segment)
        ep_id = self._episode.id

        def on_done(note: StudyNote) -> None:
            if not popover.get_visible():
                return
            clear(box)
            box.append(label(note.text, ("heading",), wrap=True, selectable=True,
                             xalign=0.0, max_width_chars=12))
            if note.translation:
                box.append(label(note.translation, ("study-translation",), wrap=True,
                                 selectable=True, xalign=0.0, max_width_chars=12))
            if note.explanation:
                exp_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                max_content_height=220,
                                                propagate_natural_height=True)
                exp_scroll.set_child(label(note.explanation, ("study-explanation",), wrap=True,
                                           selectable=True, xalign=0.0, max_width_chars=12))
                box.append(exp_scroll)

            actions = Gtk.Box(spacing=6, halign=Gtk.Align.END)
            copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", css_classes=["flat", "circular"],
                                  tooltip_text=_("Copiar explicação"))
            copy_btn.connect("clicked", lambda *_: self._copy_note(note))
            actions.append(copy_btn)

            save_btn = Gtk.Button(
                label=_("Salvo no caderno") if note.saved else _("Salvar no caderno"),
                css_classes=["pill"] if note.saved else ["pill", "suggested-action"])

            def on_toggle_save(_btn) -> None:
                updated = self.transcription.toggle_save_note(note)
                note.saved = updated.saved
                save_btn.set_label(_("Salvo no caderno") if updated.saved else _("Salvar no caderno"))
                if updated.saved:
                    save_btn.remove_css_class("suggested-action")
                else:
                    save_btn.add_css_class("suggested-action")

            save_btn.connect("clicked", on_toggle_save)
            actions.append(save_btn)
            box.append(actions)

        def on_error(error: BaseException) -> None:
            if not popover.get_visible():
                return
            clear(box)
            box.append(label(_("Não foi possível analisar"), ("heading",), xalign=0.0))
            box.append(label(str(error), ("dim-label",), wrap=True, xalign=0.0, max_width_chars=12))

        self.transcription.study_segment(ep_id, segment, context,
                                         on_done=on_done, on_error=on_error)

    def _context_for(self, target: TranscriptSegment) -> str:
        for idx, seg in enumerate(self._segments):
            if seg.start == target.start:
                lo = max(0, idx - 1)
                hi = min(len(self._segments), idx + 2)
                return " ".join(s.text for s in self._segments[lo:hi])
        return target.text

    def _copy_note(self, note: StudyNote) -> None:
        parts = [note.text]
        if note.translation:
            parts.append(note.translation)
        if note.explanation:
            parts.append(note.explanation)
        Gdk.Display.get_default().get_clipboard().set("\n\n".join(parts))
        app = get_app()
        if app is not None:
            app._toast(_("Nota de estudo copiada"))

    # -- Study Notebook --------------------------------------------------------------

    def _rebuild_notebook(self) -> None:
        clear(self.notebook_box)
        if self.db is None:
            return
        ep_id = self._episode.id if self._episode else None
        notes = self.db.list_saved_study_notes(ep_id)
        if not notes:
            empty = Adw.StatusPage(
                icon_name="starred-symbolic",
                title=_("Caderno vazio"),
                description=_("Clique no ícone de dicionário em qualquer frase para estudar "
                              "expressões e salvar aqui."),
                css_classes=["compact"], vexpand=True)
            self.notebook_box.append(empty)
            return

        for note in notes:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           css_classes=["study-note-card"])
            head = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            jump_btn = Gtk.Button(label=format_clock(note.start),
                                  css_classes=["flat", "caption", "numeric"],
                                  tooltip_text=_("Ir para este trecho no áudio"))
            jump_btn.connect("clicked", lambda _b, t=note.start: self.playback.seek(t))
            head.append(jump_btn)
            head.append(Gtk.Box(hexpand=True))

            copy_btn = Gtk.Button(icon_name="edit-copy-symbolic",
                                  css_classes=["flat", "circular", "transcript-cue-btn"],
                                  tooltip_text=_("Copiar"))
            copy_btn.connect("clicked", lambda _b, n=note: self._copy_note(n))
            head.append(copy_btn)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic",
                                 css_classes=["flat", "circular", "transcript-cue-btn"],
                                 tooltip_text=_("Remover do caderno"))
            del_btn.connect("clicked", lambda _b, nid=note.id: self.db.delete_study_note(nid))
            head.append(del_btn)
            card.append(head)

            card.append(label(note.text, ("heading",), wrap=True, selectable=True,
                              xalign=0.0, max_width_chars=10))
            if note.translation:
                card.append(label(note.translation, ("study-translation",), wrap=True,
                                  selectable=True, xalign=0.0, max_width_chars=10))
            if note.explanation:
                card.append(label(note.explanation, ("caption", "dim-label"), wrap=True,
                                  selectable=True, xalign=0.0, max_width_chars=10))
            self.notebook_box.append(card)

    # -- Manager signals -------------------------------------------------------------

    def _on_segments_updated(self, _mgr, episode_id: str) -> None:
        if self._episode is None or self._episode.id != episode_id:
            return
        self._reload_segments()
        self._sync_controls()
        if self._bilingual:
            self._maybe_request_translations(self.playback.position)

    def _on_translation_updated(self, _mgr, episode_id: str) -> None:
        if self._episode is None or self._episode.id != episode_id:
            return
        fresh = {s.start: s.translation for s in self.transcription.segments_for(episode_id)}
        for seg, row in zip(self._segments, self._rows):
            tr = fresh.get(seg.start, "")
            if tr:
                seg.translation = tr
                row.set_bilingual(self._bilingual)

    def _on_state_changed(self, _mgr, episode_id: str, _state: str, _detail: str) -> None:
        if self._episode is None or self._episode.id != episode_id:
            return
        self._sync_controls()
        if not self._segments:
            self._reload_segments()

    def _notify_error(self, message: str) -> None:
        app = get_app()
        if app is not None:
            app._toast(message)

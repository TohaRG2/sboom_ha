/**
 * SBoom — Now Playing (метаданные + прогресс).
 *
 * Живёт внутри immersive-карточки плеера, поверх фрост-стекла (обложка —
 * фон карточки, рисует host). Здесь: заголовок дисплейным шрифтом, строка
 * «Исполнитель · Альбом», источник (радио/плейлист), бейджи E/Lyrics и
 * тонкий люкс-скраббер. Всё прозрачное, светлый текст.
 *
 * Позиция якорится к серверному снимку (position_sec на момент
 * position_ts_ms) и крутится по локальным часам — плавно, без сброса.
 * Клик/перетаск/клавиатура по дорожке эмитят `@seek {detail:{value}}`.
 */

import { LitElement, css, html, nothing } from "../lit-base.js";

class SboomNowPlaying extends LitElement {
  static get properties() {
    return {
      state: { type: Object },
      glow: { type: String },
      _dragging: { state: true },
    };
  }

  constructor() {
    super();
    this.state = null;
    this.glow = "";
    // Якорь позиции: базовый снимок + локальная метка времени (skew-proof).
    this._basePos = 0;
    this._baseWall = 0;
    this._lastPosKey = null;
    this._dragging = false;
    this._dragValue = 0;
    this._ticker = null;
  }

  get _track() {
    return this.state?.track || null;
  }
  get _playing() {
    return !!this._track?.playing;
  }
  get _duration() {
    const d = Number(this._track?.duration_sec);
    return Number.isFinite(d) && d > 0 ? d : 0;
  }

  // Позиция: при drag — «под пальцем»; иначе база + прошедшее (пока играет).
  get _shownPos() {
    if (this._dragging) return this._dragValue;
    let pos = this._basePos;
    if (this._playing) pos += (Date.now() - this._baseWall) / 1000;
    pos = Math.max(0, pos);
    const dur = this._duration;
    return dur ? Math.min(pos, dur) : pos;
  }

  _artistLine(track) {
    const artists = Array.isArray(track?.artists)
      ? track.artists.filter(Boolean)
      : [];
    const parts = [];
    if (artists.length) parts.push(artists.join(", "));
    else if (track?.artist) parts.push(track.artist);
    if (track?.album) parts.push(track.album);
    return parts.join(" · ");
  }

  _sourceLabel(track) {
    // Только радиостанция. playlist_title часто содержит мусор/чужой трек
    // (не связан с текущим), поэтому в now-playing его не показываем.
    const src = track?.station_name || "";
    if (!src || src === track?.album || src === track?.title) return "";
    return src;
  }

  _fmt(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
  }

  // ── Якорь позиции + локальный тикер ────────────────────────────────────
  updated(changed) {
    if (changed.has("state")) {
      this._reanchor(false);
      this._syncTicker();
    }
  }

  connectedCallback() {
    super.connectedCallback();
    this._reanchor(true);
    this._syncTicker();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopTicker();
  }

  // Ре-синк только при новом снимке (position_ts_ms/трек) — посторонние push
  // (обложка/лирика) прогресс не дёргают.
  _reanchor(force) {
    const track = this._track;
    const ts = Number(track?.position_ts_ms);
    const key = Number.isFinite(ts)
      ? `t${ts}`
      : `${track?.track_id}:${track?.position_sec}`;
    if (!force && key === this._lastPosKey) return;
    this._lastPosKey = key;
    let base = Number(track?.position_sec);
    base = Number.isFinite(base) ? base : 0;
    // Снимок мог быть сделан давно (напр. при перезагрузке страницы) —
    // прибавляем время, прошедшее с серверной метки position_ts_ms, как это
    // делает media_player через media_position_updated_at. При обычном push
    // метка свежая → прибавка ~0. Skew-защита: игнорируем аномалии.
    if (track?.playing && Number.isFinite(ts)) {
      const elapsed = (Date.now() - ts) / 1000;
      if (elapsed > 0 && elapsed < 86400) base += elapsed;
    }
    this._basePos = base;
    this._baseWall = Date.now();
  }

  _syncTicker() {
    // Крутим всегда пока играет (в т.ч. радио без длительности — тикает
    // прошедшее время). Останавливаем на паузе.
    if (this._playing) this._startTicker();
    else this._stopTicker();
  }

  _startTicker() {
    if (this._ticker) return;
    this._ticker = setInterval(() => {
      if (!this._dragging) this.requestUpdate();
    }, 1000);
  }

  _stopTicker() {
    if (this._ticker) {
      clearInterval(this._ticker);
      this._ticker = null;
    }
  }

  // ── Скраббер: pointer + клавиатура ─────────────────────────────────────
  _valueFromEvent(e, el) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    return Math.round(ratio * this._duration);
  }

  _onPointerDown(e) {
    if (!this._duration) return;
    e.preventDefault();
    const el = e.currentTarget;
    this._dragging = true;
    this._dragValue = this._valueFromEvent(e, el);
    el.setPointerCapture?.(e.pointerId);
  }

  _onPointerMove(e) {
    if (!this._dragging) return;
    this._dragValue = this._valueFromEvent(e, e.currentTarget);
  }

  _onPointerUp(e) {
    if (!this._dragging) return;
    const value = this._valueFromEvent(e, e.currentTarget);
    this._dragging = false;
    this._seekTo(value);
  }

  // Оптимистичный якорь после перемотки: база = значение, часы — сейчас.
  _seekTo(value) {
    this._basePos = value;
    this._baseWall = Date.now();
    this._emitSeek(value);
  }

  _onKeyScrub(e) {
    if (!this._duration) return;
    const step = e.shiftKey ? 30 : 5;
    let value = null;
    if (e.key === "ArrowRight")
      value = Math.min(this._duration, this._shownPos + step);
    else if (e.key === "ArrowLeft") value = Math.max(0, this._shownPos - step);
    else if (e.key === "Home") value = 0;
    else if (e.key === "End") value = this._duration;
    if (value === null) return;
    e.preventDefault();
    this._seekTo(value);
  }

  _emitSeek(value) {
    this.dispatchEvent(
      new CustomEvent("seek", {
        detail: { value },
        bubbles: true,
        composed: true,
      })
    );
  }

  static get styles() {
    return css`
      :host {
        display: block;
        color: #fff;
      }
      .np {
        min-width: 0;
      }
      .titlerow {
        display: flex;
        align-items: center;
        gap: 9px;
      }
      .title {
        flex: 1;
        min-width: 0;
        margin: 0;
        font-family: var(--sb-disp, system-ui);
        font-size: clamp(20px, 2.6vw, 26px);
        line-height: 1.15;
        font-weight: 700;
        letter-spacing: -0.02em;
        text-shadow: 0 1px 4px rgba(0, 0, 0, 0.55);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .marks {
        flex: none;
        display: flex;
        gap: 5px;
      }
      .sub {
        margin-top: 4px;
        font-size: 14px;
        color: rgba(255, 255, 255, 0.86);
        text-shadow: 0 1px 3px rgba(0, 0, 0, 0.5);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .src {
        margin-top: 3px;
        font-size: 12px;
        color: rgba(255, 255, 255, 0.6);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .pill {
        font-family: var(--sb-mono, monospace);
        font-size: 10px;
        letter-spacing: 0.06em;
        font-weight: 700;
        color: rgba(255, 255, 255, 0.82);
        padding: 2px 6px;
        border: 1px solid rgba(255, 255, 255, 0.3);
        border-radius: 4px;
        white-space: nowrap;
      }

      /* ── Люкс-скраббер ── */
      .scrubber {
        margin-top: 12px;
      }
      .track {
        padding: 8px 0;
        cursor: pointer;
        touch-action: none;
      }
      .track[aria-disabled="true"] {
        cursor: default;
      }
      .rail {
        position: relative;
        height: 4px;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.24);
      }
      .fill {
        position: absolute;
        left: 0;
        top: 0;
        bottom: 0;
        border-radius: 999px;
        background: #fff;
      }
      .knob {
        position: absolute;
        top: 50%;
        width: 13px;
        height: 13px;
        border-radius: 50%;
        background: #fff;
        transform: translate(-50%, -50%) scale(0);
        box-shadow: 0 1px 6px rgba(0, 0, 0, 0.5);
        transition: transform 0.15s ease;
      }
      .track:hover .knob,
      .track.dragging .knob,
      .track:focus-visible .knob {
        transform: translate(-50%, -50%) scale(1);
      }
      .track:focus-visible {
        outline: none;
      }
      .track:focus-visible .rail {
        box-shadow: 0 0 0 2px var(--sb-accent, #7c5cff);
      }
      .times {
        display: flex;
        justify-content: space-between;
        margin-top: 5px;
        font-family: var(--sb-mono, monospace);
        font-size: 11px;
        color: rgba(255, 255, 255, 0.62);
        font-variant-numeric: tabular-nums;
      }

      .empty {
        font-size: 15px;
        color: rgba(255, 255, 255, 0.7);
        padding: 8px 0;
      }

      @media (prefers-reduced-motion: reduce) {
        .knob {
          transition: none;
        }
      }
    `;
  }

  render() {
    const track = this._track;
    if (!track || !track.title) {
      return html`<div class="np">
        <div class="empty">Ничего не воспроизводится</div>
      </div>`;
    }

    const subline = this._artistLine(track);
    const source = this._sourceLabel(track);
    const dur = this._duration;
    const pos = this._shownPos;
    const pct = dur ? Math.min(100, Math.max(0, (pos / dur) * 100)) : 0;

    return html`
      <div class="np">
        <div class="titlerow">
          <h2 class="title" title=${track.title}>${track.title}</h2>
          ${track.explicit || track.has_lyrics
            ? html`<span class="marks">
                ${track.explicit
                  ? html`<span class="pill" title="Explicit">E</span>`
                  : nothing}
                ${track.has_lyrics
                  ? html`<span class="pill" title="Есть текст">LYR</span>`
                  : nothing}
              </span>`
            : nothing}
        </div>
        ${subline
          ? html`<div class="sub" title=${subline}>${subline}</div>`
          : nothing}
        ${source
          ? html`<div class="src" title=${source}>${source}</div>`
          : nothing}

        <div class="scrubber">
          <div
            class="track ${this._dragging ? "dragging" : ""}"
            role="slider"
            tabindex=${dur ? "0" : "-1"}
            aria-label="Позиция воспроизведения"
            aria-valuemin="0"
            aria-valuemax=${dur}
            aria-valuenow=${Math.round(pos)}
            aria-valuetext=${`${this._fmt(pos)} из ${this._fmt(dur)}`}
            aria-disabled=${dur ? "false" : "true"}
            @pointerdown=${this._onPointerDown}
            @pointermove=${this._onPointerMove}
            @pointerup=${this._onPointerUp}
            @pointercancel=${this._onPointerUp}
            @keydown=${this._onKeyScrub}
          >
            <div class="rail">
              <div class="fill" style="width:${pct}%"></div>
              <div class="knob" style="left:${pct}%"></div>
            </div>
          </div>
          <div class="times">
            <span>${this._fmt(pos)}</span>
            <span>${dur ? this._fmt(dur) : "live"}</span>
          </div>
        </div>
      </div>
    `;
  }
}

if (!customElements.get("sboom-nowplaying"))
  customElements.define("sboom-nowplaying", SboomNowPlaying);

/**
 * SBoom — Transport controls (люксовый минимализм, SVG-иконки).
 *
 * Живёт внутри immersive-карточки плеера (поверх фрост-стекла), поэтому
 * прозрачный фон и светлый контент. Безрамочные иконочные кнопки + одна
 * акцентная play/pause. Вторичные действия: shuffle / repeat / like.
 * Громкость — тонкий слайдер.
 *
 * Команды: WS `sboom/command` ({action, value?}). Состояние отражается из
 * `state.track` (playing/shuffle/repeat/liked) и `state.state`
 * (volume_percent/muted); координатор патчит его оптимистично, поэтому кнопки
 * реагируют мгновенно. Ошибка → `@toast`.
 */

import { LitElement, css, html, svg } from "../lit-base.js";

const REPEAT_CYCLE = { none: "all", all: "one", one: "none", context: "one" };

// Иконки — тонкие, единый viewBox 24. fill=currentColor.
const I = {
  prev: svg`<path d="M7 6a1 1 0 0 1 2 0v4.6l8.5-5A1 1 0 0 1 19 6.5v11a1 1 0 0 1-1.5.9L9 13.4V18a1 1 0 0 1-2 0V6z"/>`,
  next: svg`<path d="M17 6a1 1 0 0 0-2 0v4.6l-8.5-5A1 1 0 0 0 5 6.5v11a1 1 0 0 0 1.5.9L15 13.4V18a1 1 0 0 0 2 0V6z"/>`,
  play: svg`<path d="M8 5.14v13.72a1 1 0 0 0 1.5.86l11-6.86a1 1 0 0 0 0-1.72l-11-6.86A1 1 0 0 0 8 5.14z"/>`,
  pause: svg`<path d="M8 4.5A1.5 1.5 0 0 0 6.5 6v12a1.5 1.5 0 0 0 3 0V6A1.5 1.5 0 0 0 8 4.5zm8 0A1.5 1.5 0 0 0 14.5 6v12a1.5 1.5 0 0 0 3 0V6A1.5 1.5 0 0 0 16 4.5z"/>`,
  shuffle: svg`<path d="M16 4.3a1 1 0 0 1 1.7-.7l2.7 2.7a1 1 0 0 1 0 1.4L17.7 10.4a1 1 0 0 1-1.7-.7V8.5h-1.3l-2 2.6-1.3-1.6 1.9-2.5a1 1 0 0 1 .8-.4H16V4.3zM4 6h2.5a1 1 0 0 1 .8.4L15 16.5h1v-1.2a1 1 0 0 1 1.7-.7l2.7 2.7a1 1 0 0 1 0 1.4L17.7 21.4a1 1 0 0 1-1.7-.7V19.5h-2.5a1 1 0 0 1-.8-.4L4.9 8H4a1 1 0 0 1 0-2zm0 12h2.5a1 1 0 0 0 .8-.4l2-2.6-1.3-1.6-1.9 2.6H4a1 1 0 0 0 0 2z"/>`,
  repeat: svg`<path d="M7 7h9.6V5.2a.8.8 0 0 1 1.35-.57l2.8 2.8a.8.8 0 0 1 0 1.14l-2.8 2.8A.8.8 0 0 1 16.6 10.8V9H8v3a1 1 0 0 1-2 0V8a1 1 0 0 1 1-1zm10 10H8.4v1.8a.8.8 0 0 1-1.35.57l-2.8-2.8a.8.8 0 0 1 0-1.14l2.8-2.8A.8.8 0 0 1 8.4 13.2V15H17v-3a1 1 0 0 1 2 0v4a1 1 0 0 1-1 1z"/>`,
  heartOn: svg`<path d="M12 20.7l-1.3-1.2C6 15.3 3 12.5 3 9.1 3 6.4 5.1 4.3 7.8 4.3c1.5 0 3 .7 4.2 2 1.2-1.3 2.7-2 4.2-2C18.9 4.3 21 6.4 21 9.1c0 3.4-3 6.2-7.7 10.4L12 20.7z"/>`,
  heartOff: svg`<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M12 19.2l-1-.9C6.7 14.5 4 12 4 9.1 4 7 5.7 5.3 7.8 5.3c1.4 0 2.8.8 3.6 2 .3.4.9.4 1.2 0 .8-1.2 2.2-2 3.6-2C18.3 5.3 20 7 20 9.1c0 2.9-2.7 5.4-7 9.2l-1 .9z"/>`,
  vol: svg`<path d="M4 9.5A1.5 1.5 0 0 1 5.5 8H8l3.4-2.9A1 1 0 0 1 13 5.9v12.2a1 1 0 0 1-1.6.8L8 16H5.5A1.5 1.5 0 0 1 4 14.5v-5zM16 8.5a1 1 0 0 1 1.4.1 5.4 5.4 0 0 1 0 6.8 1 1 0 1 1-1.5-1.3 3.4 3.4 0 0 0 0-4.2 1 1 0 0 1 .1-1.4z"/>`,
  mute: svg`<path d="M4 9.5A1.5 1.5 0 0 1 5.5 8H8l3.4-2.9A1 1 0 0 1 13 5.9v12.2a1 1 0 0 1-1.6.8L8 16H5.5A1.5 1.5 0 0 1 4 14.5v-5zM16.3 9.3l1.7 1.7 1.7-1.7 1.3 1.3L19.3 12l1.7 1.7-1.3 1.3L18 13.3l-1.7 1.7-1.3-1.3L16.7 12l-1.7-1.7 1.3-1.3z"/>`,
  dislike: svg`<path d="M15 3H6a2 2 0 0 0-1.85 1.23l-3 7.05A2 2 0 0 0 1 12v1.9a2 2 0 0 0 2 2h5.3l-.8 3.86.02.32c0 .4.17.78.44 1.05l.9.87 5.55-5.56A2 2 0 0 0 17 15V5a2 2 0 0 0-2-2zm4 0v11h4V3h-4z"/>`,
  remote: svg`<path d="M15 9H9a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V10a1 1 0 0 0-1-1zm-3 11a1 1 0 1 1 0-2 1 1 0 0 1 0 2zM7.05 6.05l1.41 1.41A5 5 0 0 1 12 6a5 5 0 0 1 3.54 1.46l1.41-1.41A7 7 0 0 0 12 4a7 7 0 0 0-4.95 2.05zM12 0A11.96 11.96 0 0 0 4.22 3.22l1.41 1.41A9.95 9.95 0 0 1 12 2a9.95 9.95 0 0 1 6.36 2.64l1.41-1.41A11.96 11.96 0 0 0 12 0z"/>`,
};

class SboomControls extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      state: { type: Object },
      entryId: { type: String },
    };
  }

  constructor() {
    super();
    this.hass = null;
    this.state = null;
    this.entryId = null;
  }

  get _track() {
    return this.state?.track || null;
  }
  get _playing() {
    return !!this._track?.playing;
  }
  get _liked() {
    return !!this._track?.liked;
  }
  get _shuffle() {
    return !!this._track?.shuffle;
  }
  get _repeat() {
    const r = this._track?.repeat;
    if (r === "one" || r === "all" || r === "none") return r;
    if (r === "context") return "all";
    return "none";
  }
  get _muted() {
    return !!(this.state?.state?.muted ?? this.state?.muted);
  }
  get _volume() {
    const v = this.state?.state?.volume_percent ?? this.state?.volume_percent;
    return typeof v === "number" ? v : 0;
  }

  async _command(action, value) {
    if (!this.hass) return;
    const msg = { type: "sboom/command", action };
    if (value !== undefined) msg.value = value;
    if (this.entryId) msg.entry_id = this.entryId;
    try {
      await this.hass.callWS(msg);
      this.dispatchEvent(
        new CustomEvent("command-done", { bubbles: true, composed: true })
      );
    } catch (err) {
      this._toast(`Ошибка: ${err.message || err}`, "error");
    }
  }

  _togglePlay() {
    this._command(this._playing ? "pause" : "play");
  }
  _toggleMute() {
    this._command(this._muted ? "unmute" : "mute");
  }
  _toggleLike() {
    // лайк ↔ снять лайк (дизлайк — отдельная кнопка)
    this._command(this._liked ? "remove_like" : "like");
  }
  _dislike() {
    // дизлайк текущего трека (тюнинг персональной волны). Состояние
    // «disliked» колонка не отдаёт — действие одноразовое.
    this._command("dislike");
    this._toast("Дизлайк", "info");
  }
  _toggleShuffle() {
    this._command("shuffle", !this._shuffle);
  }
  _cycleRepeat() {
    this._command("repeat", REPEAT_CYCLE[this._repeat] || "all");
  }
  get _speed() {
    const s = Number(this._track?.playback_speed);
    return Number.isFinite(s) && s > 0 ? s : 1;
  }
  _cycleSpeed() {
    const steps = [0.75, 1, 1.25, 1.5, 2];
    const cur = this._speed;
    const idx = steps.findIndex((s) => Math.abs(s - cur) < 0.01);
    const next = steps[(idx + 1) % steps.length];
    this._command("playback_speed", next);
  }
  _findRemote() {
    this._command("find_remote");
    this._toast("Ищу пульт…", "info");
  }
  _onVolume(e) {
    this._command("volume", Number(e.target.value));
  }
  _fmtSpeed(s) {
    return (Number.isInteger(s) ? String(s) : String(s).replace(/0+$/, "")) + "×";
  }

  _toast(message, type) {
    this.dispatchEvent(
      new CustomEvent("toast", {
        detail: { message, type },
        bubbles: true,
        composed: true,
      })
    );
  }

  _ico(inner, size = 22) {
    return svg`<svg viewBox="0 0 24 24" width=${size} height=${size} fill="currentColor" aria-hidden="true">${inner}</svg>`;
  }

  static get styles() {
    return css`
      :host {
        display: block;
        color: #fff;
      }

      /* ── Всё в одну строку: secondary · prev · play · next · secondary ── */
      .row {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 18px;
      }

      /* безрамочная иконочная кнопка — никакого «пузыря» */
      .ic {
        appearance: none;
        border: none;
        background: none;
        padding: 4px;
        margin: 0;
        color: rgba(255, 255, 255, 0.9);
        cursor: pointer;
        display: inline-flex;
        line-height: 0;
        border-radius: 12px;
        transition: color 0.18s ease, transform 0.14s ease, opacity 0.18s ease;
      }
      /* вторичные — меньше и приглушённее */
      .ic.sm {
        color: rgba(255, 255, 255, 0.62);
      }
      .ic:hover {
        color: #fff;
        transform: translateY(-1px);
      }
      .ic:active {
        transform: scale(0.9);
      }
      .ic:focus-visible {
        outline: 2px solid var(--sb-accent, #7c5cff);
        outline-offset: 3px;
      }
      .ic.on {
        color: var(--sb-glow, #7c5cff);
      }
      .ic.liked {
        color: var(--sb-like, #ff5c8a);
      }
      .ic.rep {
        position: relative;
      }
      .ic .badge {
        position: absolute;
        right: 0;
        bottom: 0;
        font-family: var(--sb-mono);
        font-size: 8px;
        font-weight: 700;
        color: var(--sb-glow, #7c5cff);
      }

      /* акцентная play/pause — чистый светлый диск, тёмная иконка */
      .play {
        appearance: none;
        border: none;
        cursor: pointer;
        width: 48px;
        height: 48px;
        margin: 0 4px;
        border-radius: 50%;
        background: #fff;
        color: #111319;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 10px 26px -8px rgba(0, 0, 0, 0.6);
        transition: transform 0.14s ease, box-shadow 0.18s ease;
      }
      .play:hover {
        transform: scale(1.05);
        box-shadow: 0 14px 30px -8px rgba(0, 0, 0, 0.7);
      }
      .play:active {
        transform: scale(0.96);
      }
      .play:focus-visible {
        outline: 2px solid #fff;
        outline-offset: 4px;
      }

      /* ── Ряд действий: лайк · дизлайк · скорость · пульт ── */
      .actions {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 22px;
        margin-top: 12px;
      }
      .speed {
        appearance: none;
        border: none;
        background: none;
        cursor: pointer;
        min-width: 40px;
        padding: 4px 8px;
        border-radius: 999px;
        color: rgba(255, 255, 255, 0.62);
        font: inherit;
        font-size: 13px;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
        transition: color 0.16s ease, background 0.16s ease;
      }
      .speed:hover {
        color: #fff;
      }
      .speed.on {
        color: var(--sb-glow, #7c5cff);
      }
      .speed:focus-visible {
        outline: 2px solid var(--sb-accent, #7c5cff);
        outline-offset: 3px;
      }

      /* ── Громкость: тонкий слайдер ── */
      .volume {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 14px;
      }
      .volume .ic {
        color: rgba(255, 255, 255, 0.7);
        padding: 4px;
      }
      input[type="range"] {
        flex: 1;
        min-width: 0;
        height: 4px;
        cursor: pointer;
        -webkit-appearance: none;
        appearance: none;
        background: linear-gradient(
          to right,
          #fff 0%,
          #fff var(--pct, 0%),
          rgba(255, 255, 255, 0.24) var(--pct, 0%),
          rgba(255, 255, 255, 0.24) 100%
        );
        border-radius: 999px;
      }
      input[type="range"]::-webkit-slider-thumb {
        -webkit-appearance: none;
        width: 13px;
        height: 13px;
        border-radius: 50%;
        background: #fff;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
        transition: transform 0.14s ease;
      }
      input[type="range"]::-moz-range-thumb {
        width: 13px;
        height: 13px;
        border: none;
        border-radius: 50%;
        background: #fff;
      }
      input[type="range"]:hover::-webkit-slider-thumb {
        transform: scale(1.18);
      }
      input[type="range"]:focus-visible {
        outline: 2px solid var(--sb-accent, #7c5cff);
        outline-offset: 6px;
        border-radius: 999px;
      }
      .volume .val {
        min-width: 38px;
        text-align: right;
        font-family: var(--sb-mono);
        font-size: 12px;
        color: rgba(255, 255, 255, 0.7);
        font-variant-numeric: tabular-nums;
      }

      @media (prefers-reduced-motion: reduce) {
        .ic,
        .play,
        input[type="range"]::-webkit-slider-thumb {
          transition: none;
        }
        .ic:hover,
        .play:hover,
        .ic:active,
        .play:active {
          transform: none;
        }
      }
    `;
  }

  render() {
    const playing = this._playing;
    const repeat = this._repeat;
    const repeatLabel =
      repeat === "one"
        ? "Повтор трека"
        : repeat === "all"
          ? "Повтор всех"
          : "Повтор выключен";

    return html`
      <div class="row">
        <button
          class="ic sm ${this._shuffle ? "on" : ""}"
          aria-label="Перемешать"
          aria-pressed=${this._shuffle ? "true" : "false"}
          @click=${this._toggleShuffle}
        >
          ${this._ico(I.shuffle, 20)}
        </button>
        <button
          class="ic"
          aria-label="Предыдущий трек"
          @click=${() => this._command("prev")}
        >
          ${this._ico(I.prev, 26)}
        </button>
        <button
          class="play"
          aria-label=${playing ? "Пауза" : "Воспроизвести"}
          @click=${this._togglePlay}
        >
          ${this._ico(playing ? I.pause : I.play, 25)}
        </button>
        <button
          class="ic"
          aria-label="Следующий трек"
          @click=${() => this._command("next")}
        >
          ${this._ico(I.next, 26)}
        </button>
        <button
          class="ic sm rep ${repeat !== "none" ? "on" : ""}"
          aria-label=${repeatLabel}
          @click=${this._cycleRepeat}
        >
          ${this._ico(I.repeat, 20)}
          ${repeat === "one"
            ? html`<span class="badge" aria-hidden="true">1</span>`
            : ""}
        </button>
      </div>

      <div class="actions">
        <button
          class="ic ${this._liked ? "liked" : ""}"
          aria-label=${this._liked ? "Убрать лайк" : "Лайк"}
          aria-pressed=${this._liked ? "true" : "false"}
          @click=${this._toggleLike}
        >
          ${this._ico(this._liked ? I.heartOn : I.heartOff, 22)}
        </button>
        <button class="ic" aria-label="Дизлайк" @click=${this._dislike}>
          ${this._ico(I.dislike, 21)}
        </button>
        <button
          class="speed ${this._speed !== 1 ? "on" : ""}"
          aria-label="Скорость воспроизведения ${this._fmtSpeed(this._speed)}"
          @click=${this._cycleSpeed}
        >
          ${this._fmtSpeed(this._speed)}
        </button>
        <button class="ic" aria-label="Найти пульт" @click=${this._findRemote}>
          ${this._ico(I.remote, 20)}
        </button>
      </div>

      <div class="volume">
        <button
          class="ic"
          aria-label=${this._muted ? "Включить звук" : "Выключить звук"}
          @click=${this._toggleMute}
        >
          ${this._ico(this._muted ? I.mute : I.vol, 20)}
        </button>
        <input
          type="range"
          min="0"
          max="100"
          step="1"
          aria-label="Громкость"
          style=${`--pct:${this._volume}%`}
          .value=${String(this._volume)}
          @change=${this._onVolume}
        />
        <span class="val">${this._volume}%</span>
      </div>
    `;
  }
}

if (!customElements.get("sboom-controls"))
  customElements.define("sboom-controls", SboomControls);

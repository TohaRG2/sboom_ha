/**
 * SBoom — main panel (host).
 *
 * Премиум-плеер для колонки SberBoom. Тёмная самодостаточная «сцена»: hero
 * now-playing слева, справа browse (Очередь / Поиск). Сигнатура — ambient-glow
 * за hero, производный от доминирующего цвета обложки (эхо LED-кольца колонки).
 * См. DESIGN_SPEC.md.
 *
 * Data-feed (подписка sboom/subscribe, devices, state, @seek/@toast-релей)
 * унаследован из общего `SboomFeedBase` — тот же слой использует Lovelace-
 * карточка `sboom-card`. Панель добавляет presentation: раскладку/вкладки,
 * ambient-glow (--sb-glow из обложки), idle-приглушение, версию.
 */

const _v = new URL(import.meta.url).searchParams.get("v") || "";
const _q = _v ? `?v=${_v}` : "";
await Promise.all([
  import(`./components/sboom-toast.js${_q}`),
  import(`./components/sboom-nowplaying.js${_q}`),
  import(`./components/sboom-controls.js${_q}`),
  import(`./components/sboom-track-row.js${_q}`),
  import(`./components/sboom-tile.js${_q}`),
  import(`./components/sboom-browse.js${_q}`),
]);

import { html, css, nothing } from "./lit-base.js";
import { SboomFeedBase } from "./components/sboom-feed-base.js";
import { tokens } from "./components/sboom-tokens.css.js";

const DEFAULT_GLOW = "#7C5CFF";

class SboomPanel extends SboomFeedBase {
  static get properties() {
    return {
      narrow: { type: Boolean },
      panel: { type: Object },
      _glow: { type: String },
      _picker: { type: Boolean },
      _idle: { type: Boolean },
    };
  }

  constructor() {
    super();
    this._glow = DEFAULT_GLOW;
    this._picker = false; // открыт ли дропдаун выбора колонки
    this._lastCover = null;
    this._idle = false; // панель приглушена после простоя
    this._idleTimer = null;
    this._wake = this._wake.bind(this);
  }

  connectedCallback() {
    super.connectedCallback(); // поднимает feed
    this._wake();
  }

  disconnectedCallback() {
    super.disconnectedCallback(); // гасит feed
    clearTimeout(this._idleTimer);
  }

  // Активность мыши/касания «будит» панель; через 10с простоя — приглушаем
  // (контролы+метаданные становятся прозрачнее, обложка видна лучше).
  _wake() {
    if (this._idle) this._idle = false;
    clearTimeout(this._idleTimer);
    this._idleTimer = setTimeout(() => {
      this._idle = true;
    }, 10000);
  }

  get _version() {
    return this.panel?.config?.version || this._state?.version || "";
  }

  _togglePicker(e) {
    e?.stopPropagation();
    this._picker = !this._picker;
    if (this._picker) {
      const close = () => {
        this._picker = false;
        window.removeEventListener("click", close);
      };
      setTimeout(() => window.addEventListener("click", close), 0);
    }
  }

  // при переключении колонки закрываем дропдаун; сброс glow — в хуке базы
  _selectDevice(entryId) {
    this._picker = false;
    super._selectDevice(entryId);
  }

  _onDeviceSelected(_entryId) {
    this._glow = DEFAULT_GLOW;
    this._lastCover = null;
  }

  get _deviceName() {
    const d = this._devices.find((x) => x.entry_id === this._entryId);
    return d?.name || "SberBoom";
  }

  // хук базы: новое состояние → пересчитать ambient glow из обложки
  _onStateApplied(state) {
    this._syncGlow(state?.track?.cover_url || null);
  }

  // ── ambient glow: доминирующий цвет обложки (считается на сервере) ──────
  // CDN Звука не отдаёт CORS → клиентский canvas невозможен. Цвет берём через
  // WS sboom/cover_color (Pillow на бэкенде, кеш по URL) — обёртка в базе.
  async _syncGlow(coverUrl) {
    if (coverUrl === this._lastCover) return;
    this._lastCover = coverUrl;
    if (!coverUrl) {
      this._glow = DEFAULT_GLOW;
      return;
    }
    const color = await this.fetchCoverColor(coverUrl);
    // защита от гонки: обложка могла смениться, пока считался цвет
    if (coverUrl === this._lastCover) {
      this._glow = color || DEFAULT_GLOW;
    }
  }

  static get styles() {
    return [
      tokens,
      css`
        :host {
          display: block;
          min-height: 100%;
          box-sizing: border-box;
          color: var(--sb-ink);
          background:
            radial-gradient(
              120% 80% at 18% 0%,
              var(--sb-glow-soft),
              transparent 60%
            ),
            var(--sb-stage);
          font-family: system-ui, "Segoe UI", Roboto, sans-serif;
          transition: --sb-glow 0.6s ease;
        }

        .wrap {
          display: grid;
          grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
          gap: 24px;
          max-width: 1120px;
          margin: 0 auto;
          padding: 22px 22px 40px;
          box-sizing: border-box;
        }

        .left {
          min-width: 0;
          position: sticky;
          top: 22px;
          align-self: start;
        }
        .right {
          min-width: 0;
          display: flex;
          flex-direction: column;
        }

        /* ── Immersive player card: обложка-фон + фрост-стекло снизу ── */
        .player {
          position: relative;
          border-radius: var(--sb-radius);
          overflow: hidden;
          aspect-ratio: 1 / 1; /* ближе к квадрату */
          min-height: 520px;
          display: flex;
          isolation: isolate;
          box-shadow:
            0 26px 64px -24px rgba(0, 0, 0, 0.65),
            inset 0 0 0 1px rgba(255, 255, 255, 0.06);
        }
        .art {
          position: absolute;
          inset: 0;
          z-index: 0;
          background:
            radial-gradient(
              120% 90% at 30% 12%,
              var(--sb-glow-soft),
              transparent 60%
            ),
            var(--sb-elev);
        }
        .art img {
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
        }
        .veil {
          position: absolute;
          inset: 0;
          z-index: 1;
          pointer-events: none;
          background: linear-gradient(
            to bottom,
            rgba(6, 6, 10, 0.5),
            transparent 24%
          );
        }
        .head {
          position: absolute;
          inset: 0 0 auto 0;
          z-index: 3;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          padding: 14px 16px;
        }
        .picker {
          position: relative;
          min-width: 0;
        }
        .brand {
          display: inline-flex;
          align-items: center;
          gap: 9px;
          max-width: 60vw;
          border: none;
          background: rgba(0, 0, 0, 0.3);
          backdrop-filter: blur(8px);
          color: #fff;
          font: inherit;
          font-weight: 600;
          font-size: 14px;
          padding: 7px 12px;
          border-radius: 999px;
          cursor: default;
        }
        .brand.switch {
          cursor: pointer;
        }
        .brand.switch:hover {
          background: rgba(0, 0, 0, 0.46);
        }
        .brand:focus-visible {
          outline: 2px solid #fff;
          outline-offset: 2px;
        }
        .brand .nm {
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .brand .chev {
          opacity: 0.7;
          font-size: 10px;
        }
        .dot {
          width: 8px;
          height: 8px;
          flex: none;
          border-radius: 50%;
          background: var(--sb-glow);
          box-shadow: 0 0 10px var(--sb-glow);
        }
        .dot.off {
          background: rgba(255, 255, 255, 0.5);
          box-shadow: none;
        }
        .menu {
          position: absolute;
          top: calc(100% + 6px);
          left: 0;
          min-width: 210px;
          padding: 6px;
          background: rgba(20, 20, 26, 0.94);
          backdrop-filter: blur(22px);
          border: 1px solid var(--sb-line);
          border-radius: 14px;
          box-shadow: 0 20px 44px -16px rgba(0, 0, 0, 0.72);
          z-index: 5;
        }
        .menu button {
          display: flex;
          align-items: center;
          gap: 9px;
          width: 100%;
          border: none;
          background: none;
          color: var(--sb-ink);
          font: inherit;
          font-size: 13px;
          text-align: left;
          padding: 9px 10px;
          border-radius: 9px;
          cursor: pointer;
        }
        .menu button:hover {
          background: var(--sb-elev-2);
        }
        .menu button[aria-current="true"] {
          color: var(--sb-glow);
        }
        .version {
          font-family: var(--sb-mono);
          font-size: 11px;
          color: rgba(255, 255, 255, 0.72);
          padding: 4px 9px;
          border-radius: 999px;
          background: rgba(0, 0, 0, 0.3);
          backdrop-filter: blur(8px);
          flex: none;
        }
        /* Apple-vibrancy: лёгкий блюр + насыщенность, невысокая непрозрачность —
           обложка под стеклом остаётся узнаваемой. */
        .glass {
          position: relative;
          z-index: 2;
          margin-top: auto;
          width: 100%;
          box-sizing: border-box;
          padding: 16px 22px 16px;
          background: linear-gradient(
            to top,
            rgba(8, 8, 12, 0.78) 0%,
            rgba(8, 8, 12, 0.5) 60%,
            rgba(8, 8, 12, 0.22) 100%
          );
          backdrop-filter: blur(18px) saturate(1.6);
          -webkit-backdrop-filter: blur(18px) saturate(1.6);
          transition: padding 0.45s ease, opacity 0.45s ease;
        }
        /* простой: «схлопываем» — прячем контролы+громкость, оставляем
           метаданные и прогресс; высота падает до минимума + лёгкая (не
           сильная) прозрачность */
        .player.idle .glass {
          padding-bottom: 14px;
          opacity: 0.82;
        }

        .error {
          padding: 12px 14px;
          margin-bottom: 16px;
          background: color-mix(in srgb, #ff4d4f 20%, var(--sb-elev));
          border: 1px solid color-mix(in srgb, #ff4d4f 45%, transparent);
          color: var(--sb-ink);
          border-radius: var(--sb-radius-sm);
          font-size: 13px;
        }

        sboom-controls {
          display: block;
          margin-top: 16px;
          overflow: hidden;
          max-height: 220px;
          opacity: 1;
          transition: max-height 0.45s ease, opacity 0.35s ease,
            margin-top 0.45s ease;
        }
        /* idle: контролы+громкость схлопываются в ноль */
        .player.idle sboom-controls {
          max-height: 0;
          opacity: 0;
          margin-top: 0;
          pointer-events: none;
        }
        sboom-browse {
          display: block;
        }

        @media (max-width: 820px) {
          .wrap {
            grid-template-columns: 1fr;
            gap: 18px;
            padding: 14px 12px 32px;
          }
          .left {
            position: static;
          }
          .player {
            min-height: 0;
          }
          .glass {
            padding: 24px 18px 18px;
          }
        }
      `,
    ];
  }

  render() {
    const connected = this._state?.connected;
    const cover = this._state?.track?.cover_url || "";
    const multi = this._devices.length > 1;
    return html`
      <div
        class="wrap"
        style=${this._glow ? `--sb-glow:${this._glow}` : nothing}
        @toast=${this._onToast}
        @command-done=${this._fetchState}
      >
        <section class="left">
          ${this._error ? html`<div class="error">${this._error}</div>` : ""}

          <div
            class="player ${this._idle ? "idle" : ""}"
            @pointermove=${this._wake}
            @pointerdown=${this._wake}
            @touchstart=${this._wake}
          >
            <div class="art ${cover ? "" : "empty"}">
              ${cover ? html`<img src=${cover} alt="" />` : ""}
            </div>
            <div class="veil"></div>

            <div class="head">
              <div class="picker">
                <button
                  class="brand ${multi ? "switch" : ""}"
                  aria-haspopup=${multi ? "menu" : nothing}
                  aria-expanded=${multi ? String(this._picker) : nothing}
                  @click=${multi ? this._togglePicker : nothing}
                >
                  <span class="dot ${connected ? "" : "off"}"></span>
                  <span class="nm">${this._deviceName}</span>
                  ${multi ? html`<span class="chev">▾</span>` : ""}
                </button>
                ${this._picker && multi
                  ? html`<div class="menu" role="menu">
                      ${this._devices.map(
                        (d) => html`<button
                          role="menuitemradio"
                          aria-current=${d.entry_id === this._entryId
                            ? "true"
                            : "false"}
                          @click=${() => this._selectDevice(d.entry_id)}
                        >
                          <span class="dot"></span>${d.name}
                        </button>`
                      )}
                    </div>`
                  : ""}
              </div>
              ${this._version
                ? html`<span class="version">v${this._version}</span>`
                : ""}
            </div>

            <div class="glass">
              <sboom-nowplaying
                .state=${this._state}
                @seek=${this._onSeek}
              ></sboom-nowplaying>
              <sboom-controls
                .hass=${this.hass}
                .state=${this._state}
                .entryId=${this._entryId}
              ></sboom-controls>
            </div>
          </div>
        </section>

        <section class="right">
          <sboom-browse
            .hass=${this.hass}
            .entryId=${this._entryId}
            .currentTrackId=${this._state?.track?.track_id}
          ></sboom-browse>
        </section>
      </div>

      <sboom-toast></sboom-toast>
    `;
  }
}

customElements.define("sboom-panel", SboomPanel);

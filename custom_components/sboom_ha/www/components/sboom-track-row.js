/**
 * SBoom — Track Row (примитив строки трека).
 *
 * Переиспользуемая строка трека для всех представлений drill-down браузера:
 * очередь, результаты поиска, топ-треки артиста, треклист альбома. Чисто
 * презентационный компонент — НЕ ходит в WS. По клику/Enter/Space эмитит
 * `open` с `{track}` вверх; родитель (`sboom-browse`) решает, играть или нет.
 *
 * Слева — либо номер `index` (треклист альбома, моно-tabular, --sb-ink-faint),
 * либо обложка `cover_url` (40px, --sb-radius-sm, lazy) с плейсхолдером.
 * Центр — title + строка исполнителей (--sb-ink-dim, ellipsis). Справа —
 * компактные пилюли E/FLAC (моно, бордер --sb-line) + длительность mm:ss
 * (моно-tabular). При `active` — glow-подсветка, левый акцентный бордер и
 * живой 3-полосный эквалайзер (статичен при prefers-reduced-motion).
 *
 * `artists` принимает массив строк ИЛИ объектов {title}/{name} — оба варианта
 * нормализуются в `_artistLine`. Компонент ТОЛЬКО потребляет токены --sb-* с
 * host (см. DESIGN_SPEC.md / DESIGN_SPEC_BROWSE.md), не переопределяя их.
 */

import { LitElement, html, css } from "../lit-base.js";

class SboomTrackRow extends LitElement {
  static get properties() {
    return {
      track: { type: Object },
      index: { type: Number },
      active: { type: Boolean, reflect: true },
    };
  }

  constructor() {
    super();
    this.track = null;
    this.index = null;
    this.active = false;
  }

  /** Нормализует artists (массив строк | объектов | строку) в единую строку. */
  _artistLine() {
    const a = this.track?.artists;
    if (Array.isArray(a)) {
      const line = a
        .map((x) => (typeof x === "string" ? x : x?.title || x?.name || ""))
        .filter(Boolean)
        .join(", ");
      if (line) return line;
    }
    if (typeof a === "string" && a) return a;
    // fallback для элементов поиска: singular artist / subtitle
    return this.track?.artist || this.track?.subtitle || "";
  }

  /** Секунды → mm:ss (пусто, если длительность не задана/некорректна). */
  _duration() {
    const n = Number(this.track?.duration);
    if (!Number.isFinite(n) || n <= 0) return "";
    const m = Math.floor(n / 60);
    const s = Math.floor(n % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  // Захватываем трек в момент НАЖАТИЯ: если между pointerdown и click произойдёт
  // ре-рендер/пересборка списка и `.track` этого узла подменится — клик всё
  // равно унесёт тот трек, на который пользователь реально нажал.
  _onPointerDown(e) {
    this._pressed = this.track;
    console.info(
      "[sboom] track-row pointerdown",
      e?.pointerType,
      this._pressed?.title,
      this._pressed?.track_id ?? this._pressed?.id
    );
  }

  _open(e) {
    const t = this._pressed || this.track;
    this._pressed = null;
    console.info(
      "[sboom] track-row _open",
      e?.type,
      "→",
      t?.title,
      t?.track_id ?? t?.id,
      t ? "" : "(NO TRACK!)"
    );
    if (!t) return;
    this.dispatchEvent(
      new CustomEvent("open", {
        detail: { track: t },
        bubbles: true,
        composed: true,
      })
    );
  }

  _onKeydown(e) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      this._pressed = this.track;
      this._open();
    }
  }

  static get styles() {
    return css`
      :host {
        display: block;
      }

      .row {
        position: relative;
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 8px;
        border-radius: var(--sb-radius-sm);
        border-left: 2px solid transparent;
        cursor: pointer;
        user-select: none; /* текст не должен «съедать» одиночный тап */
        -webkit-user-select: none;
        touch-action: manipulation; /* без double-tap-zoom задержки на тач */
        -webkit-tap-highlight-color: transparent;
        transition: background 0.15s ease, border-color 0.15s ease;
      }

      .row:hover {
        background: var(--sb-elev-2);
      }

      .row:focus-visible {
        outline: 2px solid var(--sb-accent);
        outline-offset: -2px;
      }

      :host([active]) .row {
        background: var(--sb-glow-soft);
        border-left-color: var(--sb-accent);
      }

      /* --- Левый слот: номер ИЛИ обложка --- */
      .lead {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
      }

      .num {
        width: 32px;
        text-align: center;
        font-family: var(--sb-mono, ui-monospace, "SF Mono", "Roboto Mono", monospace);
        font-size: 13px;
        font-variant-numeric: tabular-nums;
        color: var(--sb-ink-faint);
      }

      .cover {
        position: relative;
        width: 40px;
        height: 40px;
        border-radius: var(--sb-radius-sm);
        overflow: hidden;
        background: var(--sb-elev-2);
      }

      .cover img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
      }

      /* Плейсхолдер обложки (SVG нота) */
      .ph {
        width: 100%;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--sb-ink-faint);
      }

      .ph svg {
        width: 18px;
        height: 18px;
        fill: currentColor;
      }

      /* --- Центр --- */
      .info {
        min-width: 0;
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }

      .title {
        font-family: system-ui, "Segoe UI", Roboto, sans-serif;
        font-size: 14px;
        color: var(--sb-ink);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      :host([active]) .title {
        font-weight: 600;
        color: var(--sb-ink);
      }

      .artist {
        font-size: 12px;
        color: var(--sb-ink-dim);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      /* --- Справа: бейджи + длительность / эквалайзер --- */
      .meta {
        flex: none;
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .badge {
        font-family: var(--sb-mono, ui-monospace, "SF Mono", "Roboto Mono", monospace);
        font-size: 9px;
        font-weight: 700;
        line-height: 1;
        letter-spacing: 0.02em;
        color: var(--sb-ink-faint);
        border: 1px solid var(--sb-line);
        border-radius: 3px;
        padding: 2px 4px;
      }

      .dur {
        min-width: 34px;
        text-align: right;
        font-family: var(--sb-mono, ui-monospace, "SF Mono", "Roboto Mono", monospace);
        font-size: 12px;
        font-variant-numeric: tabular-nums;
        color: var(--sb-ink-dim);
      }

      /* Эквалайзер текущего трека */
      .eq {
        display: flex;
        align-items: flex-end;
        gap: 2px;
        width: 16px;
        height: 16px;
      }

      .eq span {
        flex: 1;
        background: var(--sb-accent);
        border-radius: 1px;
        transform-origin: bottom;
        animation: eq 1s ease-in-out infinite;
      }

      .eq span:nth-child(1) {
        animation-delay: -0.4s;
      }
      .eq span:nth-child(2) {
        animation-delay: -0.15s;
      }
      .eq span:nth-child(3) {
        animation-delay: -0.65s;
      }

      @keyframes eq {
        0%,
        100% {
          height: 30%;
        }
        50% {
          height: 100%;
        }
      }

      @media (prefers-reduced-motion: reduce) {
        .eq span {
          animation: none;
          height: 60%;
        }
        .row {
          transition: none;
        }
      }

      @media (max-width: 480px) {
        .badge {
          display: none;
        }
      }
    `;
  }

  render() {
    const t = this.track;
    if (!t) return html``;

    const hasIndex = this.index != null && this.index !== "";
    const artists = this._artistLine();
    const dur = this._duration();
    const title = t.title || (hasIndex ? "—" : `#${t.id ?? t.track_id ?? ""}`);

    return html`
      <div
        class="row"
        role="button"
        tabindex="0"
        aria-current=${this.active ? "true" : "false"}
        aria-label=${`${title}${artists ? " — " + artists : ""}`}
        @pointerdown=${this._onPointerDown}
        @click=${this._open}
        @keydown=${this._onKeydown}
      >
        <div class="lead">
          ${hasIndex && !this.active
            ? html`<div class="num">${this.index}</div>`
            : hasIndex && this.active
            ? html`<div class="eq" aria-hidden="true">
                <span></span><span></span><span></span>
              </div>`
            : html`<div class="cover">
                ${t.cover_url
                  ? html`<img src=${t.cover_url} alt="" loading="lazy" />`
                  : html`<div class="ph" aria-hidden="true">
                      <svg viewBox="0 0 24 24">
                        <path
                          d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"
                        />
                      </svg>
                    </div>`}
              </div>`}
        </div>

        <div class="info">
          <div class="title">${title}</div>
          ${artists ? html`<div class="artist">${artists}</div>` : ""}
        </div>

        <div class="meta">
          ${t.explicit ? html`<span class="badge" title="Explicit">E</span>` : ""}
          ${t.has_flac
            ? html`<span class="badge" title="FLAC">FLAC</span>`
            : ""}
          ${this.active && !hasIndex
            ? html`<div class="eq" aria-hidden="true">
                <span></span><span></span><span></span>
              </div>`
            : dur
            ? html`<span class="dur">${dur}</span>`
            : ""}
        </div>
      </div>
    `;
  }
}

if (!customElements.get("sboom-track-row"))
  customElements.define("sboom-track-row", SboomTrackRow);

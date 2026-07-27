/**
 * SBoom — Tile (примитив тайл-обложка для drill-down браузера).
 *
 * Переиспользуемый тайл `<sboom-tile .item .shape>`: обложка сверху + title и
 * опциональный subtitle снизу. Раскладывается в горизонтальные shelf'ы поиска и
 * дискографии артиста (`flex: none`, фиксированная ширина ~132px, адаптив).
 *
 * `item = {id, title, subtitle?, cover_url, type, pt}`.
 * `shape`: "round" (артист — обложка кругом, текст по центру) | "square"
 * (альбом/плейлист — обложка с радиусом --sb-radius-sm, текст слева).
 * Плейсхолдер с нотой, если нет cover_url; img — object-fit cover, loading=lazy.
 *
 * Тайл — интерактивная кнопка (role=button, tabindex): клик / Enter / Space →
 * `dispatchEvent("open", {detail:{item}, bubbles:true, composed:true})`. Сам WS
 * не вызывает — маршрутизацию drill/play делает host (sboom-browse). Hover слегка
 * приподнимает тайл (translateY -2px) и подсвечивает обложку; :focus-visible —
 * outline --sb-accent; prefers-reduced-motion отключает подъём. Потребляет
 * дизайн-токены --sb-* (заданы на host), не переопределяет их.
 */

import { LitElement, html, css } from "../lit-base.js";

class SboomTile extends LitElement {
  static get properties() {
    return {
      item: { type: Object },
      shape: { type: String, reflect: true },
    };
  }

  constructor() {
    super();
    this.item = null;
    this.shape = "square";
  }

  // Захват item в момент нажатия — защита от подмены `.item` при ре-рендере
  // между pointerdown и click (см. sboom-track-row).
  _onPointerDown() {
    this._pressed = this.item;
  }

  _open() {
    const it = this._pressed || this.item;
    this._pressed = null;
    if (!it) return;
    this.dispatchEvent(
      new CustomEvent("open", {
        detail: { item: it },
        bubbles: true,
        composed: true,
      })
    );
  }

  _onKeydown(e) {
    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      this._pressed = this.item;
      this._open();
    }
  }

  static get styles() {
    return css`
      :host {
        display: block;
        flex: none;
      }

      .tile {
        display: flex;
        flex-direction: column;
        gap: 8px;
        width: 132px;
        padding: 10px 8px 12px;
        box-sizing: border-box;
        border: none;
        border-radius: var(--sb-radius, 18px);
        background: transparent;
        color: inherit;
        cursor: pointer;
        text-align: left;
        user-select: none;
        -webkit-user-select: none;
        touch-action: manipulation;
        -webkit-tap-highlight-color: transparent;
        transition: background 0.16s ease, transform 0.16s ease;
      }
      .tile:hover {
        background: var(--sb-elev, #16161d);
        transform: translateY(-2px);
      }
      .tile:active {
        transform: translateY(-1px);
      }

      /* ── Обложка ─────────────────────────────────────────────── */
      .cover {
        width: 116px;
        height: 116px;
        margin: 0 auto;
        display: block;
        object-fit: cover;
        background: var(--sb-elev-2, #1e1e27);
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.28);
        transition: box-shadow 0.16s ease, filter 0.16s ease;
      }
      .tile:hover .cover {
        filter: brightness(1.06);
        box-shadow: 0 8px 22px rgba(0, 0, 0, 0.4);
      }
      .cover.round {
        border-radius: 50%;
      }
      .cover.square {
        border-radius: var(--sb-radius-sm, 12px);
      }
      .cover.ph {
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--sb-ink-faint, rgba(245, 245, 247, 0.38));
      }
      .cover.ph svg {
        width: 40px;
        height: 40px;
      }

      /* ── Подписи ─────────────────────────────────────────────── */
      .meta {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      :host([shape="round"]) .meta {
        text-align: center;
      }
      .title {
        font-size: 13px;
        font-weight: 500;
        line-height: 1.3;
        color: var(--sb-ink, #f5f5f7);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .subtitle {
        font-size: 12px;
        line-height: 1.3;
        color: var(--sb-ink-faint, rgba(245, 245, 247, 0.38));
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      /* ── Доступность / motion ────────────────────────────────── */
      .tile:focus-visible {
        outline: 2px solid var(--sb-accent, #7c5cff);
        outline-offset: 2px;
      }
      @media (prefers-reduced-motion: reduce) {
        .tile,
        .cover {
          transition: none;
        }
        .tile:hover,
        .tile:active {
          transform: none;
        }
      }

      /* ── Адаптив ─────────────────────────────────────────────── */
      @media (max-width: 480px) {
        .tile {
          width: 112px;
        }
        .cover {
          width: 96px;
          height: 96px;
        }
      }
    `;
  }

  _renderCover(shape) {
    const cls = `cover ${shape}`;
    const cover = this.item?.cover_url;
    if (cover) {
      return html`<img
        class=${cls}
        src=${cover}
        alt=""
        loading="lazy"
        draggable="false"
      />`;
    }
    return html`<div class="${cls} ph" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none">
        <path
          d="M9 18V6l10-2v12"
          stroke="currentColor"
          stroke-width="1.6"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
        <circle cx="6.5" cy="18" r="2.5" stroke="currentColor" stroke-width="1.6" />
        <circle cx="16.5" cy="16" r="2.5" stroke="currentColor" stroke-width="1.6" />
      </svg>
    </div>`;
  }

  render() {
    const item = this.item || {};
    const shape = this.shape === "round" ? "round" : "square";
    const title = item.title || "—";
    return html`
      <div
        class="tile"
        role="button"
        tabindex="0"
        aria-label=${item.subtitle ? `${title} — ${item.subtitle}` : title}
        title=${title}
        @pointerdown=${this._onPointerDown}
        @click=${this._open}
        @keydown=${this._onKeydown}
      >
        ${this._renderCover(shape)}
        <div class="meta">
          <div class="title">${title}</div>
          ${item.subtitle
            ? html`<div class="subtitle">${item.subtitle}</div>`
            : ""}
        </div>
      </div>
    `;
  }
}

if (!customElements.get("sboom-tile")) {
  customElements.define("sboom-tile", SboomTile);
}

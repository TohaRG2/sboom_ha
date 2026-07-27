/**
 * SBoom — <sboom-toast>: всплывающее уведомление сцены.
 *
 * Тёмная плашка снизу-по-центру, авто-скрытие ~3s, плавное появление/уход.
 * Тип задаёт цвет статус-индикатора: success — зелёный, error — красный,
 * info — акцент сцены (--sb-accent, производный от обложки). Плашка —
 * var(--sb-elev-2), текст --sb-ink, radius --sb-radius-sm, мягкая тень.
 *
 * Host завязан на публичный метод .show(message, type): его вызывают из
 * обработчика @toast. Сигнатура сохранена. role="status" + aria-live="polite"
 * — screen reader зачитывает сообщение без перехвата фокуса.
 *
 * prefers-reduced-motion: reduce → без transition (мгновенно вкл/выкл).
 */

import { LitElement, html, css } from "../lit-base.js";

class SboomToast extends LitElement {
  static get properties() {
    return {
      _message: { type: String, state: true },
      _type: { type: String, state: true },
      _visible: { type: Boolean, state: true },
    };
  }

  constructor() {
    super();
    this._message = "";
    this._type = "info";
    this._visible = false;
    this._timer = null;
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }

  /**
   * Показать уведомление. Host зовёт toast.show(msg, type).
   * @param {string} message — текст
   * @param {"success"|"error"|"info"} type — тип (влияет только на индикатор)
   * @param {number} duration — время до авто-скрытия, мс
   */
  show(message, type = "info", duration = 3000) {
    if (this._timer) clearTimeout(this._timer);
    this._message = message;
    this._type = ["success", "error", "info"].includes(type) ? type : "info";
    this._visible = true;
    this._timer = setTimeout(() => {
      this._visible = false;
      this._timer = null;
    }, duration);
  }

  static get styles() {
    return css`
      :host {
        position: fixed;
        left: 50%;
        bottom: calc(env(safe-area-inset-bottom, 0px) + 24px);
        transform: translateX(-50%);
        z-index: 10000;
        pointer-events: none;
        display: block;
        max-width: min(92vw, 440px);
      }

      .toast {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 12px 18px;
        border-radius: var(--sb-radius-sm, 12px);
        background: var(--sb-elev-2, #1e1e27);
        border: 1px solid var(--sb-line, rgba(255, 255, 255, 0.08));
        color: var(--sb-ink, #f5f5f7);
        font-family: system-ui, "Segoe UI", Roboto, sans-serif;
        font-size: 14px;
        line-height: 1.35;
        box-shadow:
          0 12px 32px rgba(0, 0, 0, 0.45),
          0 2px 8px rgba(0, 0, 0, 0.3);
        pointer-events: auto;
        opacity: 0;
        transform: translateY(12px) scale(0.98);
        transition:
          opacity 0.28s ease,
          transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
      }

      .toast.visible {
        opacity: 1;
        transform: translateY(0) scale(1);
      }

      /* Статус-индикатор — точка слева, цвет по типу */
      .dot {
        flex: 0 0 auto;
        width: 9px;
        height: 9px;
        border-radius: 50%;
        background: var(--dot, var(--sb-accent, #7c5cff));
        box-shadow: 0 0 10px var(--dot, var(--sb-accent, #7c5cff));
      }

      .toast.info {
        --dot: var(--sb-accent, #7c5cff);
      }
      .toast.success {
        --dot: #34d399;
      }
      .toast.error {
        --dot: #f87171;
      }

      .msg {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      @media (prefers-reduced-motion: reduce) {
        .toast {
          transition: none;
          transform: none;
        }
        .toast.visible {
          transform: none;
        }
      }
    `;
  }

  render() {
    return html`
      <div
        class="toast ${this._type} ${this._visible ? "visible" : ""}"
        role="status"
        aria-live="polite"
      >
        <span class="dot" aria-hidden="true"></span>
        <span class="msg">${this._message}</span>
      </div>
    `;
  }
}

if (!customElements.get("sboom-toast")) {
  customElements.define("sboom-toast", SboomToast);
}

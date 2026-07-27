/**
 * SBoom — общий базовый класс с data-feed логикой.
 *
 * Наследуется И полностраничной панелью (`sboom-panel`), И Lovelace-карточкой
 * (`sboom-card`). Владеет источником состояния: push-подпиской `sboom/subscribe`
 * на координатор с деградацией до 5-сек поллинга, списком колонок
 * (`sboom/devices`) и адресацией по `entry_id` (мультирум), а также релеем
 * @seek/@toast от дочерних компонентов.
 *
 * Presentation-слой (раскладка, glow, idle, версия) остаётся в подклассе.
 * Точки расширения (хуки, по умолчанию no-op):
 *   _onStateApplied(state)  — после применения нового состояния (панель: glow);
 *   _onDeviceSelected(id)   — после переключения колонки (панель: сброс glow).
 */
import { LitElement } from "../lit-base.js";

export class SboomFeedBase extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      _state: { type: Object },
      _error: { type: String },
      _devices: { type: Array },
      _entryId: { type: String },
    };
  }

  constructor() {
    super();
    this._state = null;
    this._error = "";
    this._devices = [];
    this._entryId = null;
    this._devicesLoaded = false;
    this._unsub = null; // отписка от push-подписки
    this._subscribing = false;
    this._autoRefresh = null; // fallback-поллинг, если подписка недоступна
  }

  connectedCallback() {
    super.connectedCallback();
    this._ensureFeed();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._teardownFeed();
  }

  updated(changed) {
    if (changed.has("hass") && this.hass) this._ensureFeed();
  }

  // ── источник состояния: push-подписка (мгновенно) с fallback-поллингом ──
  async _ensureFeed() {
    if (!this.hass || this._unsub || this._autoRefresh || this._subscribing) {
      return;
    }
    this._subscribing = true;
    try {
      await this._loadDevicesOnce();
      this._unsub = await this.hass.connection.subscribeMessage(
        (msg) => this._applyState(msg),
        { type: "sboom/subscribe", entry_id: this._entryId }
      );
    } catch {
      // подписка недоступна — деградируем до поллинга
      this._autoRefresh = setInterval(() => this._fetchState(), 5000);
      this._fetchState();
    } finally {
      this._subscribing = false;
    }
  }

  async _loadDevicesOnce() {
    if (this._devicesLoaded) return;
    this._devicesLoaded = true;
    try {
      const res = await this.hass.callWS({ type: "sboom/devices" });
      this._devices = res?.devices || [];
    } catch {
      this._devices = [];
    }
    if (
      !this._entryId ||
      !this._devices.some((d) => d.entry_id === this._entryId)
    ) {
      this._entryId = this._devices[0]?.entry_id || null;
    }
  }

  _teardownFeed() {
    if (this._unsub) {
      this._unsub();
      this._unsub = null;
    }
    if (this._autoRefresh) {
      clearInterval(this._autoRefresh);
      this._autoRefresh = null;
    }
  }

  _applyState(state) {
    this._state = state;
    this._error = "";
    this._onStateApplied(state);
  }

  // хук presentation-слоя (панель: ambient glow из обложки)
  _onStateApplied(_state) {}

  async _fetchState() {
    if (!this.hass) return;
    try {
      this._applyState(
        await this.hass.callWS({ type: "sboom/state", entry_id: this._entryId })
      );
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  // переключение активной колонки (мультирум)
  _selectDevice(entryId) {
    if (entryId === this._entryId) return;
    this._entryId = entryId;
    this._state = null;
    this._teardownFeed();
    this._ensureFeed();
    this._onDeviceSelected(entryId);
  }

  _onDeviceSelected(_entryId) {}

  // доминирующий цвет обложки (Pillow на бэкенде, кеш по URL). Возвращает
  // hex-строку или null; запись в CSS-var — забота подкласса.
  async fetchCoverColor(url) {
    if (!url || !this.hass) return null;
    try {
      const resp = await this.hass.callWS({ type: "sboom/cover_color", url });
      return resp?.color || null;
    } catch {
      return null;
    }
  }

  // ── релей событий дочерних компонентов ─────────────────────────────────
  _onToast(e) {
    const toast = this.shadowRoot?.querySelector("sboom-toast");
    if (toast) toast.show(e.detail.message, e.detail.type);
  }

  async _onSeek(e) {
    if (!this.hass) return;
    try {
      await this.hass.callWS({
        type: "sboom/command",
        action: "seek",
        value: Math.round(e.detail.value),
        entry_id: this._entryId,
      });
    } catch (err) {
      this._onToast({
        detail: {
          message: `Ошибка перемотки: ${err.message || err}`,
          type: "error",
        },
      });
    }
  }
}

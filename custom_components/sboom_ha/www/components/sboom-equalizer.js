/**
 * SBoom — Эквалайзер + настройки колонки (мост к интеграции sberhome).
 *
 * Эквалайзер и тумблеры колонки живут в облачном канале интеграции `sberhome`
 * (сущности `switch`/`select`/`number`), а не в локальном канале `sboom_ha`.
 * Обе интеграции «поженены»: у HA-устройства колонки общий identifier
 * ``("sber_speaker", serial)``. По `serial` этот компонент находит устройство
 * и его сущности эквалайзера/настроек, рендерит их и шлёт HA-сервисы.
 *
 * Классификация сущностей эквалайзера — по служебным атрибутам, которые
 * sberhome кладёт в state (`eq_group`/`eq_role`/`eq_band_index`/`eq_frequency`),
 * а не по хрупкому матчингу имени. Если sberhome не установлена или у колонки
 * нет эквалайзера — компонент рендерит пустоту (host скрывает секцию).
 *
 * Реактивность: значения читаются из `hass.states` на каждый рендер, поэтому
 * optimistic-патчи sberhome и polling отражаются автоматически.
 */

import { LitElement, css, html } from "../lit-base.js";
import { tokens } from "./sboom-tokens.css.js";

// Общий identifier поженённого устройства (см. sberhome/const.py
// SPEAKER_MERGE_DOMAIN и sboom_ha/_entity_base.py).
const MERGE_DOMAIN = "sber_speaker";
const SBERHOME = "sberhome";


class SboomEqualizer extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      serial: { type: String },
      _expanded: { state: true },
    };
  }

  constructor() {
    super();
    this.hass = null;
    this.serial = null;
    this._expanded = false;
  }

  // ── discovery: serial → HA-устройства → сущности sberhome ────────────────
  // Колонка может быть представлена НЕСКОЛЬКИМИ HA-устройствами с общим
  // identifier (sber_speaker, serial): одно от sboom_ha (плеер), другое от
  // sberhome (настройки/эквалайзер). HA не всегда сливает их в одно, поэтому
  // собираем ВСЕ устройства с этим serial, а сущности sberhome ищем по всем.
  get _haDeviceIds() {
    const devices = this.hass?.devices;
    if (!devices || !this.serial) return [];
    const ids = [];
    for (const id in devices) {
      const idents = devices[id].identifiers || [];
      if (
        idents.some(
          (p) =>
            (p[0] === MERGE_DOMAIN || p[0] === SBERHOME) && p[1] === this.serial
        )
      ) {
        ids.push(id);
      }
    }
    return ids;
  }

  // Все сущности sberhome на устройстве(ах) колонки.
  get _sberhomeEntities() {
    const reg = this.hass?.entities;
    const devIds = new Set(this._haDeviceIds);
    if (!reg || !devIds.size) return [];
    const out = [];
    for (const eid in reg) {
      const e = reg[eid];
      if (devIds.has(e.device_id) && e.platform === SBERHOME) out.push(eid);
    }
    return out;
  }

  // Классификация: {enabled, preset, bands[], toggles[]} по атрибутам state.
  get _eq() {
    const res = { enabled: null, preset: null, bands: [], toggles: [] };
    for (const eid of this._sberhomeEntities) {
      const st = this.hass.states[eid];
      if (!st) continue;
      const a = st.attributes || {};
      const domain = eid.split(".")[0];
      if (a.eq_group) {
        if (a.eq_role === "enabled") res.enabled = eid;
        else if (a.eq_role === "preset") res.preset = eid;
        else if (a.eq_role === "band")
          res.bands.push({ eid, index: a.eq_band_index ?? 0, freq: a.eq_frequency });
        continue;
      }
      // Прочие настройки колонки: все управляемые switch/select (детский
      // режим, ограничения, звуковой отклик …). Диагностические/read-only
      // сущности (entity_category='diagnostic') пропускаем. Пустые select
      // (CARD-узлы без опций) и недоступные — тоже.
      if (domain === "switch" || domain === "select") {
        const reg = this.hass.entities?.[eid];
        const opts = a.options || [];
        const usable =
          reg?.entity_category !== "diagnostic" &&
          st.state !== "unavailable" &&
          (domain === "switch" || opts.length > 0);
        if (usable) res.toggles.push({ eid, domain });
      }
    }
    res.bands.sort((x, y) => x.index - y.index);
    return res;
  }

  get _hasContent() {
    const eq = this._eq;
    return !!(eq.enabled || eq.preset || eq.bands.length || eq.toggles.length);
  }

  // ── команды ──────────────────────────────────────────────────────────────
  async _callService(domain, service, data) {
    try {
      await this.hass.callService(domain, service, data);
    } catch (e) {
      this.dispatchEvent(
        new CustomEvent("toast", {
          detail: { message: `Ошибка: ${e.message || e}`, type: "error" },
          bubbles: true,
          composed: true,
        })
      );
    }
  }

  _toggleSwitch(eid) {
    const on = this.hass.states[eid]?.state === "on";
    this._callService("switch", on ? "turn_off" : "turn_on", { entity_id: eid });
  }

  _selectOption(eid, option) {
    this._callService("select", "select_option", { entity_id: eid, option });
  }

  _setBand(eid, value) {
    this._callService("number", "set_value", { entity_id: eid, value });
  }

  // ── render ────────────────────────────────────────────────────────────────
  render() {
    if (!this.hass || !this._hasContent) return html``;
    const eq = this._eq;
    return html`
      <section class="eq">
        <header @click=${() => (this._expanded = !this._expanded)}>
          <span class="title"><span class="ic">🎚️</span> Звук колонки</span>
          <span class="chev ${this._expanded ? "open" : ""}">▾</span>
        </header>
        ${this._expanded
          ? html`
              ${this._renderEqualizer(eq)}
              ${eq.toggles.length ? this._renderToggles(eq.toggles) : ""}
            `
          : ""}
      </section>
    `;
  }

  _renderEqualizer(eq) {
    if (!eq.enabled && !eq.bands.length && !eq.preset) return "";
    const enabledOn =
      eq.enabled && this.hass.states[eq.enabled]?.state === "on";
    return html`
      <div class="block">
        ${eq.enabled
          ? html`
              <div class="row toprow">
                <span class="label">Эквалайзер</span>
                <button
                  class="sw ${enabledOn ? "on" : ""}"
                  role="switch"
                  aria-checked=${enabledOn}
                  @click=${() => this._toggleSwitch(eq.enabled)}
                >
                  <span class="knob"></span>
                </button>
              </div>
            `
          : ""}
        ${eq.preset ? this._renderPresets(eq.preset) : ""}
        ${eq.bands.length
          ? html`<div class="bands ${enabledOn ? "" : "muted"}">
              ${eq.bands.map((b) => this._renderBand(b, eq.enabled ? enabledOn : true))}
            </div>`
          : ""}
      </div>
    `;
  }

  _renderPresets(eid) {
    const st = this.hass.states[eid];
    const options = st?.attributes?.options || [];
    const current = st?.state;
    return html`
      <div class="presets">
        ${options.map(
          (opt) => html`
            <button
              class="chip ${opt === current ? "active" : ""}"
              @click=${() => this._selectOption(eid, opt)}
            >
              ${opt}
            </button>
          `
        )}
      </div>
    `;
  }

  _renderBand(band, active) {
    const st = this.hass.states[band.eid];
    const a = st?.attributes || {};
    const value = Number(st?.state ?? 0);
    const min = a.min ?? -6;
    const max = a.max ?? 6;
    const step = a.step ?? 0.5;
    const label =
      band.freq != null ? this._fmtFreq(band.freq) : `#${band.index + 1}`;
    return html`
      <div class="band">
        <span class="gain">${value > 0 ? "+" : ""}${value}</span>
        <input
          type="range"
          class="slider"
          .min=${min}
          .max=${max}
          .step=${step}
          .value=${String(value)}
          ?disabled=${!active}
          @change=${(e) => this._setBand(band.eid, Number(e.target.value))}
        />
        <span class="freq">${label}</span>
      </div>
    `;
  }

  _renderToggles(toggles) {
    return html`
      <div class="block toggles">
        ${toggles.map((t) => {
          const st = this.hass.states[t.eid];
          const name =
            st?.attributes?.friendly_name || t.eid.split(".")[1];
          if (t.domain === "switch") {
            const on = st?.state === "on";
            return html`
              <div class="row">
                <span class="label">${name}</span>
                <button
                  class="sw ${on ? "on" : ""}"
                  role="switch"
                  aria-checked=${on}
                  @click=${() => this._toggleSwitch(t.eid)}
                >
                  <span class="knob"></span>
                </button>
              </div>
            `;
          }
          const options = st?.attributes?.options || [];
          return html`
            <div class="row">
              <span class="label">${name}</span>
              <select
                class="sel"
                @change=${(e) => this._selectOption(t.eid, e.target.value)}
              >
                ${options.map(
                  (opt) => html`<option ?selected=${opt === st?.state}>${opt}</option>`
                )}
              </select>
            </div>
          `;
        })}
      </div>
    `;
  }

  _fmtFreq(hz) {
    return hz >= 1000 ? `${(hz / 1000).toFixed(hz % 1000 ? 1 : 0)}k` : `${hz}`;
  }

  static get styles() {
    return [
      tokens,
      css`
        :host {
          display: block;
          font-family: var(--sb-disp);
          color: var(--sb-ink);
        }
        /* Без рамки и фона — компонент «сливается» с областью под обложкой. */
        .eq {
          background: none;
          border: none;
          overflow: hidden;
        }
        header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 10px 4px;
          cursor: pointer;
          user-select: none;
        }
        .title {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          font-weight: 600;
          font-size: 13px;
          letter-spacing: 0.4px;
          text-transform: uppercase;
          color: var(--sb-ink-dim);
        }
        .ic {
          font-size: 15px;
        }
        .chev {
          color: var(--sb-ink-faint);
          font-size: 12px;
          transition: transform 0.2s ease;
        }
        .chev.open {
          transform: rotate(180deg);
        }
        .block {
          padding: 4px 4px 14px;
        }
        .block + .block {
          border-top: 1px solid var(--sb-line);
        }
        .row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          min-height: 40px;
        }
        .toprow {
          margin-bottom: 4px;
        }
        .label {
          font-size: 13px;
          color: var(--sb-ink-dim);
        }
        /* пресеты — чипы */
        .presets {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin: 10px 0 4px;
        }
        .chip {
          border: 1px solid var(--sb-line);
          background: transparent;
          color: var(--sb-ink-dim);
          border-radius: 999px;
          padding: 6px 14px;
          font-size: 12px;
          font-family: inherit;
          cursor: pointer;
          transition: all 0.15s ease;
        }
        .chip:hover {
          color: var(--sb-ink);
          border-color: var(--sb-ink-faint);
        }
        .chip.active {
          background: var(--sb-accent);
          border-color: var(--sb-accent);
          color: #fff;
        }
        /* полосы — вертикальные слайдеры */
        .bands {
          display: flex;
          justify-content: space-between;
          gap: 6px;
          margin-top: 14px;
          transition: opacity 0.2s ease;
        }
        .bands.muted {
          opacity: 0.4;
        }
        .band {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 8px;
          flex: 1;
        }
        .gain {
          font-size: 11px;
          font-variant-numeric: tabular-nums;
          color: var(--sb-ink);
          font-family: var(--sb-mono);
        }
        .slider {
          writing-mode: vertical-lr;
          direction: rtl;
          width: 6px;
          height: 96px;
          accent-color: var(--sb-accent);
          cursor: pointer;
        }
        .freq {
          font-size: 10px;
          color: var(--sb-ink-faint);
          font-family: var(--sb-mono);
        }
        /* тумблеры */
        .toggles .row + .row {
          border-top: 1px solid var(--sb-line);
        }
        .sw {
          position: relative;
          width: 42px;
          height: 24px;
          border-radius: 999px;
          border: none;
          background: var(--sb-elev-2);
          cursor: pointer;
          transition: background 0.15s ease;
          flex: none;
        }
        .sw.on {
          background: var(--sb-accent);
        }
        .knob {
          position: absolute;
          top: 3px;
          left: 3px;
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: #fff;
          transition: transform 0.15s ease;
        }
        .sw.on .knob {
          transform: translateX(18px);
        }
        .sel {
          background: var(--sb-elev-2);
          color: var(--sb-ink);
          border: 1px solid var(--sb-line);
          border-radius: var(--sb-radius-sm);
          padding: 6px 10px;
          font-family: inherit;
          font-size: 13px;
          cursor: pointer;
        }
      `,
    ];
  }
}

if (!customElements.get("sboom-equalizer")) {
  customElements.define("sboom-equalizer", SboomEqualizer);
}

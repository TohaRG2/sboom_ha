/**
 * Self-contained lit 3.x re-export.
 *
 * Раньше компоненты панели получали `LitElement`, `html` и `css` через
 * trick `Object.getPrototypeOf(customElements.get("ha-panel-lovelace"))`.
 * Этот подход зависит от того, был ли к моменту загрузки панели
 * зарегистрирован `ha-panel-lovelace` с активным LitElement-prototype.
 *
 * На современном HA frontend эти символы уже не проксируются через
 * prototype — hack рассыпается в "чистых" установках без дополнительных
 * HACS-карт, которые побочно их гидратируют.
 *
 * Теперь: статический vendored bundle `vendor/lit.js` (~16 КБ,
 * self-contained) — работает одинаково у любого пользователя, без
 * зависимости от окружения.
 */
export {
  LitElement,
  html,
  css,
  ReactiveElement,
  CSSResult,
  unsafeCSS,
  nothing,
  noChange,
  render,
  svg,
  mathml,
} from "./vendor/lit.js";

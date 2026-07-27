/**
 * SBoom — общий блок дизайн-токенов `--sb-*`.
 *
 * Единственный источник палитры для панели И Lovelace-карточки. Токены
 * привязаны к нативным переменным темы Home Assistant (адаптируются к
 * светлой/тёмной/кастомной теме) с жёсткими фолбэками. Компоненты
 * ТОЛЬКО потребляют `--sb-*`, никогда не переопределяют (см. DESIGN_SPEC.md).
 *
 * Токены не пересекают границу shadow-host, поэтому каждый host (панель,
 * карточка) обязан задекларировать этот блок на своём `:host`:
 *     static get styles() { return [tokens, css`… свои стили …`]; }
 */
import { css } from "../lit-base.js";

export const tokens = css`
  :host {
    --sb-stage: var(--primary-background-color, #111418);
    --sb-elev: var(--card-background-color, var(--ha-card-background, #1c1f26));
    --sb-elev-2: var(
      --secondary-background-color,
      var(--state-icon-hover-color, #2a2f3a)
    );
    --sb-line: var(--divider-color, rgba(127, 127, 127, 0.2));
    --sb-ink: var(--primary-text-color, #e8eaed);
    --sb-ink-dim: var(--secondary-text-color, rgba(232, 234, 237, 0.65));
    --sb-ink-faint: var(--disabled-text-color, rgba(232, 234, 237, 0.42));
    --sb-accent: var(--primary-color, #03a9f4);
    --sb-glow: var(--primary-color, #03a9f4);
    --sb-glow-soft: color-mix(in srgb, var(--sb-glow) 20%, transparent);
    --sb-like: var(--primary-color, #ff5c8a);
    --sb-radius: var(--ha-card-border-radius, 12px);
    --sb-radius-sm: 8px;
    --sb-gap: 16px;
    --sb-disp: "SF Pro Display", "Inter", system-ui, sans-serif;
    --sb-mono: ui-monospace, "SF Mono", "Roboto Mono", monospace;
  }
`;

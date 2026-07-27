/**
 * SBoom — Browse (единый drill-down браузер каталога Звука).
 *
 * Заменяет вкладки «Очередь / Поиск» одним цельным навигационным стеком.
 * Персистентная строка поиска сверху; ниже — активное представление из
 * `_stack` (последний элемент активен). `_stack[0]` всегда `{kind:"queue"}`.
 *
 *   queue  → строки очереди (<sboom-track-row>), текущий трек подсвечен.
 *   search → секции Исполнители/Альбомы/Треки/Плейлисты (<sboom-tile>/<sboom-track-row>).
 *   artist → шапка исполнителя + «Популярное» (треки) + «Дискография» (тайлы).
 *   album  → шапка альбома + нумерованный треклист.
 *
 * Все WS-вызовы (`sboom/queue|search|artist|release|play`) живут здесь;
 * примитивы <sboom-track-row>/<sboom-tile> только эмитят `@open` (bubbles),
 * а контейнер решает: играть трек / плейлист или drill-down в artist/album.
 * ВАЖНО: WS-поле идентификатора — `content_id` (не `id`, оно занято HA).
 *
 * Компонент потребляет дизайн-токены --sb-* (заданы на host, см. DESIGN_SPEC.md)
 * и НЕ переопределяет их. Тёмная «сцена»: карточка --sb-elev, hover --sb-elev-2,
 * текст --sb-ink / --sb-ink-dim / --sb-ink-faint, акцент --sb-accent.
 */

import { LitElement, html, css, nothing } from "../lit-base.js";
// Примитивы sboom-track-row / sboom-tile прелоадятся host'ом (sboom-panel.js)
// с cache-bust ?v — здесь используем только теги (регистрируются глобально).

const DEBOUNCE_MS = 350;
const SEARCH_LIMIT = 8;
const TOP_TRACKS_MAX = 8;

class SboomBrowse extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entryId: { type: String },
      currentTrackId: { type: String },
      _stack: { type: Array, state: true },
      _query: { type: String, state: true },
      _queue: { type: Array, state: true },
      _queueLoading: { type: Boolean, state: true },
      _navLoading: { type: Boolean, state: true },
      _pendingId: { type: String, state: true },
    };
  }

  constructor() {
    super();
    this.hass = null;
    this.entryId = null;
    this.currentTrackId = null;
    this._stack = [{ kind: "queue" }];
    this._query = "";
    this._queue = [];
    this._queueLoading = false;
    this._queueFetched = false;
    this._navLoading = false;
    this._debounce = null;
    this._reqId = 0;
    this._animKey = "";
    this._pendingId = null; // оптимистичная подсветка запускаемого трека
    this._pendingTimer = null;
    this._lastPlayId = null; // дедуп двойных play-команд
    this._lastPlayAt = 0;
  }

  connectedCallback() {
    super.connectedCallback();
    if (this.hass) this._fetchQueue();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._debounce) clearTimeout(this._debounce);
    clearTimeout(this._queueSync);
    clearTimeout(this._pendingTimer);
  }

  shouldUpdate(changed) {
    // HA подменяет объект `hass` на КАЖДЫЙ state_changed, но render() его не
    // читает (нужен только для callWS — а свойство обновляется независимо от
    // ре-рендера). Если изменился ТОЛЬКО hass — не ре-рендерим (иначе списки
    // пересобираются вхолостую и «съедают» тапы). Первый приход hass (пока
    // очередь не загружена) пропускаем — он нужен для стартовой загрузки.
    if (changed.size === 1 && changed.has("hass")) return !this._queueFetched;
    return true;
  }

  updated(changed) {
    if (changed.has("entryId")) {
      // Смена колонки — сброс в корень + перечитать очередь.
      this._stack = [{ kind: "queue" }];
      this._query = "";
      this._queue = [];
      this._queueFetched = false;
      if (this.hass) this._fetchQueue();
    } else if (
      changed.has("hass") &&
      this.hass &&
      !this._queueFetched &&
      !this._queueLoading
    ) {
      // HA заменяет объект hass на каждый state_changed — тянем очередь один
      // раз на колонку (флаг), а не пока пусто (иначе спам backend'а).
      this._fetchQueue();
    }
    // Сменился текущий трек. Перечитываем очередь ТОЛЬКО если новый трек ушёл
    // из неё (реальная смена контекста) — иначе не трогаем список (подсветка
    // сдвинется сама), чтобы не пересобирать DOM под пальцем пользователя.
    if (
      changed.has("currentTrackId") &&
      this.hass &&
      this._active.kind === "queue" &&
      this.currentTrackId != null
    ) {
      const inQueue = (this._queue || []).some(
        (t) => String(t.track_id) === String(this.currentTrackId)
      );
      if (!inQueue) {
        clearTimeout(this._queueSync);
        this._queueSync = setTimeout(() => {
          this._queueFetched = false;
          this._fetchQueue();
        }, 2000);
      }
    }
    // Колонка подтвердила переключение на запускаемый трек — снимаем pending.
    if (
      changed.has("currentTrackId") &&
      this._pendingId != null &&
      String(this.currentTrackId) === String(this._pendingId)
    ) {
      this._pendingId = null;
      clearTimeout(this._pendingTimer);
    }
    // Плавный fade при смене активного представления.
    const key = this._activeKey();
    if (key !== this._animKey) {
      this._animKey = key;
      this._animateView();
    }
  }

  // ── Навигационный стек ─────────────────────────────────────────
  get _active() {
    return this._stack[this._stack.length - 1] || { kind: "queue" };
  }

  _activeKey() {
    const v = this._active;
    return `${this._stack.length}:${v.kind}:${v.title || v.query || ""}`;
  }

  _push(view) {
    this._stack = [...this._stack, view];
  }

  _replaceTop(patch) {
    const next = this._stack.slice();
    next[next.length - 1] = { ...next[next.length - 1], ...patch };
    this._stack = next;
  }

  _pop() {
    if (this._stack.length <= 1) return;
    this._stack = this._stack.slice(0, -1);
    // Синхронизируем строку поиска с тем, куда вернулись.
    const top = this._active;
    this._query = top.kind === "search" ? top.query || "" : "";
    // Вернулись к очереди — перечитываем на актуальную (пока браузили,
    // текущий трек/контекст мог смениться).
    if (top.kind === "queue" && this.hass) {
      this._queueFetched = false;
      this._fetchQueue();
    }
  }

  _animateView() {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    this.updateComplete.then(() => {
      const el = this.renderRoot?.querySelector(".view");
      if (!el || !el.animate) return;
      el.animate(
        [
          { opacity: 0, transform: "translateY(6px)" },
          { opacity: 1, transform: "none" },
        ],
        { duration: 220, easing: "cubic-bezier(0.22,0.61,0.36,1)" }
      );
    });
  }

  // ── Данные: очередь ────────────────────────────────────────────
  async _fetchQueue() {
    if (!this.hass) return;
    this._queueLoading = true;
    this._queueFetched = true; // тянем один раз на колонку (см. updated)
    try {
      const resp = await this.hass.callWS({
        type: "sboom/queue",
        entry_id: this.entryId,
      });
      this._queue = resp?.queue || [];
    } catch (err) {
      this._queue = [];
      this._toast(`Ошибка очереди: ${err.message || err}`, "error");
    } finally {
      this._queueLoading = false;
    }
  }

  // ── Данные: поиск ──────────────────────────────────────────────
  _onInput(e) {
    this._query = e.target.value;
    if (this._debounce) clearTimeout(this._debounce);
    const q = this._query.trim();
    if (!q) {
      // Очистка: если верхний view — search, возвращаемся назад.
      if (this._active.kind === "search") this._pop();
      return;
    }
    this._debounce = setTimeout(() => this._runSearch(q), DEBOUNCE_MS);
  }

  _onSubmit(e) {
    e.preventDefault();
    if (this._debounce) clearTimeout(this._debounce);
    const q = this._query.trim();
    if (q) this._runSearch(q);
  }

  _clearSearch() {
    if (this._debounce) clearTimeout(this._debounce);
    this._query = "";
    if (this._active.kind === "search") this._pop();
    const input = this.renderRoot?.querySelector("input");
    if (input) input.focus();
  }

  async _runSearch(query) {
    if (!this.hass) return;
    // Верхний view не search → push; иначе обновляем существующий.
    if (this._active.kind === "search") {
      this._replaceTop({ query, loading: true });
    } else {
      this._push({
        kind: "search",
        query,
        result: null,
        loading: true,
        searched: false,
      });
    }
    const reqId = ++this._reqId;
    try {
      const resp = await this.hass.callWS({
        type: "sboom/search",
        query,
        limit: SEARCH_LIMIT,
      });
      if (reqId !== this._reqId) return; // устаревший ответ
      if (this._active.kind !== "search") return; // ушли со страницы
      this._replaceTop({ result: resp || {}, loading: false, searched: true });
    } catch (err) {
      if (reqId !== this._reqId) return;
      if (this._active.kind === "search") {
        this._replaceTop({ result: null, loading: false, searched: true });
      }
      this._toast(`Ошибка поиска: ${err.message || err}`, "error");
    }
  }

  // ── Действия: @open от примитивов ──────────────────────────────
  _onOpen(e) {
    const detail = e.detail || {};
    console.info(
      "[sboom] browse _onOpen",
      detail.track ? "track:" + detail.track.title : "",
      detail.item ? "item:" + detail.item.title + "/" + detail.item.type : ""
    );
    if (detail.track) {
      this._playTrack(detail.track);
    } else if (detail.item) {
      this._onTileOpen(detail.item);
    }
  }

  async _playTrack(track) {
    const contentId = String(track.id ?? track.track_id ?? "");
    console.info("[sboom] browse _playTrack", track.title, "contentId=", contentId);
    if (!contentId) return;
    // Оптимистично подсвечиваем выбранный трек и честно сообщаем — колонка
    // грузит трек с заметной задержкой (несколько секунд).
    this._pendingId = contentId;
    clearTimeout(this._pendingTimer);
    this._pendingTimer = setTimeout(() => {
      this._pendingId = null;
    }, 25000);
    this._toast(track.title ? `Запускаю «${track.title}»…` : "Запускаю…", "info");
    await this._play(contentId, track.pt || "track", true);
  }

  _onTileOpen(item) {
    switch (item.type) {
      case "artist":
        this._drillArtist(item);
        break;
      case "release":
        this._drillAlbum(item);
        break;
      case "playlist":
        this._play(String(item.id), item.pt || "playlist");
        break;
      default:
        this._play(String(item.id), item.pt || "track");
    }
  }

  async _drillArtist(item) {
    if (!this.hass || item?.id == null) return;
    this._navLoading = true;
    try {
      const resp = await this.hass.callWS({
        type: "sboom/artist",
        content_id: String(item.id),
      });
      this._push({ kind: "artist", data: resp || {}, title: resp?.title || "" });
    } catch (err) {
      this._toast(`Ошибка: ${err.message || err}`, "error");
    } finally {
      this._navLoading = false;
    }
  }

  async _drillAlbum(item) {
    if (!this.hass || item?.id == null) return;
    this._navLoading = true;
    try {
      const resp = await this.hass.callWS({
        type: "sboom/release",
        content_id: String(item.id),
      });
      this._push({ kind: "album", data: resp || {}, title: resp?.title || "" });
    } catch (err) {
      this._toast(`Ошибка: ${err.message || err}`, "error");
    } finally {
      this._navLoading = false;
    }
  }

  async _play(contentId, pt, silent) {
    // Дедуп: один пользовательский клик может породить 2 события (pointer/
    // click + ре-рендер) — не шлём одинаковую play-команду дважды за 2с,
    // иначе колонка получает бэклог deeplink'ов.
    const now = Date.now();
    if (this._lastPlayId === contentId && now - this._lastPlayAt < 2000) {
      console.info("[sboom] _play DEDUPED", contentId);
      return;
    }
    this._lastPlayId = contentId;
    this._lastPlayAt = now;
    console.info("[sboom] browse _play → WS", { contentId, pt: pt || "track", entry_id: this.entryId, hasHass: !!this.hass });
    if (!this.hass || !contentId) {
      console.warn("[sboom] _play aborted: no hass or contentId");
      return;
    }
    try {
      const res = await this.hass.callWS({
        type: "sboom/play",
        content_id: contentId,
        pt: pt || "track",
        entry_id: this.entryId,
      });
      console.info("[sboom] _play WS result", res);
      if (!silent) this._toast("Запущено", "success");
    } catch (err) {
      console.error("[sboom] _play WS error", err);
      this._toast(`Ошибка: ${err.message || err}`, "error");
    }
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

  // ── Хелперы отрисовки ──────────────────────────────────────────
  _isCurrent(id) {
    const s = String(id);
    return (
      (this.currentTrackId != null && s === String(this.currentTrackId)) ||
      (this._pendingId != null && s === String(this._pendingId))
    );
  }

  _asItem(raw, type, pt) {
    // Нормализация raw-данных из WS в контракт <sboom-tile>.
    return {
      id: raw.id,
      title: raw.title,
      subtitle: raw.subtitle,
      cover_url: raw.cover_url,
      type,
      pt: raw.pt || pt,
    };
  }

  _activeTitle() {
    const v = this._active;
    if (v.kind === "queue") return "Очередь";
    if (v.kind === "search") return v.query || "Поиск";
    return v.title || "";
  }

  static get styles() {
    return css`
      :host {
        display: block;
        color: var(--sb-ink);
        font-family: system-ui, "Segoe UI", Roboto, sans-serif;
      }

      .card {
        position: relative;
      }

      /* ── Строка поиска (персистентная) ───────────────────────── */
      form {
        position: relative;
        display: block;
      }
      .search-icon,
      .clear {
        position: absolute;
        top: 50%;
        transform: translateY(-50%);
        display: inline-flex;
        color: var(--sb-ink-faint);
      }
      .search-icon {
        left: 14px;
        pointer-events: none;
      }
      .search-icon svg,
      .clear svg {
        width: 18px;
        height: 18px;
        display: block;
        fill: none;
        stroke: currentColor;
        stroke-width: 2;
        stroke-linecap: round;
      }
      input {
        width: 100%;
        box-sizing: border-box;
        padding: 12px 44px;
        border: 1px solid var(--sb-line);
        border-radius: var(--sb-radius-sm);
        background: var(--sb-stage, var(--sb-elev));
        color: var(--sb-ink);
        font-size: 15px;
        font-family: inherit;
        transition: border-color 0.18s ease, box-shadow 0.18s ease;
      }
      input::placeholder {
        color: var(--sb-ink-faint);
      }
      input:hover {
        border-color: color-mix(in srgb, var(--sb-ink) 16%, transparent);
      }
      input:focus {
        outline: none;
        border-color: var(--sb-accent);
        box-shadow: 0 0 0 3px var(--sb-glow-soft);
      }
      input::-webkit-search-cancel-button {
        -webkit-appearance: none;
        appearance: none;
      }
      .clear {
        right: 8px;
        width: 30px;
        height: 30px;
        align-items: center;
        justify-content: center;
        border: none;
        border-radius: 50%;
        background: transparent;
        color: var(--sb-ink-dim);
        cursor: pointer;
        transition: background 0.16s ease, color 0.16s ease;
      }
      .clear:hover {
        background: var(--sb-elev-2);
        color: var(--sb-ink);
      }

      /* ── Слим-хедер «назад» ──────────────────────────────────── */
      .nav {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 12px;
      }
      .back {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        flex: none;
        border: 1px solid var(--sb-line);
        border-radius: var(--sb-radius-sm);
        background: transparent;
        color: var(--sb-ink-dim);
        cursor: pointer;
        transition: color 0.15s ease, background 0.15s ease,
          border-color 0.15s ease;
      }
      .back:hover {
        color: var(--sb-ink);
        background: var(--sb-elev-2);
      }
      .back svg {
        width: 18px;
        height: 18px;
        fill: none;
        stroke: currentColor;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
      }
      .nav-title {
        min-width: 0;
        font-size: 15px;
        font-weight: 600;
        color: var(--sb-ink);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      /* ── Обёртка активного view ──────────────────────────────── */
      .view {
        position: relative;
        margin-top: 14px;
      }

      /* ── Заголовок view (queue) ──────────────────────────────── */
      .view-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 8px;
      }
      .view-head h2 {
        margin: 0;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        color: var(--sb-ink-faint);
      }
      .view-head h2 .count {
        color: var(--sb-ink-dim);
      }
      .refresh {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 30px;
        height: 30px;
        border: 1px solid var(--sb-line);
        border-radius: var(--sb-radius-sm);
        background: transparent;
        color: var(--sb-ink-dim);
        cursor: pointer;
        transition: color 0.15s ease, background 0.15s ease;
      }
      .refresh:hover {
        color: var(--sb-ink);
        background: var(--sb-elev-2);
      }
      .refresh svg {
        width: 16px;
        height: 16px;
        fill: none;
        stroke: currentColor;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
      }
      .refresh.spinning svg {
        animation: spin 0.9s linear infinite;
      }

      /* ── Списки строк ────────────────────────────────────────── */
      .rows {
        display: flex;
        flex-direction: column;
        gap: 2px;
        max-height: 460px;
        overflow-y: auto;
        scrollbar-width: thin;
        scrollbar-color: var(--sb-line) transparent;
      }
      .rows::-webkit-scrollbar {
        width: 8px;
      }
      .rows::-webkit-scrollbar-thumb {
        background: var(--sb-line);
        border-radius: 8px;
        border: 2px solid transparent;
        background-clip: padding-box;
      }
      sboom-track-row {
        display: block;
      }

      /* ── Секции ──────────────────────────────────────────────── */
      .sections {
        display: flex;
        flex-direction: column;
        gap: 22px;
      }
      .section-title {
        margin: 0 0 10px;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        color: var(--sb-ink-faint);
      }

      /* ── Горизонтальный shelf тайлов ─────────────────────────── */
      .shelf {
        display: flex;
        gap: 12px;
        overflow-x: auto;
        padding-bottom: 6px;
        scrollbar-width: thin;
        scrollbar-color: var(--sb-line) transparent;
        scroll-snap-type: x proximity;
      }
      .shelf::-webkit-scrollbar {
        height: 6px;
      }
      .shelf::-webkit-scrollbar-thumb {
        background: var(--sb-line);
        border-radius: 3px;
      }
      .shelf sboom-tile {
        flex: 0 0 auto;
        scroll-snap-align: start;
      }

      /* ── Шапка сущности (artist / album) ─────────────────────── */
      .entity {
        display: flex;
        gap: 18px;
        align-items: flex-end;
        margin-bottom: 22px;
      }
      .entity-art {
        width: 132px;
        height: 132px;
        flex: none;
        object-fit: cover;
        background: var(--sb-elev-2);
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
      }
      .entity-art.round {
        border-radius: 50%;
      }
      .entity-art.square {
        border-radius: var(--sb-radius-sm);
      }
      .entity-art.ph {
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--sb-ink-faint);
      }
      .entity-art.ph svg {
        width: 44px;
        height: 44px;
      }
      .entity-meta {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding-bottom: 2px;
      }
      .entity-kind {
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        color: var(--sb-ink-faint);
      }
      .entity-name {
        margin: 0;
        font-family: var(--sb-disp, "SF Pro Display", "Inter", system-ui,
            sans-serif);
        font-size: 26px;
        font-weight: 700;
        letter-spacing: -0.02em;
        line-height: 1.1;
        color: var(--sb-ink);
        overflow: hidden;
        text-overflow: ellipsis;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
      }
      .entity-sub {
        font-size: 13px;
        color: var(--sb-ink-dim);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .play-pill {
        align-self: flex-start;
        margin-top: 6px;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 9px 18px 9px 14px;
        border: none;
        border-radius: 999px;
        background: var(--sb-accent);
        color: var(--sb-stage, #0b0b0f);
        font-size: 14px;
        font-weight: 700;
        font-family: inherit;
        cursor: pointer;
        box-shadow: 0 6px 18px var(--sb-glow-soft);
        transition: transform 0.15s ease, filter 0.15s ease;
      }
      .play-pill:hover {
        transform: translateY(-1px);
        filter: brightness(1.08);
      }
      .play-pill:active {
        transform: translateY(0);
      }
      .play-pill svg {
        width: 16px;
        height: 16px;
        fill: currentColor;
      }

      /* ── Нумерованный треклист (album) ───────────────────────── */
      .tracklist sboom-track-row {
        display: block;
      }

      /* ── Состояния ───────────────────────────────────────────── */
      .empty {
        padding: 26px 8px 30px;
        text-align: center;
        color: var(--sb-ink-faint);
        font-size: 13px;
      }
      .spinner {
        margin: 30px auto;
        width: 26px;
        height: 26px;
        border-radius: 50%;
        border: 2.5px solid var(--sb-line);
        border-top-color: var(--sb-accent);
        animation: spin 0.8s linear infinite;
      }
      .overlay {
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: var(--sb-radius);
        background: color-mix(in srgb, var(--sb-stage, #0b0b0f) 55%, transparent);
        z-index: 5;
      }

      @keyframes spin {
        to {
          transform: rotate(360deg);
        }
      }

      /* ── Доступность / motion ────────────────────────────────── */
      :focus-visible {
        outline: 2px solid var(--sb-accent);
        outline-offset: 2px;
      }
      input:focus-visible {
        outline: none;
      }
      @media (prefers-reduced-motion: reduce) {
        input,
        .back,
        .refresh,
        .clear,
        .play-pill {
          transition: none;
        }
        .play-pill:hover {
          transform: none;
        }
        .refresh.spinning svg,
        .spinner {
          animation: none;
        }
      }

      @media (max-width: 480px) {
        .card {
          padding: 12px 10px;
        }
        .entity {
          gap: 14px;
        }
        .entity-art {
          width: 96px;
          height: 96px;
        }
        .entity-name {
          font-size: 21px;
        }
        .rows {
          max-height: 60vh;
        }
      }
    `;
  }

  // ── Представления ──────────────────────────────────────────────
  _renderQueue() {
    const n = this._queue.length;
    return html`
      <div class="view-head">
        <h2>
          Очередь${n ? html` <span class="count">· ${n}</span>` : nothing}
        </h2>
        <button
          class="refresh ${this._queueLoading ? "spinning" : ""}"
          @click=${this._fetchQueue}
          aria-label="Обновить очередь"
          title="Обновить очередь"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
        </button>
      </div>
      ${n === 0
        ? html`<div class="empty">
            ${this._queueLoading ? "Загрузка…" : "Очередь пуста"}
          </div>`
        : html`<div class="rows">
            ${this._queue.map(
              (t) => html`<sboom-track-row
                .track=${t}
                ?active=${this._isCurrent(t.track_id)}
              ></sboom-track-row>`
            )}
          </div>`}
    `;
  }

  _renderTileShelf(items, shape, type, pt) {
    return html`<div class="shelf">
      ${items.map(
        (raw) => html`<sboom-tile
          .item=${this._asItem(raw, type, pt)}
          .shape=${shape}
        ></sboom-tile>`
      )}
    </div>`;
  }

  _renderSearch(view) {
    if (view.loading && !view.result) {
      return html`<div class="spinner" role="status" aria-label="Поиск…"></div>`;
    }
    const r = view.result || {};
    const artists = Array.isArray(r.artists) ? r.artists : [];
    const releases = Array.isArray(r.releases) ? r.releases : [];
    const tracks = Array.isArray(r.tracks) ? r.tracks : [];
    const playlists = Array.isArray(r.playlists) ? r.playlists : [];
    const hasAny =
      artists.length || releases.length || tracks.length || playlists.length;

    if (view.searched && !hasAny) {
      return html`<div class="empty">Ничего не найдено</div>`;
    }
    if (!hasAny) return nothing;

    return html`
      <div class="sections">
        ${artists.length
          ? html`<section>
              <h3 class="section-title">Исполнители</h3>
              ${this._renderTileShelf(artists, "round", "artist", "artist")}
            </section>`
          : nothing}
        ${releases.length
          ? html`<section>
              <h3 class="section-title">Альбомы</h3>
              ${this._renderTileShelf(releases, "square", "release", "release")}
            </section>`
          : nothing}
        ${tracks.length
          ? html`<section>
              <h3 class="section-title">Треки</h3>
              <div class="rows">
                ${tracks.map(
                  (t) => html`<sboom-track-row
                    .track=${t}
                    ?active=${this._isCurrent(t.id ?? t.track_id)}
                  ></sboom-track-row>`
                )}
              </div>
            </section>`
          : nothing}
        ${playlists.length
          ? html`<section>
              <h3 class="section-title">Плейлисты</h3>
              ${this._renderTileShelf(
                playlists,
                "square",
                "playlist",
                "playlist"
              )}
            </section>`
          : nothing}
      </div>
    `;
  }

  _renderArtist(data) {
    const tracks = (Array.isArray(data.tracks) ? data.tracks : []).slice(
      0,
      TOP_TRACKS_MAX
    );
    const releases = Array.isArray(data.releases) ? data.releases : [];
    return html`
      <header class="entity">
        ${data.cover_url
          ? html`<img
              class="entity-art round"
              src=${data.cover_url}
              alt=""
              loading="lazy"
            />`
          : html`<div class="entity-art round ph" aria-hidden="true">
              ${this._noteIcon()}
            </div>`}
        <div class="entity-meta">
          <div class="entity-kind">Исполнитель</div>
          <h1 class="entity-name">${data.title || "—"}</h1>
          <button
            class="play-pill"
            @click=${() => this._play(String(data.id), "artist")}
            aria-label="Слушать исполнителя"
          >
            ${this._playIcon()} Слушать
          </button>
        </div>
      </header>
      ${tracks.length
        ? html`<section>
            <h3 class="section-title">Популярное</h3>
            <div class="rows">
              ${tracks.map(
                (t) => html`<sboom-track-row
                  .track=${t}
                  ?active=${this._isCurrent(t.id ?? t.track_id)}
                ></sboom-track-row>`
              )}
            </div>
          </section>`
        : nothing}
      ${releases.length
        ? html`<section>
            <h3 class="section-title">Дискография</h3>
            ${this._renderTileShelf(releases, "square", "release", "release")}
          </section>`
        : nothing}
      ${!tracks.length && !releases.length
        ? html`<div class="empty">Нет данных исполнителя</div>`
        : nothing}
    `;
  }

  _renderAlbum(data) {
    const tracks = Array.isArray(data.tracks) ? data.tracks : [];
    const sub = [data.artist, data.year].filter(Boolean).join(" · ");
    return html`
      <header class="entity">
        ${data.cover_url
          ? html`<img
              class="entity-art square"
              src=${data.cover_url}
              alt=""
              loading="lazy"
            />`
          : html`<div class="entity-art square ph" aria-hidden="true">
              ${this._noteIcon()}
            </div>`}
        <div class="entity-meta">
          <div class="entity-kind">Альбом</div>
          <h1 class="entity-name">${data.title || "—"}</h1>
          ${sub ? html`<div class="entity-sub">${sub}</div>` : nothing}
          <button
            class="play-pill"
            @click=${() => this._play(String(data.id), "release")}
            aria-label="Слушать альбом"
          >
            ${this._playIcon()} Слушать
          </button>
        </div>
      </header>
      ${tracks.length
        ? html`<div class="tracklist rows">
            ${tracks.map(
              (t, i) => html`<sboom-track-row
                .track=${t}
                .index=${i + 1}
                ?active=${this._isCurrent(t.id ?? t.track_id)}
              ></sboom-track-row>`
            )}
          </div>`
        : html`<div class="empty">Треклист пуст</div>`}
    `;
  }

  _renderActive() {
    const v = this._active;
    if (v.kind === "search") return this._renderSearch(v);
    if (v.kind === "artist") return this._renderArtist(v.data || {});
    if (v.kind === "album") return this._renderAlbum(v.data || {});
    return this._renderQueue();
  }

  // ── SVG-иконки ─────────────────────────────────────────────────
  _playIcon() {
    return html`<svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8 5v14l11-7z" />
    </svg>`;
  }

  _noteIcon() {
    return html`<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M9 18V6l10-2v12"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <circle cx="6" cy="18" r="3" stroke="currentColor" stroke-width="1.6" />
      <circle cx="16" cy="16" r="3" stroke="currentColor" stroke-width="1.6" />
    </svg>`;
  }

  render() {
    const drilled = this._stack.length > 1;
    return html`
      <div class="card" @open=${this._onOpen}>
        <form @submit=${this._onSubmit} role="search">
          <span class="search-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.2-3.2" />
            </svg>
          </span>
          <input
            type="search"
            aria-label="Поиск в каталоге"
            placeholder="Поиск треков, исполнителей, плейлистов…"
            .value=${this._query}
            @input=${this._onInput}
            autocomplete="off"
            spellcheck="false"
          />
          ${this._query
            ? html`<button
                class="clear"
                type="button"
                aria-label="Очистить поиск"
                @click=${this._clearSearch}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>`
            : nothing}
        </form>

        ${drilled
          ? html`<div class="nav">
              <button class="back" @click=${this._pop} aria-label="Назад">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M15 18l-6-6 6-6" />
                </svg>
              </button>
              <span class="nav-title">${this._activeTitle()}</span>
            </div>`
          : nothing}

        <div class="view">
          ${this._renderActive()}
          ${this._navLoading
            ? html`<div class="overlay" role="status" aria-label="Загрузка">
                <div class="spinner"></div>
              </div>`
            : nothing}
        </div>
      </div>
    `;
  }
}

if (!customElements.get("sboom-browse"))
  customElements.define("sboom-browse", SboomBrowse);

# SBoom Panel — Design Spec (locked)

Премиум-панель управления умной колонкой **SberBoom** внутри Home Assistant.
Lit 3, no-build, vendored lit (`../lit-base.js`). Единый источник дизайн-токенов
и контрактов для всех компонентов. **Ничего не менять по своему вкусу — только
следовать спеке.**

## Тезис дизайна

Колонка SberBoom — физический объект со **световым LED-кольцом**. Сигнатура
панели: **UI «носит» цвет того, что играет** — из обложки текущего трека
извлекается доминирующий цвет и разливается ambient-градиентом за hero, повторяя
свечение кольца колонки. Пока играет — свечение мягко «дышит» (6s), на паузе —
статично и приглушено. `prefers-reduced-motion` → без анимации.

Это НЕ дефолт «near-black + один кислотный акцент»: база — сине-чёрная (не
чистый #000), акцент **многоцветный и производный от обложки**, дефолт —
фиолетовый (не кислотно-зелёный). Конвенция реальных плееров (Apple Music /
Spotify color-adaptive), уместная для предмета.

## Токены (CSS custom properties — заданы на `:host` в sboom-panel.js)

Компоненты ТОЛЬКО потребляют эти переменные, НЕ переопределяют:

```
--sb-stage:      #0B0B0F   /* фон сцены, сине-чёрный, не #000 */
--sb-elev:       #16161D   /* приподнятые карточки */
--sb-elev-2:     #1E1E27   /* hover/active поверхности */
--sb-line:       rgba(255,255,255,0.08)   /* разделители/бордеры */
--sb-ink:        #F5F5F7   /* основной текст */
--sb-ink-dim:    rgba(245,245,247,0.60)   /* вторичный текст */
--sb-ink-faint:  rgba(245,245,247,0.38)   /* третичный/подписи */
--sb-glow:       #7C5CFF   /* ДИНАМИЧЕСКИЙ — из обложки; дефолт-фиолет */
--sb-glow-soft:  rgba(124,92,255,0.22)    /* производная glow для теней */
--sb-radius:     18px      /* карточки */
--sb-radius-sm:  12px
--sb-gap:        16px
--sb-accent:     var(--sb-glow)  /* интерактив (слайдеры/active) = glow */
```

Панель — самодостаточная тёмная сцена (не зависит от light/dark темы HA), как у
Sonos/Apple Music/Roon. Так задумано.

## Типографика

- **Дисплей** (заголовок now-playing): `"SF Pro Display", "Inter", system-ui,
  sans-serif`, вес 700, `letter-spacing: -0.02em`. Крупно (см. шкалу).
- **Текст/UI**: `system-ui, "Segoe UI", Roboto, sans-serif`.
- **Утилита** (тайм-код, версия, бейджи FLAC/E, битность): `ui-monospace,
  "SF Mono", "Roboto Mono", monospace`, tabular-nums. Монопространство для
  «приборных» данных — отсылка к device/протокольной природе интеграции.

Шкала: hero-title 30px/34px, artist 16px, album/подписи 13px, секц.заголовок
11px uppercase `letter-spacing:.14em` `--sb-ink-faint`, тайм-код 12px mono.

## Раскладка

Wide (>820px): две колонки — hero слева (sticky), справа таб-панель
[Очередь | Поиск]. Narrow: одна колонка, hero сверху, под ним табы.

```
┌──────────────────────────────────────────────────────┐
│  ambient glow (из обложки)                             │
│  ┌────────────┐   NOW PLAYING              v0.30.0(mono)│
│  │            │   Track Title (display 30px)           │
│  │   cover    │   Artist · Album                       │
│  │  большая   │   [E][FLAC]  1:12 ──●──────── 3:40      │
│  └────────────┘   ⏮   ⏯(56px)   ⏭     ♥  ⇄  ↻         │
│                   🔊 ──────●──────────────────── 42%    │
├──────────────────────────────────────────────────────┤
│  [ Очередь · 12 ]   [ Поиск ]     ← segmented tabs      │
│  ......                                                 │
└──────────────────────────────────────────────────────┘
```

## Контракты компонентов (теги + props + события — НЕ менять имена)

Данные `sboom/state` → `state = { connected, version, state:{volume_percent,
muted}, track:{...} }`. `track` поля: `title, artists[], album, cover_url,
track_id, playing, position_sec, duration_sec, shuffle, repeat("none"|"one"|
"all"|"context"?), liked, explicit, has_lyrics, playback_speed, provider,
station_name, playlist_title`.

- `<sboom-nowplaying .state=${state} .glow=${glowHex}>` — hero: крупная обложка,
  заголовок (display), artist·album, бейджи `E`/`FLAC`/`LYRICS`, **скраббер**
  (position/duration, кликом эмитит `@seek {detail:{value:seconds}}`), tabular
  тайм-коды. Пусто → «Ничего не воспроизводится» + placeholder.
- `<sboom-controls .hass .state>` — транспорт (prev/play-pause/next),
  like(♥)/shuffle(⇄)/repeat(↻ с состоянием), громкость+mute. Команды через
  `hass.callWS({type:"sboom/command", action, value?})`. action ∈ play|pause|
  next|prev|mute|unmute|like|shuffle(bool)|repeat(str)|volume(int)|seek(int).
  Оптимистично отражает `state.track.playing/shuffle/repeat/liked`. Ошибка →
  `@toast {detail:{message,type:"error"}}`.
- `<sboom-queue .hass .currentTrackId>` — очередь `sboom/queue`→`{queue:[{track_id,
  title,artists[],album,cover_url,duration,explicit}]}`. Клик → `sboom/play
  {content_id:String(track_id), pt:"track"}`. Текущий трек подсвечен glow.
- `<sboom-search .hass>` — поиск `sboom/search {query,limit}` → `{best,
  artists[],releases[],tracks[],playlists[]}`. Каждый item `{id,type,title,
  subtitle,cover_url,pt,explicit,duration}`. **Секции по категориям**
  (Исполнители / Альбомы / Треки / Плейлисты) — пользователь выбирает
  исполнителя/альбом/трек. Клик → `sboom/play {content_id:item.id, pt:item.pt}`.
  Debounce 350ms. Ошибка/пусто → аккуратные состояния.
- `<sboom-toast>` — метод `.show(message, type)`; ловит `@toast` на host.

## Ключ качества (обязательно)

Отзывчивость до мобильного, видимый keyboard-focus (`:focus-visible`),
`prefers-reduced-motion` уважается, `aria-label` на иконочных кнопках, обложки
`loading="lazy"`, длинный текст — ellipsis. Смелость тратим в ОДНОМ месте
(ambient glow); всё вокруг — тихое и дисциплинированное.

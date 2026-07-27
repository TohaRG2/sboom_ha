# SBoom Browse — Design Spec (locked)

Единый **люксовый drill-down браузер** для правой колонки панели плеера.
Заменяет вкладки «Очередь / Поиск» одним цельным, отзывчивым, дружелюбным
интерфейсом (как browse в нативных плеерах Spotify/Apple Music). Lit 3,
no-build, vendored lit (`../lit-base.js`). Тёмная сцена (НЕ фрост-стекло —
это только у карточки плеера). Токены `--sb-*` заданы на host, ТОЛЬКО
потребляются.

## Тезис

Никаких табов. Персистентная строка поиска сверху. Ниже — **навигационный
стек** представлений: выбор сущности «перестраивает» зависимые (артист →
альбомы+топ-треки; альбом → треклист; трек → играет). Назад — по стеку.

## Навигационная модель (в `sboom-browse`)

`_stack` — массив view-объектов, активен последний. `_stack[0]` всегда
`{kind:"queue"}` (корень). Кнопка «назад» (шеврон) видна при `length>1`,
делает pop.

- Пустой поиск → активна очередь (корень).
- Ввод в поиск → если верхний view не `search`, push `{kind:"search",query,result}`;
  иначе обновляем его. Debounce 350мс, защита от гонки устаревших ответов.
- Очистка поиска → если верхний `search`, pop (возврат туда, откуда искали).
- Клик по артисту (в поиске/альбоме) → `sboom/artist {content_id}` → push
  `{kind:"artist",data}`.
- Клик по альбому → `sboom/release {content_id}` → push `{kind:"album",data}`.
- Клик по треку → играть (`sboom/play {content_id, pt:"track", entry_id}`).

Слим-хедер представления при `length>1`: `‹ back`  + заголовок активного view.
Строка поиска остаётся сверху всегда.

## Представления

- **queue** — «Очередь · N» + кнопка обновления (`sboom/queue {entry_id}` →
  `{queue:[{track_id,title,artists,album,cover_url,duration,explicit}]}`).
  Строки `<sboom-track-row>`; текущий трек (`currentTrackId`) подсвечен +
  анимированный эквалайзер. Клик → play `content_id=String(track_id)`.
- **search** — секции в порядке: **Исполнители** (круглые тайлы, горизонтальный
  ряд-shelf) / **Альбомы** (квадратные тайлы, ряд) / **Треки** (строки) /
  **Плейлисты** (строки). Тайл артиста→drill artist, тайл альбома→drill release,
  трек→play, плейлист→play. Пустые секции скрыты. Данные `sboom/search`.
- **artist** — шапка: круглый аватар (крупный) + имя + «Исполнитель» + кнопка
  ▶ «Слушать» (play pt=artist). Секция **Популярное** (топ-треки, строки, до
  ~8, можно «показать все»). Секция **Дискография** (тайлы альбомов, ряд/сетка,
  новые сверху). Данные из push'а: `{id,title,cover_url,releases[],tracks[]}`.
- **album** — шапка: квадратная обложка + название + «Исполнитель · Год» +
  ▶ «Слушать» (play pt=release). **Треклист** (нумерованные строки: №, title,
  длительность, E/FLAC). Клик по треку→play. Данные: `{id,title,year,artist,
  cover_url,tracks[]}`.

## Контракты компонентов (теги/props/события — НЕ менять)

- `<sboom-browse .hass .entryId .currentTrackId>` — контейнер. Все WS-вызовы
  здесь. Эмитит `@toast` вверх. Держит стек и рендерит представления, используя
  примитивы ниже.
- `<sboom-track-row .track .index .active>` — переиспользуемая строка трека.
  `track`: `{id,title,artists,duration,explicit,has_flac,cover_url,pt}` —
  `artists` может быть массивом строк ИЛИ объектов `{title}` (обрабатывать оба).
  `index` (опц.) — номер в треклисте (для альбома). `active` (bool) —
  текущий трек (подсветка + эквалайзер). Клик/Enter → `dispatchEvent("open",
  {detail:{track}, bubbles, composed})`. НЕ вызывает WS сам.
- `<sboom-tile .item .shape>` — переиспользуемый тайл-обложка. `shape`:
  `"round"` (артист) | `"square"` (альбом/плейлист). `item`:
  `{id,title,subtitle?,cover_url,type,pt}`. Клик/Enter → `dispatchEvent("open",
  {detail:{item}, bubbles, composed})`.

## Действия (в sboom-browse, ловит `@open` от примитивов)

- track-row `@open` → `sboom/play {content_id:String(track.id ?? track.track_id),
  pt:track.pt||"track", entry_id}`; toast «Запущено».
- tile `@open` type=artist → `sboom/artist {content_id:item.id}` → push artist.
- tile `@open` type=release → `sboom/release {content_id:item.id}` → push album.
- tile `@open` type=playlist → play `pt:"playlist"`.
- Шапка artist ▶ → play `pt:"artist"`; шапка album ▶ → play `pt:"release"`.

## Визуал (люкс, best-practice UX)

- Всё на `--sb-elev` карточках, hover `--sb-elev-2`, разделители `--sb-line`,
  акцент `--sb-accent`, текст `--sb-ink`/`--sb-ink-dim`/`--sb-ink-faint`.
- Заголовки секций: 11px uppercase `letter-spacing:.14em` `--sb-ink-faint`.
- Тайлы: обложка (артист круг, альбом radius `--sb-radius-sm`), под ней title
  (1 строка ellipsis) + subtitle (`--sb-ink-faint`). Горизонтальные shelf'ы —
  прокрутка по X с тонким скроллбаром; hover приподнимает тайл.
- Строки: обложка (опц.) `--sb-radius-sm`, title/artist (ellipsis), справа
  моно-длительность (`--sb-mono`, tabular). Бейджи E/FLAC компактные.
- Шапки artist/album: крупная обложка, крупный заголовок (`--sb-disp`), под ним
  мета, акцентная кнопка ▶ «Слушать» (пилюля `--sb-accent`, тёмный текст).
- Слим-хедер назад: `‹` иконка (SVG, не emoji) + заголовок; hover.
- Переходы между view: лёгкий fade/slide (respect `prefers-reduced-motion`).
- Состояния: загрузка (спиннер/скелетон), пусто («Ничего не найдено» / «Очередь
  пуста»), ошибка (toast). Debounce поиска, гонки ответов отсекать по reqId.

## Качество (обязательно)

Отзывчивость до мобильного, `:focus-visible` (outline `--sb-accent`), клавиатура
(Enter/Space на тайлах/строках/кнопках), `aria-label` на иконках, `loading=lazy`
на обложках, ellipsis. SVG-иконки (лупа, крестик, назад, play, эквалайзер) — не
emoji. Никаких захардкоженных цветов вне `--sb-*` (кроме нейтральных
rgba-теней). Смелость — в drill-down и шапках; списки — тихие и дисциплинированные.

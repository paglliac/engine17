# Опись провайдеров: где код, кто потребляет, где токены

Инвентаризация «как есть» на 2026-11-03 для проектирования единого provider engine. Без предложений по дизайну.
Секреты не читались и не копировались: ниже только расположение файлов и имена ключей. Равенство копий токенов на Mac
проверено сравнением 8-символьных sha256-отпечатков, сами отпечатки в документ не попали. На gang.su ключи смотрел
только по именам (ssh `kir@gang.su`, sudo, только чтение); равенство копий там не проверено.

## Общая картина: хаб SecondBrain

- Боевой хаб: репо `paglliac/tasks17`, локальный чекаут `~/SecondBrain-new` (ветка `main`)
  - на gang.su: `/srv/sb/app` (ветка `main`), unit `secondbrain.service`, `uvicorn server.app:app` на `127.0.0.1:3141`, Caddy снаружи на `https://gang.su` — `~/SecondBrain-new/deploy/roles/hub/templates/secondbrain.service.j2`, `deploy/group_vars/all.yml`
  - деплой — собственный ansible в `~/SecondBrain-new/deploy/` (не этот репо); infra знает о нём только как `ports_taken.secondbrain: 3141` и `direct_taken.secondbrain: [avito.ktalk.ru, exchange.avito.ru]` — `group_vars/all/services.yml`
  - локально тот же код можно поднять через meta-server: `python3 server/scripts/run_local.py` в `~/SecondBrain-new`, данные `~/SecondBrain-data/{vault,data}` — `~/meta-server/config.yaml`, `~/SecondBrain-new/.env` (`VAULT`, `DATA_DIR`). Сейчас не запущен
- Старый хаб: `~/SecondBrain` (ветка `master`, remote не настроен), данные в `~/SecondBrain/knowledge_base/`; не запущен, его MCP никто не регистрирует
  - живой остаток: `~/SecondBrain/.mcp.json` → MCP `confluence` для сессий в этой папке (см. Confluence)
- HTTP API хаба — все роуты в `~/SecondBrain-new/server/app.py:327-352`:
  - `/api/projects`, `/api/tracker`, `/api/people`, `/api/triage`, `/api/areas`, `/api/calendar`, `/api/settings`, `/api/files`, `/api/today`, `/api/inbox`, `/api/meetings`, `/api/mattermost`, `/api/transcripts`, `/api/resources`, `/api/me`, `/api/health`, `/api/logs`
- Авторизация в хаб — `server/auth.py`: cookie сессии (браузер) или `Authorization: Bearer <SB_API_TOKEN>` (MCP, скрипты, mm-recaps). Токен один на всех потребителей
- Где хаб держит секреты (ADR 0014, `server/collectors/base.py:84-99`):
  - `.env` — только конфиг инстанса и `SB_*`; на gang.su `/srv/sb/app/.env`: `VAULT`, `DATA_DIR`, `DISABLE_AUTOUPDATER`, `SB_SESSION_SECRET`, `SB_API_TOKEN`, `SB_PASSWORD_HASH`
  - креды провайдеров — таблица `credentials(user_id, key, value)` в `vault/tracker/tracker.db`; на gang.su (`/srv/sb/vault/tracker/tracker.db`) там: `CLAUDE_CODE_OAUTH_TOKEN`, `EWS_EMAIL`, `EWS_PASSWORD`, `EWS_SERVER`, `EWS_USERNAME`, `KTALK_KONTUR_NGTOKEN`, `KTALK_NGTOKEN`, `KTALK_TOKEN`, `MM_TOKEN`, `MM_URL`, `TODOIST_API_TOKEN`
  - вводятся через UI хаба: `/api/settings/save`, `/api/settings/collectors/ktalk/curl`, `/api/settings/claude` — `server/api/settings.py`
  - `CREDENTIAL_KEYS` в `.env` фильтруются (`merged_env`), даже если строка осталась на диске
- Фоновые задачи хаба (`server/app.py:211-228`): опрос календаря, discover источников инбокса, разбор захватов (LLM), jobs (recaps)
- `~/SecondBrain-new/server/mcp/*.py` — «тонкие обёртки» над HTTP API (правило `skill → project MCP → main server → data`, `~/SecondBrain-new/CLAUDE.md:64-76`)
  - по умолчанию `SB_SERVER_URL=http://127.0.0.1:3141`, токен из env процесса; имена FastMCP без `_global`
  - нигде не зарегистрированы: `.mcp.json` в `~/SecondBrain-new` нет, в `~/.claude.json` проектных `mcpServers` нет ни у одного проекта
- `~/mcp/secondbrain/*.py` — ручные копии этих обёрток, зарегистрированы глобально (`~/mcp/secondbrain/README.md`)
  - отличия по замыслу: url/токен из `~/mcp/secondbrain/.env` через `sb_client.py` (перечитывается при изменении), имена `*_global`, url по умолчанию `https://gang.su`
  - дрейф по инструментам (сравнение `@mcp.tool` в обеих копиях):
    - `inbox`: в глобальной нет `run_capture`
    - `sb_calendar`: в глобальной нет `calendar_get_agenda`, `calendar_add_agenda_item`, `calendar_set_agenda`
    - `triage`: в глобальной лишние `list_groups`, `group_queue`, `edit_group`, `decide_group` → ходят в `/api/triage/groups|group|group/edit|group/decide`, которых на gang.su нет (роуты есть только в старом `~/SecondBrain/server/api/triage.py:1109-1112`)
    - `observability` (`health`, `list_logs`, `check_claude`) есть только в `~/SecondBrain-new/server/mcp/observability.py`, глобальной копии нет
    - `tracker`, `people`, `mattermost`, `ktalk` — наборы инструментов совпадают, отличается только транспорт
- Глобальные MCP на Mac (`~/.claude.json` → `mcpServers`, scope user; проектных нет):
  - `mattermost_global` → `~/mcp/mattermost/mattermost_mcp.py`
  - `tracker_global`, `inbox_global`, `triage_global`, `people_global`, `sb_mattermost_global`, `ktalk_global`, `calendar_global` → `~/mcp/secondbrain/*.py`
  - `perf_review_global` → `~/orca/projects/personal-review/server/mcp-stdio.ts` (env `REVIEW_DB`, `NODE_NO_WARNINGS`)
  - `unit_global` → `~/mcp/unit/unit_mcp.py`
  - `confluence_global` → `~/.local/bin/mcp-atlassian --env-file ~/mcp/confluence/.env`
  - `pencil` — не провайдер данных
- Те же `~/mcp/*` подключены ещё в двух агентах на Mac, каждый своим конфигом:
  - Hermes (`~/.hermes/config.yaml:242-283`, launchd `~/Library/LaunchAgents/ai.hermes.gateway.plist`): `mattermost_global` + 7 из `~/mcp/secondbrain`
  - Codex (`~/.codex/config.toml:35-66`): тот же набор
- Ещё один конфиг MCP — `~/.claude/avito.mcp.json`: корпоративный `avito mcp` и `avito mcp hub`. В `~/.claude.json`, `settings.json` и shell rc не упомянут; skill `maturity-rollup` вызывает `mcp__avito__paas_confluence_*`

## Внешние провайдеры

### Mattermost (mt.avito.ru)

- Копий клиентского кода 8 (живых 6), общего кода нет:
  - `~/SecondBrain-new/server/collectors/mattermost_client.py` (446 строк, sync `requests`) — боевая, в хабе
    - вокруг неё: `server/api/mattermost.py` (HTTP-поверхность), `server/collectors/mattermost.py` (коллектор сохранённых постов, своя сессия для `flagged`/unsave), `server/api/resources.py` и `source_labels.py` (заголовки ссылок)
  - `~/SecondBrain/server/collectors/mattermost_client.py` (442) — старый хаб, отличие от боевой 6 строк
  - `~/mcp/mattermost/mattermost_client.py` (476) — копия старой: другие первые ~60 строк (свои `ensure_requests`/`load_env`, `from_env` берёт dict); от боевой отличается на 62 строки — `~/mcp/mattermost/README.md`
    - его же импортируют через `sys.path` скрипты Screen Sprite: `~/assistant/mm-check/mm-avatars.py`, `mm-status.py`, `mm-obligations.py`
  - `~/orca/workspaces/mm-recaps/recaps/mm.py` — свой async-клиент (`httpx`) с allowlist путей только на чтение; плюс `recaps/push.py` слушает WebSocket `/api/v4/websocket`
  - `~/orca/projects/MattermostImportant/Packages/Core/Sources/Core/Mattermost.swift` — Swift, PoC
  - `~/orca/projects/mattermost-macos/MattermostMac/Core/Network/` + `Packages/MattermostCore` — полноценный Swift-клиент
  - `~/orca/projects/mattermost/app/src/main/java/ru/kir/mattermost/data/network/MattermostApi.kt` — Android (Retrofit)
  - `~/Documents/Obsidian Vault/resources/scripts/mattermost_saved_posts.py` — заброшенный скрипт (апрель 2026)
- Потребители:
  - `mattermost_global` (Mac, напрямую в MM): Claude Code в любом проекте, Hermes, Codex, `~/assistant/mm-check/mm-check.sh` (`claude -p` с `mcp__mattermost_global`)
  - `sb_mattermost_global` (Mac → хаб `/api/mattermost` → MM токеном хаба): Claude Code, Hermes, Codex
  - хаб: коллектор инбокса (сохранённые посты), заголовки ссылок в ресурсах проектов
  - mm-recaps на gang.su (`recaps.service`): рекапы каналов, «Ждут от меня», ИИ-чат со своим MCP `recaps/chat_mcp.py` (stdio, только чтение), push-ретранслятор в FCM
  - mm-recaps локально: сейчас висит процесс `python -m recaps` из `~/orca/workspaces/mm-recaps`, запущен 2026-10-28 из сессии Claude Code, креды в env процесса (`MM_URL`, `MM_TOKEN`, `MM_COOKIE`, `RECAPS_ACCESS_TOKEN`, …); `.env` в чекауте нет
  - MattermostImportant (macOS menu bar), mattermost-macos (Mac-клиент), Android `ru.kir.mattermost`, Screen Sprite (`~/assistant`)
- Что умеет:
  - чтение: status, search, thread, channels, channel posts, users, mentions, unreads, saved — одинаковый набор из 10 у `mattermost_global` и `/api/mattermost` (`server/api/mattermost.py:1-18`)
  - запись:
    - `mm_post` / `POST /api/mattermost/post` — выключено (`MM_ALLOW_POST=0` и в `~/mcp/mattermost/.env`, и в `~/SecondBrain-new/.env`)
    - коллектор хаба снимает флажок «сохранено» после разбора (`server/collectors/mattermost.py:192-206`, `finalize`)
    - mm-recaps — только чтение по allowlist (`recaps/mm.py:21`), WebSocket только слушает
    - нативные клиенты пишут сессией пользователя (у mattermost-macos — реакции и действия с сообщениями: `MattermostMacTests/ReactionsTests.swift`, `MessageActionsTests.swift`)
- Креды (имена ключей и где лежат):
  - PAT-A: `~/mcp/mattermost/.env` (`MM_URL`, `MM_TOKEN`, `MM_COOKIE`, `MM_ALLOW_POST`)
    - отсюда же берёт `MM_TOKEN` MattermostImportant (`Mattermost.swift:78-90`) и скрипты Screen Sprite
    - отсюда же infra залила `MM_TOKEN` в `/srv/recaps/.env` при первом деплое: `recaps_env_file: ~/mcp/mattermost/.env` в `inventory.local.yml`
  - PAT-B (другой токен): `~/SecondBrain-new/.env`, `~/SecondBrain/knowledge_base/.env`, `~/SecondBrain-backup-20260918-144212/env.bak` (`MM_TOKEN`, `MM_COOKIE`, `MM_URL`)
  - хаб gang.su: `MM_TOKEN`, `MM_URL` в `credentials` tracker.db (какой из PAT — не проверено)
  - mm-recaps gang.su: `/srv/recaps/.env` → `MM_TOKEN`
  - `MM_COOKIE` (один и тот же во всех копиях): `~/mcp/mattermost/.env`, `~/SecondBrain-new/.env`, `~/SecondBrain/knowledge_base/.env`, `~/Documents/Obsidian Vault/.env`, `~/Documents/Obsidian Vault/resources/scripts/.env` (два последних с правами 0644)
  - отдельно: `~/.config/mattermost-poc/mmauthtoken` (cookie для curl из skill `mattermost-api`), Keychain `MMAUTHTOKEN` у mattermost-macos, сессия в приложении Android
  - итого на Mac минимум 2 разных PAT и 1 cookie в 7+ файлах; на сервере ещё 2 места
- Транспорт: везде прямой REST `/api/v4` (+ WebSocket у mm-recaps и клиентов); `sb_mattermost_global` — через хаб `/api/mattermost`

### Confluence

- Два разных инстанса, оба через вендорный `mcp-atlassian` (`~/.local/bin/mcp-atlassian`), своего кода нет:
  - `confluence_global` → `cf.avito.ru` (рабочий), `~/mcp/confluence/.env`: `CONFLUENCE_URL`, `CONFLUENCE_PERSONAL_TOKEN`
    - включённые инструменты (`--enabled-tools` в `~/.claude.json`): search, get_page, get_page_children, get_comments, get_inline_comments — чтение; add_comment, add_inline_comment, reply_to_comment — запись комментариев
  - `confluence` (проектный, только в сессиях из `~/SecondBrain`) → личный Atlassian Cloud `paglliac-1787124514458.atlassian.net`, `~/SecondBrain/.mcp.json` → `--env-file ~/SecondBrain/knowledge_base/.env`: `CONFLUENCE_URL`, `CONFLUENCE_USERNAME`, `CONFLUENCE_API_TOKEN`, env `TOOLSETS`
    - копия тех же ключей в `~/SecondBrain-new/.env` (комментарий «used by the confluence MCP», но `.mcp.json` в SecondBrain-new нет) и в `env.bak` бэкапа
- Третий путь к `cf.avito.ru`: корпоративный `avito mcp` (`~/.claude/avito.mcp.json`), инструменты `paas_confluence_download_page` / `upload_page` — запись целых страниц; потребитель — skill `~/.claude/skills/maturity-rollup/SKILL.md:20-22`. Креды — внутри `avito` CLI
- Хаб и сервисы gang.su Confluence не используют
- Транспорт: напрямую из Mac

### ktalk (avito.ktalk.ru, транскрипты встреч)

- Код — одна живая копия: `~/SecondBrain-new/server/collectors/ktalk.py` (`BASE_URL = https://avito.ktalk.ru/api`), «vendored from the former global ktalk MCP» (`~/.claude/mcp-servers/ktalk/` больше не существует)
  - старая версия: `~/SecondBrain/server/collectors/ktalk.py` (67 строк разницы)
  - индекс и хранение: `server/collectors/transcript_index.py`, файлы в `vault/infrastructure/sources/transcripts/`
  - HTTP: `server/api/transcripts.py` — `GET /api/transcripts`, `GET /api/transcripts/{id}`, `POST /api/transcripts` (ручная загрузка, источник `manual`)
- Потребители:
  - хаб: пайплайн инбокса, раз в цикл discover → fetch → индекс
  - `ktalk_global` (Mac → `/api/transcripts`): list, read, upload — работает с копиями на хабе, в ktalk не ходит
  - `inbox_global.sweep_ktalk` / `discover_now` → `/api/inbox/sweep`, `/api/inbox/discover`
- Что умеет: в ktalk — только чтение (список записей, транскрипт); запись только в хаб (upload)
- Креды: `KTALK_TOKEN`, `KTALK_NGTOKEN`, `KTALK_KONTUR_NGTOKEN` (cookie сессии `sessionToken`/`ngtoken`/`kontur_ngtoken`, вставляются из «Copy as cURL» в UI)
  - gang.su: `credentials` tracker.db
  - устаревшие копии: `~/SecondBrain-new/.env`, `~/SecondBrain/knowledge_base/.env`, `env.bak` бэкапа (одно и то же значение)
- Транспорт: только хаб на gang.su; сайт отвечает только с адреса хоста, мимо NL-туннеля (`direct_taken` в `services.yml`)
- Остаток: `~/ktalk-data/` — 615 файлов выгрузки старого глобального MCP, последний раз писался 2026-05-26

### Календарь Exchange (exchange.avito.ru)

- Код — одна живая копия: `~/SecondBrain-new/server/api/calendar.py` (`exchangelib`, EWS, `DELEGATE`, без autodiscover)
  - старая: `~/SecondBrain/server/api/calendar.py` (392 строки против 622, без agenda)
  - опрос раз в 300 с (`POLL_INTERVAL_SECONDS`) в снапшот `calendar_snapshot` в tracker.db; запросы читают снапшот, не Exchange
- HTTP: `/api/calendar/{today,day,upcoming,past,status}`, `POST /refresh`, `GET|PUT /agenda`, `POST /agenda/items`; ещё `/api/today/*` (план дня поверх календаря)
- Потребители:
  - `calendar_global` (Mac → `/api/calendar`): `calendar_day`, `calendar_upcoming`, `calendar_status`; agenda-инструментов в глобальной копии нет
  - Screen Sprite: `~/assistant/mm-check/sb-check.py` → `/api/calendar/upcoming`, `/api/triage/status` с токеном из `~/mcp/secondbrain/.env`
  - UI хаба (день, встречи, agenda)
- Что умеет: Exchange — только чтение; agenda встречи пишется в хаб, не в Exchange
- Креды: `EWS_SERVER`, `EWS_USERNAME`, `EWS_PASSWORD`, `EWS_EMAIL` — только `credentials` tracker.db на gang.su (в локальных `.env` их нет); среди них пароль учётки (`EWS_PASSWORD`)
- Транспорт: только хаб на gang.su, мимо NL-туннеля (`direct_taken`)

### ZenMoney (api.zenmoney.ru)

- Код — одна копия: `~/budget17/src/zenmoney/client.ts` (`https://api.zenmoney.ru/v8`, `POST /diff/`); синк `src/sync.ts`
  - вторая рабочая копия репо: `~/orca/workspaces/budget17/Ansible-Deploy/src/zenmoney/client.ts`
- Потребители: сервис `budget` на gang.su (`/srv/budget`, `budget.gang.su`), локальный запуск `~/budget17`; iOS-приложение `~/budget17/ios` ходит в API `budget`, не в ZenMoney (токен `budget.access-token` в Keychain, `ios/Shared/Keychain.swift`)
- Что умеет: используется только чтение (`changesSince` → `/diff` без изменений с клиента); сам токен даёт и запись (`~/budget17/README.md:13`)
- Креды: `ZENMONEY_TOKEN`
  - gang.su: `/srv/budget/.env` (там же `ACCESS_TOKEN` для приложения)
  - Mac: `~/budget17/.env` сейчас нет; infra берёт его по `budget_env_file`, но в `inventory.local.yml` этот ключ не задан
- Транспорт: напрямую; на gang.su мимо NL-туннеля (`direct: [api.zenmoney.ru]`); в MCP не выставлен

### Todoist (найдено дополнительно)

- Код: `~/SecondBrain-new/server/collectors/todoist.py` (= `~/SecondBrain/...`, 0 строк разницы)
- Потребитель: только пайплайн инбокса хаба
- Что умеет: чтение задач + удаление задачи после разбора (`finalize`, `todoist.py:86`)
- Креды: `TODOIST_API_TOKEN` — tracker.db на gang.su; устаревшие копии в `~/SecondBrain-new/.env`, `~/SecondBrain/knowledge_base/.env`, `~/Documents/Obsidian Vault/.env` (+ `resources/scripts/.env`), `env.bak` (везде одно значение)

### org.avito.ru, оргструктура (найдено дополнительно)

- Код: `~/SecondBrain-new/server/org_structure.py`, запуск кнопкой `POST /api/settings/org-structure/refresh`; результат `areas/avito/org-structure.json`
- Что умеет: только чтение
- Креды: `ORG_COOKIE` (живёт недолго); на gang.su в `credentials` его нет; локально в `~/SecondBrain-new/.env` и `~/SecondBrain/knowledge_base/.env`

### mm-recaps как провайдер («Ждут от меня», рекапы)

- Код: репо `paglliac/mm-recaps`, чекаут `~/orca/workspaces/mm-recaps` (+ worktree-копии `mm-recaps-assistant`, `-focus`, `-recheck`); на gang.su `/srv/recaps`, `recaps.gang.su`
- Отдаёт по HTTP (Bearer `RECAPS_ACCESS_TOKEN`): `/recaps`, `/runs`, `/owed`, `/owed/items/...`, `/assistant/{facts,ask,tasks}`, `/push/test` — `README.md`, раздел API
- Сам потребляет хаб: `GET /api/projects/warnings`, `POST /api/tracker/tasks` (`recaps/assistant.py:135,150`) с `SB_API_TOKEN` из `/srv/recaps/.env`
- Потребители: форк вебаппа (`mattermost-webapp`, dev-server через meta-server, токен `$(cat ~/.recaps_token)`), mattermost-macos (Keychain `RECAPS_TOKEN`, `RecapsStore.swift`), Android (`data/recaps/`), прототип ассистента
- Креды `RECAPS_ACCESS_TOKEN`: `/srv/recaps/.env`, `~/.recaps_token`, `~/.config/mattermost-poc/recaps-token`, `~/.config/assistant-proto/recaps.env`, Keychain mattermost-macos
- MCP-обёртки на Mac нет

## Свои данные

### tracker (задачи, проекты, someday, «я»)

- Код: `~/SecondBrain-new/server/tracker_core.py` (3137 строк) + `server/api/{tracker,projects,today,me}.py`; хранение `vault/tracker/tracker.db`
  - старая копия: `~/SecondBrain/server/tracker_core.py` (2688, 527 строк разницы), своя `tracker.db` в `~/SecondBrain/knowledge_base/tracker/`; ещё одна локальная база `~/SecondBrain-data/vault/tracker/tracker.db`
- MCP: `tracker_global` → `/api/tracker`, `/api/projects`, `/api/me`; 25 инструментов, наборы в двух копиях совпадают (в старом хабе было 16)
- Потребители: Claude Code, Hermes, Codex (MCP); UI хаба; mm-recaps (`/api/projects/warnings`, `POST /api/tracker/tasks`); skill `weekly-review`
- Умеет: чтение и запись (задачи: add/update/move/complete/delete; проекты: create/update/status/merge/log/aliases/stakeholders; someday: add/promote)
- Креды: `SB_API_TOKEN` (см. «Общая картина»)

### people

- Код: `~/SecondBrain-new/server/api/{people,people_resolve}.py`, таблицы людей и алиасов в tracker.db, аватары `vault/areas/avito/avatars` (раздаются по `/avatars`)
- MCP: `people_global` → `/api/people`, 8 инструментов: list, get, resolve, create, update, add/remove/list alias — чтение и запись
- Потребители: Claude Code, Hermes, Codex; внутри хаба резолв имён при разборе инбокса, участники встреч
- Рядом другие «люди», не связанные с этим списком: `persons` в `~/resource-planning/unit-state.json` (unit), оргструктура `org-structure.json` из org.avito.ru

### inbox (пайплайн захватов)

- Код: `~/SecondBrain-new/server/api/{inbox,inbox_pipeline,inbox_store,extract,kb_writer}.py`, коллекторы `server/collectors/*`; хранение `DATA_DIR/inbox.db` (на gang.su `/srv/sb/data/inbox.db`)
- MCP: `inbox_global` → `/api/inbox`: status, list_captures, discover_now, process_now, retry/reprocess_capture, sweep_ktalk, setup, get_config; в боевой копии ещё `run_capture`
- Умеет: чтение статуса и запуск обработки (запись в хаб; через коллекторы — побочные записи во внешние системы: unsave в MM, удаление в Todoist)
- Обработка захватов зовёт LLM: `claude` CLI на хабе с `CLAUDE_CODE_OAUTH_TOKEN` из tracker.db (`server/api/_llm.py`)

### triage

- Код: `~/SecondBrain-new/server/api/triage*.py`, хранение `inbox.db`
- MCP: `triage_global` → `/api/triage`: create_items, list_items, list_fields, decide, triage_status, debug_route — чтение и запись
  - 4 «групповых» инструмента глобальной копии бьют в несуществующие на gang.su роуты (см. «Общая картина»)
- Потребители: Claude Code, Hermes, Codex; UI `/triage`, `/m/triage`; Screen Sprite `sb-check.py` (`/api/triage/status`)

### unit (распределение юнита)

- Код: `~/mcp/unit/unit_mcp.py` (826 строк, 21 инструмент); читает и пишет JSON напрямую: `UNIT_STATE_FILE`, по умолчанию `~/resource-planning/unit-state.json`
  - тот же файл раздаёт и пишет `~/resource-planning/server.py` (`/api/unit-state`, страница `unit-allocator.html`); модель данных в MCP «повторяет JS страницы 1-в-1» — вторая реализация той же логики
  - `~/resource-planning` — локальный git без remote; запускается через meta-server («Resource Planner»)
- Потребители: только Claude Code (`unit_global`); в Hermes/Codex не подключён; на gang.su нет
- Умеет: чтение и запись (команды, люди, найм, варианты, перемещения)
- Креды: нет (локальный файл); транспорт — файловая система Mac

### perf_review

- Код: `~/orca/projects/personal-review/server/{core,db,mcp,mcp-stdio}.ts`; SQLite `~/orca/projects/personal-review/review.db` (путь задаётся `REVIEW_DB` в `~/.claude.json` и в `~/meta-server/config.yaml`); репо без remote
- MCP: `perf_review_global` (stdio, 12 инструментов) — get/update review, list/get/create/update/delete project, append_to_facts/project, list/resolve notes, export_markdown — чтение и запись
- Потребители: Claude Code; веб-приложение того же репо (meta-server `reviewx`, общий файл БД, `PRAGMA data_version`)
- Креды: нет; транспорт — локальный файл. Сырьё ревью (`review-work/raw/mm`, `raw/tracker`) выгружено в файлы, живой связи с MM/хабом нет

## Сводная таблица

| Провайдер | Копий кода (живых / всего) | Потребители | Где токены (копий) |
|---|---|---|---|
| Mattermost | 6 / 8 (хаб, `~/mcp/mattermost`, старый SB, mm-recaps, 3 нативных клиента, Obsidian) | `mattermost_global`, `sb_mattermost_global`, хаб, mm-recaps (сервер и локальный процесс), Screen Sprite, MattermostImportant, Mac/Android-клиенты, Hermes, Codex | PAT-A: `~/mcp/mattermost/.env`, `/srv/recaps/.env`; PAT-B: 3 файла в SB/бэкапе; хаб: tracker.db; cookie: 5 файлов; плюс `~/.config/mattermost-poc`, Keychain, телефон |
| Confluence | 0 своего, вендорный `mcp-atlassian` ×2 + `avito mcp` | `confluence_global` (cf.avito.ru), `confluence` в `~/SecondBrain` (личный Cloud), skill maturity-rollup | cf: `~/mcp/confluence/.env`; Cloud: `~/SecondBrain/knowledge_base/.env`, `~/SecondBrain-new/.env`, `env.bak`; avito: внутри CLI |
| ktalk | 1 / 2 | хаб; `ktalk_global` и `inbox_global` через хаб | tracker.db на gang.su; 3 устаревшие копии в `.env` |
| Exchange | 1 / 2 | хаб; `calendar_global`, Screen Sprite через хаб | только tracker.db на gang.su |
| ZenMoney | 1 (+ рабочая копия репо) | сервис budget; iOS — через budget | `/srv/budget/.env` |
| Todoist | 1 / 2 | пайплайн хаба | tracker.db; 4 устаревшие копии |
| org.avito.ru | 1 / 2 | хаб (кнопка в настройках) | только локальные `.env` (на gang.su нет) |
| mm-recaps API | 1 | вебапп-форк, Mac/Android-клиенты, прототип ассистента | `RECAPS_ACCESS_TOKEN`: сервер + 3 файла на Mac + Keychain |
| tracker | 1 живая (+ старый SB); MCP-обёртка ×2 | Claude Code, Hermes, Codex, UI хаба, mm-recaps, weekly-review | `SB_API_TOKEN`: `/srv/sb/app/.env`, `~/mcp/secondbrain/.env`, `/srv/recaps/.env` |
| people | 1; MCP ×2 | Claude Code, Hermes, Codex, хаб | `SB_API_TOKEN` |
| inbox | 1; MCP ×2 (дрейф) | Claude Code, Hermes, Codex, хаб | `SB_API_TOKEN` |
| triage | 1; MCP ×2 (дрейф, 4 мёртвых инструмента) | Claude Code, Hermes, Codex, UI, Screen Sprite | `SB_API_TOKEN` |
| unit | 2 реализации одной модели (JS страницы + Python MCP) | только Claude Code на Mac | нет |
| perf_review | 1 | Claude Code, своё веб-приложение | нет |

## Факты, важные для дизайна

- Транспортов три, и они не пересекаются:
  - через хаб (`https://gang.su/api/...`, один `SB_API_TOKEN`): tracker, people, inbox, triage, calendar, transcripts, mattermost (дубль)
  - напрямую с Mac: `mattermost_global`, Confluence, unit (файл), perf_review (файл)
  - напрямую с сервиса gang.su: budget → ZenMoney, mm-recaps → MM
- ktalk и Exchange достижимы только с gang.su (с адреса хоста, мимо NL-туннеля); креды к ним есть только в tracker.db хаба. Mac в них напрямую не ходит — `group_vars/all/services.yml` (`direct_taken`), `~/SecondBrain-new/server/api/calendar.py`
- Mattermost доступен двумя MCP с одинаковыми 10 инструментами, но разными токенами и путями: `mattermost_global` (PAT-A, прямо) и `sb_mattermost_global` (токен хаба через `/api/mattermost`) — `~/.claude.json`, `~/mcp/mattermost/README.md`
- Клиентов MM 6 живых (8 с заброшенными), и ни один не переиспользует другой; ближе всего к общему — `~/mcp/mattermost/mattermost_client.py`, который уже импортируют чужие скрипты через `sys.path` (`~/assistant/mm-check/*.py`) и чей `.env` читают MattermostImportant и infra (`recaps_env_file`)
- Политики записи в MM разные: у `mattermost_global` и хаба — флаг `MM_ALLOW_POST` (сейчас 0) в двух разных `.env`; у mm-recaps — allowlist путей в коде (`recaps/mm.py:21`); коллектор хаба всё равно пишет (unsave) без флага
- `~/mcp/secondbrain/*` — ручные копии, README прямо говорит «they will drift», и дрейф уже есть в обе стороны: в глобальной не хватает 4 инструментов (`run_capture`, 3 agenda), а 4 triage-инструмента ведут на роуты, которых на gang.su нет (в `/srv/sb/app/server/api/triage.py` нет `Route("/group…")`)
- Обёртки `~/SecondBrain-new/server/mcp/*.py` нигде не зарегистрированы — боевыми фактически являются копии в `~/mcp/secondbrain`
- Одни и те же MCP-серверы зарегистрированы в трёх агентах тремя конфигами (`~/.claude.json`, `~/.hermes/config.yaml`, `~/.codex/config.toml`); unit, perf_review и confluence есть только у Claude Code
- В хабе креды переехали в tracker.db (ADR 0014), но старые `.env` с теми же ключами лежат минимум в 5 местах на Mac (`~/SecondBrain-new/.env`, `~/SecondBrain/knowledge_base/.env`, `~/SecondBrain-backup-20260918-144212/env.bak`, два `.env` в Obsidian Vault — последние с правами 0644)
- Confluence — это два разных инстанса с разными схемами авторизации (PAT на `cf.avito.ru`; email + API token на `*.atlassian.net`), плюс третий путь записи страниц через корпоративный `avito mcp`
- mm-recaps — одновременно потребитель (MM, хаб) и провайдер («Ждут от меня», рекапы) без MCP-обёртки; его токен лежит в 4 файлах на Mac и в Keychain
- В сервисах на gang.su `CLAUDE_CODE_OAUTH_TOKEN` хранится по-разному: хаб — в tracker.db, recaps — в `/srv/recaps/.env`, у go `/srv/go/.env` пустой (0 байт), хотя `services.yml` ждёт там `CLAUDE_CODE_OAUTH_TOKEN` и `GO_OWNER`
- unit и perf_review — чисто локальные файлы на Mac без HTTP-API для внешних потребителей и без бэкапа на сервере; unit к тому же дублирует бизнес-логику страницы на Python (`~/mcp/unit/unit_mcp.py:1-17`)
- Списков людей три, и они не связаны: people в хабе, `persons` в unit-state, оргструктура из org.avito.ru
- Рабочие копии и старьё, которые легко принять за источник правды: `~/SecondBrain` (старый хаб, своя tracker.db), `~/SecondBrain-data` (локальный инстанс без кредов), `~/ktalk-data` (выгрузка до мая 2026), `~/orca/workspaces/*` (worktree-копии mm-recaps, budget17, SecondBrain-new)

## Что не проверено

- Совпадают ли токены на gang.su между собой и с Mac (`MM_TOKEN` в tracker.db и в `/srv/recaps/.env`, `SB_API_TOKEN` в трёх местах): для этого нужна запись временного скрипта на сервер, а доступ был только на чтение
- Как подключается `~/.claude/avito.mcp.json`: ни в одном найденном конфиге он не упомянут
- Репо `go-game` локально не найден; на gang.su (`/srv/go/app`) провайдеров данных у него нет, только Claude и KataGo

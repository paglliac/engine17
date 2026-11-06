# engine17 — мотор поставщиков

Одна программа на `gang.su` (`motor.gang.su`), через которую Claude на маке и сервисы на сервере ходят к Mattermost, календарю, Confluence, Jira, ktalk, оргструктуре, Todoist и к данным хаба SecondBrain.

**Сначала прочитай `docs/motor.md`** — там устройство, правила и доска задач с правилами «как взять задачу». Работа идёт только по задачам оттуда.

- `docs/decisions/` — карта решений (`map.md`) и задачи, по которым к этому пришли. Почему так — там; что делать — в `docs/motor.md`.
- `docs/research/opis.md` — опись на 2026-11-03: где лежит код каждого поставщика, кто его зовёт, где токены (только имена ключей, без значений).
- `docs/research/udalyonnyj-mcp.md` — удалённый MCP в Claude Code: транспорт, токены, таймауты (60 с на HTTP-запрос, 25k токенов на ответ).

## Стек

Python 3.13 (на сервере), Starlette + uvicorn, MCP — официальный `mcp` SDK 2.x (stateless, ответы JSON), база — SQLite `data/motor.db`. Выбрано в К1: всё, что переносится, на Python, а SDK сам держит обе версии протокола MCP на одном адресе.

- `motor/provider.py` — механизм: `Provider` + `@p.action` → `GET /<поставщик>/<действие>` и инструмент MCP на `/<поставщик>`. Как писать поставщика — в шапке файла; пример — `motor/providers/motor.py`, список — `motor/providers/__init__.py`.
- `motor/errors.py` — ошибки с кодом и текстом «что делать»; `motor/store.py` — потребители, внешние токены, состояние поставщиков; `motor/page.py` — страничка.
- `make venv`, `make test`, `make run` (локально на `:4319`), `make hash-password` (строка для `MOTOR_PASSWORD_HASH`).

## Деплой — через `infra`

Своего `deploy/` здесь нет. Деплой — запись `motor` в `group_vars/all/services.yml` репо `paglliac/infra17` (чекаут `~/orca/projects/infra`, worktree-копии в `~/orca/workspaces/infra/`). Поля описаны в начале файла, общее — в `README.md` infra.

- Набросок записи (порт выбрать свободный; заняты 3141 SecondBrain, 4317 budget, 4318 go, 4325 recaps):
  ```yaml
  motor:
    repo: git@github.com:paglliac/engine17.git
    port: 43xx
    subdomain: motor
    runtime: python
    exec: "{{ srv_root }}/motor/venv/bin/python -m motor"   # что получится в К1
    requirements: requirements.txt
    data_link: data            # база токенов и потребителей
    secrets: {…: 40}           # то, что генерируется один раз
    direct: [exchange.avito.ru, avito.ktalk.ru]   # когда дойдёт до П2/П6
    sqlite: [motor.db]
    health: {path: /healthz, status: 200}
  ```
- В окружении сервиса есть `PORT`, `HOST`, `TZ`; слушать `127.0.0.1:$PORT`, HTTPS снаружи даёт Caddy (`/etc/caddy/sites/motor.caddy`). DNS не нужен: `*.gang.su` уже на сервере.
- На хосте: `/srv/motor/{app,data,.env}`, пользователь и systemd-юнит `motor`.
- Команды (из папки infra):
  - `make deploy S=motor` — первый прогон остановится и напечатает deploy key: добавить его в `paglliac/engine17` → Settings → Deploy keys (только чтение) и запустить ещё раз;
  - `make app S=motor` — только `git pull`, зависимости, рестарт;
  - `make env S=<сервис> K=MOTOR_TOKEN` — положить ключ в `.env` сервиса (значение спросит);
  - `make direct` — применить только `direct`.
- Сеть: хост выходит наружу через `awg0` (NL-туннель). Хосты, которые NL-адрес не пускают, — в `direct` сервиса. `exchange.avito.ru` и `avito.ktalk.ru` сейчас стоят в `direct_taken` у SecondBrain; когда хаб перестанет ходить к ним сам (Х8, Х9), они уходят оттуда.
- Внутренние `*.avito.ru` (`cf.avito.ru`, `prod.k.avito.ru`) с сервера не отвечают — нужен VPN Авито на `gang.su` (задача П3).
- Сервер: `ssh kir@gang.su`, sudo. Сервисы с `claude: true` получают `/usr/bin/claude`.

## Откуда переносить код

Подробности и строки — в `docs/research/opis.md`. Главное:

| что | где сейчас |
|---|---|
| хаб SecondBrain (боевой) | репо `paglliac/tasks17`, чекаут `~/SecondBrain-new`; на сервере `/srv/sb/app`, `127.0.0.1:3141`, `https://gang.su`; свой деплой `~/SecondBrain-new/deploy/` |
| HTTP API хаба (для П9) | роуты — `~/SecondBrain-new/server/app.py`; вход — `SB_API_TOKEN` |
| MCP-обёртки хаба (оригиналы для П9) | `~/SecondBrain-new/server/mcp/*.py`. Не брать `~/mcp/secondbrain/*` — разошлись |
| Mattermost (П1) | боевой клиент `~/SecondBrain-new/server/collectors/mattermost_client.py`; 10 инструментов — `~/mcp/mattermost/mattermost_mcp.py` |
| календарь Exchange (П2) | `~/SecondBrain-new/server/api/calendar.py` (EWS, `exchangelib`) |
| ktalk (П6) | `~/SecondBrain-new/server/collectors/ktalk.py` |
| Todoist (П8) | `~/SecondBrain-new/server/collectors/todoist.py` |
| оргструктура (П7) | `~/SecondBrain-new/server/org_structure.py`, вход — `ORG_COOKIE` (живёт недолго) |
| Confluence (П4) | своего кода нет: сейчас вендорный `~/.local/bin/mcp-atlassian`, набор инструментов — `--enabled-tools` у `confluence_global` в `~/.claude.json` |
| Jira (П5) | пример — класс `Tracker` в `~/avitoid_opreview/opreview/collect_sql.py`; сертификат `~/.avito/certs/personal.{crt,key}` |
| recaps (Х6) | репо `paglliac/mm-recaps`, чекаут `~/orca/workspaces/mm-recaps`: `recaps/mm.py` (клиент MM), `recaps/push.py` (WebSocket MM → FCM) |

Токены хаба лежат не в `.env`, а в таблице `credentials` в `/srv/sb/vault/tracker/tracker.db`.

## Правила

- Фаза 1 (К/П) старое не трогает: хаб, recaps, `~/mcp` работают как работали. Удаление старого — только в задачах Х.
- Значения токенов и cookie не читать, не копировать в репо и в чат — только имена ключей. Внешние токены вводятся на страничке мотора руками.
- Hermes и Codex остаются на `~/mcp/*` — эти файлы не удалять.

# Удалённый MCP-сервер для Claude Code («движок провайдеров» на gang.su)

Состояние на 2026-11-03. Источники — официальные доки Claude Code (code.claude.com, версии до v2.1.305), спецификация MCP (ревизия 2026-07-28), доки официального Python SDK (v2) и FastMCP, доки Caddy. Ссылки — внизу, у каждого пункта метка источника. **Не подтверждено** — то, чего в первоисточниках не нашёл.

Локальный факт: все скрипты в `~/mcp` импортируют `from fastmcp import FastMCP` (9 файлов) — это отдельный пакет FastMCP (gofastmcp.com), а не официальный `mcp` SDK.

## 1. Подключение Claude Code к удалённому серверу по HTTP

- Да, поддерживается; HTTP (streamable HTTP) — рекомендованный транспорт для удалённых серверов [cc-mcp]
  - SSE в Claude Code помечен deprecated: «The SSE (Server-Sent Events) transport is deprecated. Use HTTP servers instead, where available.» [cc-mcp]
  - в спецификации HTTP+SSE (2024-11-05) deprecated с 2025-03-26, новым реализациям «SHOULD NOT adopt» [spec-http]
- Команда [cc-mcp]:
  ```bash
  claude mcp add --transport http <name> <url>
  claude mcp add --transport http secure-api https://api.example.com/mcp \
    --header "Authorization: Bearer your-token"
  claude mcp add --transport http hubspot --scope user https://mcp.hubspot.com/anthropic
  ```
  - JSON-вариант: `claude mcp add-json <name> '<json>'`, принимает объект внутри `mcpServers`, без обёртки [cc-mcp]
    ```bash
    claude mcp add-json weather-api '{"type":"http","url":"https://api.weather.com/mcp","headers":{"Authorization":"Bearer token"}}'
    ```
- Форма записи (одинаковая для `.mcp.json`, `~/.claude.json`, `add-json`) [cc-mcp]
  ```json
  {
    "mcpServers": {
      "api-server": {
        "type": "http",
        "url": "${API_BASE_URL:-https://api.example.com}/mcp",
        "headers": {
          "Authorization": "Bearer ${API_KEY}"
        }
      }
    }
  }
  ```
  - `type` обязателен: запись с `url` без `type` читается как stdio и пропускается с ошибкой; `streamable-http` — алиас `http` [cc-mcp]
  - опционально `timeout` (мс) на сервер, `alwaysLoad`, `headersHelper`, `oauth` [cc-mcp]
- Скоупы [cc-mcp]
  | Scope | Где работает | Где хранится |
  | - | - | - |
  | local (по умолчанию) | только текущий проект | `~/.claude.json`, под путём проекта |
  | project | текущий проект, шарится через git | `.mcp.json` в корне проекта |
  | user | все проекты на машине | `~/.claude.json` |
  - «один раз для всех проектов» = `--scope user` [cc-mcp]
  - приоритет при совпадении имени: local > project > user > плагин > claude.ai-коннектор; запись берётся целиком, поля не мёржатся [cc-mcp]
  - сервер из конфига Claude Code перекрывает claude.ai-коннектор с тем же URL [cc-mcp]
  - `CLAUDE_CONFIG_DIR` переносит и `~/.claude`, и `.claude.json` [cc-dir]
- Имя сервера в `claude mcp`: только буквы, цифры, `-`, `_` [cc-mcp]

## 2. Авторизация

- Спека: авторизация в MCP **OPTIONAL**; если есть — для HTTP «SHOULD» следовать OAuth 2.1-схеме, токен только в `Authorization: Bearer …` на каждом запросе, не в query string [spec-auth]
- Статический bearer в `headers` — работает [cc-mcp]
  - подстановка переменных в `headers` и `url` поддерживается: `${VAR}` и `${VAR:-default}` [cc-mcp]
  - работает и в `~/.claude.json` (user/local): доки говорят, что для серверов «local, project, or user scope» `/mcp` и `claude mcp list/get` показывают `${VAR}` по имени, а не значение [cc-mcp]
  - незаданная переменная без default → конфиг грузится, но в заголовок уходит буквальный `${VAR}` + предупреждение в `claude mcp list` [cc-mcp]
  - ловушка: в `url`/`headers` удалённого сервера часть «кредовых» переменных всегда читается как пустая (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `AWS_BEARER_TOKEN_BEDROCK`, `HTTPS_PROXY`, `NPM_TOKEN` и т.п.); имя вне этого набора (пример — `API_KEY`) раскрывается [cc-mcp]
    - полный список «covered names» в доках не приведён — **не подтверждено**, попадёт ли туда своё имя вида `GANG_MCP_TOKEN`; проверка: `claude --debug-file /tmp/claude-debug.log`, искать `never expanded toward a remote server` [cc-mcp]
  - если сервер отвечает 401/403 на заданный `Authorization` — Claude Code считает подключение failed и **не** уходит в OAuth [cc-mcp]
  - сохранит ли `claude mcp add --header '…${VAR}…'` ссылку буквально (а не значение) — **не подтверждено**; надёжнее `add-json` в одинарных кавычках
- `headersHelper` — команда, печатающая JSON-объект заголовков [cc-mcp]
  ```json
  {
    "mcpServers": {
      "internal-api": {
        "type": "http",
        "url": "https://mcp.internal.example.com",
        "headersHelper": "/opt/bin/get-mcp-auth-headers.sh"
      }
    }
  }
  ```
  - запускается в shell при каждом подключении, таймаут 10 с, не кэшируется; перекрывает одноимённые статические `headers` [cc-mcp]
  - на 401/403 во время вызова: перезапуск хелпера, реконнект, один ретрай; при ошибке авторизации на первом подключении тоже ретраит, если `Authorization` идёт только от хелпера [cc-mcp]
  - env хелпера: `CLAUDE_CODE_MCP_SERVER_NAME`, `CLAUDE_CODE_MCP_SERVER_URL`, `CLAUDE_PLUGIN_ROOT` (только для плагина) [cc-mcp]
  - для user/local scope и `--mcp-config` кредовые переменные окружения хелперу **не** вырезаются; для project `.mcp.json` и плагинов — вырезаются (всё с `TOKEN`, `SECRET`, `PASSWORD`, `KEY`, `AUTH` в имени) [cc-mcp]
  - cwd хелпера для user scope — `~/.claude` (или `CLAUDE_CONFIG_DIR`) [cc-mcp]
  - для project/local scope хелпер запускается только после trust-диалога папки; в `-p` без trust — подключение только со статическими `headers` и строка `headersHelper not run` в stderr [cc-mcp]
- OAuth 2.0 — поддерживается [cc-mcp]
  - вход: `/mcp` в сессии или `claude mcp login <name>` (для `-p` — заранее, т.к. `/mcp` там нет); `--no-browser` для SSH [cc-mcp] [cc-cli]
  - преднастроенный клиент: `--client-id`, `--client-secret`, `--callback-port`; секрет в Keychain (macOS) или файле кредов [cc-mcp]
  - токены хранятся «securely», обновляются автоматически [cc-mcp]
  - v2-рантайм: шлёт OAuth-креды только на HTTPS (или localhost) token endpoint, проверяет `iss` [cc-mcp]
  - на сервере OAuth по спеке требует Protected Resource Metadata (RFC 9728) + отдельный authorization server [spec-auth]
- Что рекомендовано для single-user self-hosted — прямой рекомендации в доках нет
  - вывод из источников: статический bearer (через `${VAR}` или `headersHelper`) — минимум движущихся частей; OAuth требует поднять AS и PRM [spec-auth] [py-auth]
  - FastMCP называет `StaticTokenVerifier` dev-вариантом («stores tokens as plain text and should never be used in production»), для прода рекомендует OAuth [fm-token] [fm-http]

## 3. Распространение без `claude mcp add` на каждой машине

- Плагин — да [cc-mcp] [cc-plugin-ref]
  - MCP-серверы в `.mcp.json` в корне плагина или inline в `plugin.json` (`mcpServers`); стартуют автоматически при включении плагина [cc-mcp]
  - для `http`-серверов подстановки работают в `url`, `headers`, `headersHelper` [cc-mcp] [cc-plugin-ref]
  - токен через `userConfig`: поле с `"sensitive": true` спрашивается при включении, хранится в системном secure storage, подставляется как `${user_config.KEY}` в MCP-конфиг [cc-plugin-ref]
    ```json
    {
      "userConfig": {
        "api_token": {
          "type": "string",
          "title": "API token",
          "description": "API authentication token",
          "sensitive": true
        }
      }
    }
    ```
  - `headersHelper` из плагина `${user_config.*}` не принимает и опций в env не получает [cc-plugin-ref]
  - `claude plugin validate` предупреждает о `http://` на не-loopback хост и о литеральном креде в заголовке [cc-plugin-ref]
  - имена инструментов плагина: `mcp__plugin_<plugin>_<server>__<tool>` [cc-mcp]
  - установка: marketplace из GitHub/git/локальной папки, `claude plugin marketplace add …`, `claude plugin install <plugin>@<marketplace>` (по умолчанию user scope); в `-p` уже установленные плагины грузятся [cc-plugin-install]
  - автообновление сторонних marketplace по умолчанию **выключено** [cc-plugin-install]
  - как `userConfig` ведёт себя в headless на Linux (где «secure storage», спросит ли) — **не подтверждено**
- Managed settings — технически да, но это механизм для организаций [cc-managed]
  - `managedMcpServers` в managed-источнике (в т.ч. `managed-settings.json`): серверы добавляются к пользовательским, требует v2.1.259+ [cc-managed]
    - только `http`/`sse`, только `https://` (даже localhost по `http://` отклоняется), без `headersHelper`/`command`/`env`, **без** `${VAR}` — только литералы; заголовки читаемы всеми пользователями машины [cc-managed]
    - путь `managed-settings.json` на macOS/Linux на этой странице не приведён — **не подтверждено**
  - `managed-mcp.json` (`/Library/Application Support/ClaudeCode/`, `/etc/claude-code/`) даёт эксклюзивный контроль: отключает все прочие серверы, плагинные и `--mcp-config` (с ним `--mcp-config` вообще роняет старт на рабочей машине) — для нас не подходит [cc-managed]
- claude.ai-коннекторы — третий путь [cc-mcp]
  - сервер, добавленный на claude.ai/customize/connectors, автоматически появляется в Claude Code [cc-mcp]
  - но только при логине claude.ai-подпиской; **не** грузятся при `ANTHROPIC_API_KEY`, `apiKeyHelper` и при `CLAUDE_CODE_OAUTH_TOKEN` от `claude setup-token` — а именно так авторизован сервис `go` в этом репо (`group_vars/all/services.yml`) [cc-mcp]
  - требования claude.ai к кастомному коннектору (OAuth/authless, доступность из облака Anthropic) — **не проверял**

## 4. Поведение и лимиты

- Обновление списка инструментов (`list_changed`) [cc-mcp]
  - интерактивно: перечитывает tools/prompts/resources без реконнекта; в `-p` — только tools; при ошибке обновления оставляет старый список
  - v2-рантайм на ревизии 2026-07-28 получает уведомления через удерживаемый стрим; переоткрывает его: если закрылся <10 с — до 3 раз; если жил дольше — после 5 переоткрытий за час ждёт ~6 ч [cc-mcp]
  - в спеке 2026-07-28 такие уведомления идут по ответному SSE-стриму запроса `subscriptions/listen`; рекомендуются keep-alive комментарии `:` [spec-http]
- Таймауты [cc-env] [cc-mcp]
  - `MCP_TIMEOUT` — старт сервера, по умолчанию 30000 мс
  - `MCP_TOOL_TIMEOUT` — выполнение инструмента, по умолчанию 100000000 (~28 ч); **но для HTTP каждый запрос по умолчанию рвётся через 60 с** — поднимать через `MCP_TOOL_TIMEOUT` или per-server `timeout` > 60000
  - `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` — нет ответа и нет progress: 5 мин для сетевых серверов
  - вызов дольше 2 мин в интерактиве уходит в фоновую задачу (`CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS`); в `-p` — нет (кроме `CLAUDE_AUTO_BACKGROUND_TASKS=1`)
- Размер вывода [cc-mcp] [cc-env]
  - предупреждение >10 000 токенов (порог фиксирован), лимит `MAX_MCP_OUTPUT_TOKENS` = 25 000; сверх лимита результат пишется в файл в `tool-results/`, Claude получает путь
  - текст >50 000 символов → в файл независимо от токенов; инструмент может поднять порог через `_meta["anthropic/maxResultSizeChars"]` до 500 000
  - ответ HTTP-сервера >16 MiB → запрос падает
  - текст ошибки (`isError`) >~11 000 символов обрезается до первых и последних 5 000
- Старт и недоступный сервер [cc-env] [cc-mcp] [cc-cli]
  - старт неблокирующий по умолчанию (`MCP_CONNECTION_NONBLOCKING`); в `-p` первый ход ждёт pending-серверы (с `--mcp-config` — до `MCP_TIMEOUT`)
  - первое подключение HTTP: при транзиентной ошибке (5xx, refused, timeout) 3 ретрая → failed; ошибки auth/404 не ретраятся
  - с tool search Claude сообщают, какой сервер упал и почему; без tool search — не сообщают
  - обрыв посреди сессии: 5 попыток с backoff 1с×2; дальше интерактив — failed + `/mcp` (или `/mcp reconnect all`); `-p` — продолжает без инструментов сервера и ретраит в фоне до ~30 мин
  - `MCP_REMOTE_SERVER_CONNECTION_BATCH_SIZE` — параллельных удалённых подключений на старте, по умолчанию 20
- Discovery-кэш: удалённый сервер может грузить список инструментов из кэша и подключаться при первом вызове (`MCP_DISCOVERY_CACHE`, по умолчанию выключен, если не включён rollout'ом) [cc-env] [cc-mcp]

## 5. Один сервер со всеми провайдерами или много серверов

- Жёсткого лимита на число инструментов нет: «Claude Code doesn't impose a fixed per-server tool cap; the practical limit is your context window budget» [cc-mcp]
- Tool search (по умолчанию включён): на старте грузятся только имена инструментов и instructions сервера, описания — по требованию [cc-mcp]
  - `ENABLE_TOOL_SEARCH`: unset/`true` — откладывать; `auto`/`auto:N` — порог 10%/N% контекста; `false` — всё сразу [cc-env]
  - per-server `"alwaysLoad": true|false` в конфиге; per-tool `_meta["anthropic/alwaysLoad"]` на стороне сервера [cc-mcp]
  - instructions сервера и описания upfront-инструментов режутся на 4096 символов, описания отложенных — на 16384 (`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`) [cc-mcp]
  - серверу с многими провайдерами стоит писать внятные `instructions` — по ним Claude решает, когда искать инструменты [cc-mcp]
- Имена: `mcp__<server>__<tool>`; инструмент с полным именем >128 символов выкидывается из запроса [cc-mcp]
- Что зависит от числа серверов (вывод из [cc-mcp] [cc-perm])
  - per-server: URL, заголовки/токен, `timeout`, `alwaysLoad`, вкл/выкл в `/mcp`, статус failed/reconnect
  - права: allow-правило `mcp__<server>__*` или `mcp__<server>__get_*` (глоб только после литерального префикса сервера); deny/ask принимают глобы шире [cc-perm]
  - один сервер `gang` → одна запись конфигурации на машине, инструменты `mcp__gang__mm_search` и т.п.; отключить один провайдер можно только deny-правилами, не тумблером `/mcp`
  - официальный Python SDK умеет монтировать несколько MCP-серверов в одно ASGI-приложение под разными префиксами (`Mount("/notes", …)`, `Mount("/tasks", …)`) — т.е. один процесс, но несколько endpoint'ов [py-asgi]; аналог в FastMCP — **не проверял**

## 6. Headless `claude -p` на сервере

- Без `--bare` `-p` подхватывает всё как интерактив: user/local scope из `$CLAUDE_CONFIG_DIR/.claude.json`, project `.mcp.json` (без approval-промпта, даже в недоверенной папке), плагины [cc-headless] [cc-dir]
- `--mcp-config <файлы или JSON-строки>`; `--strict-mcp-config` — только они, остальное игнорируется [cc-cli]
  ```bash
  claude -p --strict-mcp-config --mcp-config ./mcp.json "…"
  ```
  - кредовые переменные хелперу не вырезаются для `--mcp-config` [cc-mcp]
  - невалидные записи пропускаются, видны в `system/init` → `mcp_server_errors` (`--output-format stream-json`); статус серверов — `mcp_servers` [cc-headless]
- `--bare`: не читает ни `~/.claude`, ни `.mcp.json`, MCP только из `--mcp-config`; **но не читает OAuth-креды/keychain** — нужен `ANTHROPIC_API_KEY` или `apiKeyHelper` [cc-headless]
  - работает ли `CLAUDE_CODE_OAUTH_TOKEN` в `--bare` — **не подтверждено**; без `--bare` текущая схема `go` (setup-token) работает
- Bearer: `${VAR}` в `--mcp-config` раскрывается из env процесса (сервис уже получает `.env` через systemd) — по общему правилу подстановки [cc-mcp]; отдельного примера для `--mcp-config` в доках нет
- Разрешения в `-p`: `--allowedTools "mcp__gang__*"`; по умолчанию с v2.1.302 Manual mode — неразрешённое отклоняется [cc-headless] [cc-perm]
- OAuth-сервер в `-p`: только после `claude mcp login <name>` в shell; при истечении — `needs-auth`, ретраи прекращаются [cc-mcp]
- `--mcp-config` с `http://127.0.0.1:<port>/mcp` (мимо Caddy): запрет plain http явно описан только для `managedMcpServers` — для `--mcp-config`/user scope ограничения не нашёл (вывод, **не подтверждено** явно) [cc-managed]

## 7. Серверная сторона: Python + Caddy

- Спека 2026-07-28 [spec-http]
  - один endpoint, только POST; каждый JSON-RPC — отдельный POST; ответ `application/json` или SSE-стрим, привязанный к запросу
  - **сессии (`Mcp-Session-Id`) и GET-стрим удалены**, резюм по `Last-Event-ID` не поддерживается; server→client (elicitation/sampling) — через `InputRequiredResult` (MRTR)
  - обязательные заголовки: `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`; несовпадение с телом → 400 `HeaderMismatch`
  - сервер MUST проверять `Origin` (403 при невалидном), SHOULD требовать аутентификацию
  - для SSE SHOULD слать `X-Accel-Buffering: no`
  - старые клиенты (2025-03-26…2025-11-25) — с сессиями и GET; сервер «только 2026» отвечает 405 на GET/DELETE
- Claude Code: v2-рантайм (TS SDK 2.0) пробует 2026-07-28 и откатывается на старый handshake; переключатели `MCP_SDK_GENERATION`, `MCP_PROTOCOL_NEGOTIATION` [cc-mcp] [cc-env]
- Официальный Python SDK v2 (`pip install mcp`) [py-new] [py-legacy] [py-deploy] [py-asgi]
  - `FastMCP` переименован в `MCPServer`, путь `mcp.server.fastmcp` удалён, не deprecated
  - `streamable_http_app()` обслуживает обе эры на одном `/mcp`, отключить эру нельзя
  - для 2026-клиентов stateless «by construction»; `stateless_http=True` влияет только на legacy-клиентов (без сессии, но server→client запросы падают `NoBackChannelError`, уведомления теряются); `json_response=True` — похожая цена для legacy
  - endpoint по умолчанию `/mcp`; `streamable_http_app(streamable_http_path="/")` делает путь монтирования публичным; встречается редирект `/mcp → /mcp/`
  - **гоча за прокси**: по умолчанию принимает только Host `127.0.0.1/localhost/[::1]:<port>`, иначе 421 — нужен `TransportSecuritySettings(allowed_hosts=[...])` или `enable_dns_rebinding_protection=False`, если Host контролирует прокси
  - при монтировании в Starlette lifespan под-приложения не выполняется — обязательно `async with mcp.session_manager.run():`, иначе `RuntimeError: Task group is not initialized`
  - за TLS-прокси: `uvicorn server:app --proxy-headers --forwarded-allow-ips='<proxy address>'`
  - auth: `TokenVerifier` + `AuthSettings(issuer_url, resource_server_url, …)` обязательно вместе; простого «статического токена» режима нет (есть пример `StaticTokenVerifier` со словарём); custom routes никогда не аутентифицируются [py-auth] [py-asgi]
- FastMCP (то, что в `~/mcp`) [fm-http] [fm-token]
  - `mcp.run(transport="http", host=…, port=…)` или `app = mcp.http_app()` + uvicorn; путь по умолчанию `/mcp`, меняется `path=`
  - `stateless_http=True` / `FASTMCP_STATELESS_HTTP=true` — без session affinity
  - при монтировании в Starlette/FastAPI передать `lifespan=mcp_app.lifespan`
  - Host/Origin-защита по умолчанию выключена (`host_origin_protection=False`); включается вместе с `FASTMCP_HTTP_ALLOWED_HOSTS`
  - auth: `StaticTokenVerifier` (dev), `JWTVerifier` (в т.ч. HS256 с общим секретом), `DebugTokenVerifier(validate=…)`; `@mcp.custom_route("/health", …)` не защищается auth
  - упоминает заголовки 2026-07-28 (`Mcp-Method`, `Mcp-Name`) — значит, ревизию знает; поведение `stateless_http` для 2026-клиентов отдельно не описано — **не подтверждено**
  - nginx-рекомендации: `proxy_buffering off`, таймауты ≥300 с
- Caddy [caddy-rp] [caddy-match]
  - `reverse_proxy` сам сбрасывает буфер сразу для `text/event-stream` и при неизвестном Content-Length — отдельный `flush_interval -1` не нужен
  - по умолчанию нет `read_timeout`/`write_timeout`/`stream_timeout` — долгие стримы не рвутся
  - Host проксируется как есть → для официального SDK нужен `allowed_hosts` с публичным доменом; `X-Forwarded-*` выставляются Caddy
  - проброс `Authorization` явно не описан; доки говорят, что входящие заголовки проходят без изменений
  - bearer-проверку можно сделать и в Caddy матчером `header Authorization "Bearer …"` (точное совпадение) с `respond 401` для остальных; пример собран из синтаксиса, в доках такого примера нет

## Что из этого следует для gang.su (вывод, не цитата)

- Мак: одна user-scope запись `type: http` на `https://<поддомен>.gang.su/mcp`; токен через `headersHelper` (например, из Keychain) или `${VAR}` с некредовым именем
- Сервисы на сервере (`claude: true`): `--strict-mcp-config --mcp-config <файл>` с `http://127.0.0.1:<port>/mcp` и токеном из `.env` сервиса; claude.ai-коннектор им не подойдёт (setup-token)
- Для медленных провайдеров (транскрипты и т.п.) — per-server `timeout` > 60000, иначе 60-секундный лимит на HTTP-запрос
- Сервер: stateless, auth в приложении (а не только в Caddy — локальные сервисы ходят мимо Caddy), `/health` вне auth, внятные `instructions` под tool search

## Источники

[cc-mcp]: https://code.claude.com/docs/en/mcp
[cc-managed]: https://code.claude.com/docs/en/managed-mcp
[cc-plugin-ref]: https://code.claude.com/docs/en/plugins-reference
[cc-plugin-install]: https://code.claude.com/docs/en/plugins/install
[cc-cli]: https://code.claude.com/docs/en/cli-reference
[cc-env]: https://code.claude.com/docs/en/env-vars
[cc-headless]: https://code.claude.com/docs/en/headless
[cc-dir]: https://code.claude.com/docs/en/claude-directory
[cc-perm]: https://code.claude.com/docs/en/permissions
[spec-http]: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
[spec-auth]: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
[py-new]: https://py.sdk.modelcontextprotocol.io/whats-new/
[py-legacy]: https://py.sdk.modelcontextprotocol.io/run/legacy-clients/
[py-deploy]: https://py.sdk.modelcontextprotocol.io/run/deploy/
[py-asgi]: https://py.sdk.modelcontextprotocol.io/run/asgi/
[py-auth]: https://py.sdk.modelcontextprotocol.io/run/authorization/
[fm-http]: https://gofastmcp.com/deployment/http
[fm-token]: https://gofastmcp.com/servers/auth/token-verification
[caddy-rp]: https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
[caddy-match]: https://caddyserver.com/docs/caddyfile/matchers

- cc-mcp — https://code.claude.com/docs/en/mcp
- cc-managed — https://code.claude.com/docs/en/managed-mcp
- cc-plugin-ref — https://code.claude.com/docs/en/plugins-reference
- cc-plugin-install — https://code.claude.com/docs/en/plugins/install
- cc-cli — https://code.claude.com/docs/en/cli-reference
- cc-env — https://code.claude.com/docs/en/env-vars
- cc-headless — https://code.claude.com/docs/en/headless
- cc-dir — https://code.claude.com/docs/en/claude-directory
- cc-perm — https://code.claude.com/docs/en/permissions
- spec-http — https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- spec-auth — https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- py-new — https://py.sdk.modelcontextprotocol.io/whats-new/
- py-legacy — https://py.sdk.modelcontextprotocol.io/run/legacy-clients/
- py-deploy — https://py.sdk.modelcontextprotocol.io/run/deploy/
- py-asgi — https://py.sdk.modelcontextprotocol.io/run/asgi/
- py-auth — https://py.sdk.modelcontextprotocol.io/run/authorization/
- fm-http — https://gofastmcp.com/deployment/http
- fm-token — https://gofastmcp.com/servers/auth/token-verification
- caddy-rp — https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
- caddy-match — https://caddyserver.com/docs/caddyfile/matchers

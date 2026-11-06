---
title: Опись — что есть сейчас
type: wayfinder:research
status: closed
assignee: claude
blocked_by: []
---

## Question

Для каждого поставщика (Mattermost, Confluence, ktalk, Exchange-календарь, ZenMoney, tracker, people, inbox, triage, unit, perf_review и всё, что найдётся ещё): где лежит его код, сколько копий, кто его использует (MCP на маке, сервисы на `gang.su`), где лежат токены и сколько у них копий, что поставщик умеет (читать/писать).

Где смотреть: SecondBrain (`~/SecondBrain`, `~/SecondBrain-new`, деплой на `gang.su`), `~/mcp`, `~/.claude.json`, репо сервисов из `group_vars/all/services.yml` (recaps, budget, go), `~/orca/projects/*` (personal-review, personal-assistant, MattermostImportant, …).

## Research

Находки — `docs/research/opis.md`.

## Решение

Подробно, с путями — `docs/research/opis.md`.

- **Три независимых способа связи с поставщиками, кода между ними нет общего**
  - **через хаб** (`https://gang.su/api/...`, один `SB_API_TOKEN` на всех): tracker, people, inbox, triage, calendar, transcripts и ещё одна дорога в Mattermost;
  - **напрямую с мака:** `mattermost_global`, Confluence, unit (локальный файл), perf_review (локальный SQLite);
  - **напрямую из сервисов на gang.su:** budget ходит в ZenMoney, mm-recaps — в Mattermost.
- **Хаб**
  - на сервере работает `~/SecondBrain-new` (репо `tasks17`), лежит в `/srv/sb`;
  - `~/SecondBrain` — старая копия без remote, используется только её `.mcp.json`;
  - токены хаба лежат не в `.env`, а в таблице `credentials` в `/srv/sb/vault/tracker/tracker.db`: MM, KTALK_*, EWS_*, TODOIST, CLAUDE_CODE_OAUTH_TOKEN;
  - код ktalk и Exchange есть только в хабе, токены к ним — только на сервере; оба отвечают только с адреса сервера, мимо `awg0`.
- **Mattermost**
  - 8 копий клиента, 6 из них живые, ни одна не общая: хаб, `~/mcp/mattermost`, mm-recaps `recaps/mm.py`, MattermostImportant, mattermost-macos, Android;
  - два MCP (`mattermost_global` и `sb_mattermost_global`) отдают одни и те же 10 инструментов, но с разными токенами;
  - на маке минимум 2 разных личных токена и cookie, разложенные по 7+ файлам;
  - `~/mcp/mattermost/.env` читают и другие программы: MattermostImportant, скрипты Screen Sprite, а при деплое из него заполняли `/srv/recaps/.env`.
- **`~/mcp/secondbrain/*` уже разошлись с оригиналами в обе стороны**
  - в глобальных копиях нет `run_capture` и трёх инструментов agenda;
  - 4 инструмента triage для групп вызывают адреса, которых на gang.su нет;
  - оригиналы из `SecondBrain-new/server/mcp` нигде не подключены.
- **Те же `~/mcp/*` подключены не только в Claude Code**, но и в Hermes (`~/.hermes/config.yaml`, работает под launchd) и в Codex (`~/.codex/config.toml`).
- **Confluence — три разных пути**
  - `cf.avito.ru` через `mcp-atlassian`, глобально, умеет только комментировать;
  - личный `*.atlassian.net` через `mcp-atlassian`, работает только в сессиях `~/SecondBrain`;
  - корпоративный `avito mcp` пишет страницы целиком, им пользуется скилл `maturity-rollup`.
- **unit и perf_review** — только файлы на маке, без API и без сервера. Списков людей три, и они не связаны: people в хабе, `persons` в unit, оргструктура на org.avito.ru.
- **Устаревшие копии ключей:** минимум в 5 местах на маке, из них два в Obsidian Vault с правами 0644.
- **Не проверено**
  - совпадают ли токены на сервере между собой и с копиями на маке;
  - откуда подгружается `~/.claude/avito.mcp.json`;
  - почему пуст `/srv/go/.env`.

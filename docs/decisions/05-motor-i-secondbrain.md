---
title: Мотор и SecondBrain
type: wayfinder:grilling
status: closed
assignee: claude
blocked_by: ["01", "04"]
---

## Question

Мотор вырастает из хаба SecondBrain (он уже отдаёт tracker, people, ktalk, календарь по HTTP), или это новый сервис, а хаб становится одним из его потребителей?

## Решение

Мотор — новая программа, не хаб.

- **Всё, что ходит наружу, уезжает из хаба в мотор**: Mattermost, ktalk, Exchange, Todoist, оргструктура (org.avito.ru). У хаба не остаётся внешних кодов и токенов — он берёт их данные через мотор, как recaps.
- **Хаб — один из поставщиков мотора.** Свои данные хаба (tracker, people, inbox, triage) остаются в хабе; у мотора есть их адреса (`…/tracker`, …), за которыми он ходит в HTTP хаба.
- Значит, хаб одновременно потребитель мотора (Mattermost, календарь, …) и поставщик для него (tracker, people, …).

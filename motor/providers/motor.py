"""`motor` — the motor about itself: who is calling and how every provider is doing.

Has no external side, so it is the first thing to try with a fresh token:
`curl -H "Authorization: Bearer $MOTOR_TOKEN" https://motor.gang.su/motor/status`.
"""
from __future__ import annotations

from ..provider import Provider, current_consumer

motor = Provider(
    "motor",
    "Мотор",
    writes=False,
    instructions=(
        "Служебный поставщик мотора motor.gang.su: под каким именем ты пришёл и в каком состоянии "
        "остальные поставщики (работает / протух / лежит). Звать, когда другой поставщик мотора "
        "отвечает ошибкой."
    ),
)


@motor.action
def status() -> dict:
    """Кто вызывает (имя потребителя) и состояние каждого поставщика мотора: работает / протух / лежит,
    текст ошибки и когда последний раз отвечал."""
    consumer = current_consumer.get()
    providers = []
    for provider in motor.peers:
        if provider is motor:
            continue
        state = provider.store.provider_state(provider.name)
        providers.append({
            "name": provider.name,
            "title": provider.title,
            "writes": provider.writes,
            "status": state.status if state else "unknown",
            "message": state.message if state else "",
            "last_ok_at": state.last_ok_at if state else None,
        })
    return {"consumer": consumer.name if consumer else None, "providers": providers}

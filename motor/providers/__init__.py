"""Every provider the motor serves. Adding one: a module here, then its object in PROVIDERS
(docs/motor.md, «Как добавить поставщика»)."""
from .motor import motor

PROVIDERS = [motor]

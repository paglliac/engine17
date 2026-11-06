"""Every provider the motor serves. Adding one: a module here, then its object in PROVIDERS
(docs/motor.md, «Как добавить поставщика»)."""
from .mattermost import mattermost
from .motor import motor

PROVIDERS = [motor, mattermost]

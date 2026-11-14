"""Every provider the motor serves. Adding one: a module here, then its object in PROVIDERS
(docs/motor.md, «Как добавить поставщика»)."""
from .calendar import calendar
from .family import family
from .inbox import inbox
from .ktalk import ktalk
from .mattermost import mattermost
from .motor import motor
from .people import people
from .todoist import todoist
from .tracker import tracker
from .triage import triage

PROVIDERS = [motor, mattermost, calendar, family, ktalk, todoist, tracker, people, inbox, triage]

"""Shared BotState enum to avoid circular imports between bot.py and mixins."""
from enum import Enum, auto


class BotState(Enum):
    IDLE = auto()
    SCANNING = auto()
    BUYING = auto()
    IN_POSITION = auto()
    EXITING = auto()

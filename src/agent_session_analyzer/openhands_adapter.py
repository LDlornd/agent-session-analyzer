"""Compatibility imports; new code lives in adapters.openhands."""
from .schema import SCHEMA
from .adapters.openhands import (OpenHandsAdapter, as_dict, now_ms, text_content, first_value, normalize_usage, message_fields, response_fields)

"""Compatibility imports; new code lives in adapters.dsh."""
from .schema import SCHEMA
from .adapters.dsh import TOKEN_FIELDS, DshAdapter, content_text, usage_fields, stream_info

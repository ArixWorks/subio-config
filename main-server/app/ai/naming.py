"""Deterministic SubIO config naming.

Display names must follow the fixed, reportable convention
"{flag}[⭐] @Config_SubBOT #{COUNTRY}{CODE}" (see scoring_service.format_config_name)
so users can always extract a stable code for the "report a problem" flow.
AI free-form naming was removed on purpose: an LLM-generated name is not
guaranteed to preserve the #CODE token, which would break report parsing.
"""

from __future__ import annotations

from app.services.scoring_service import format_config_name

__all__ = ["format_config_name"]

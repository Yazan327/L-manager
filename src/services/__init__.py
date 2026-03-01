"""
Services module for L-Manager
Contains business logic services including permissions, caching, and more.
"""

from .permissions import PermissionService, check_access, list_effective_permissions
from .i18n import (
    SUPPORTED_LANGUAGES,
    DEFAULT_LANGUAGE,
    get_language,
    set_language,
    translate,
    get_dictionary,
    get_direction,
    localize_legacy_message,
    localize_error_payload,
    get_error_code_for_legacy_message,
)

__all__ = [
    'PermissionService',
    'check_access',
    'list_effective_permissions',
    'SUPPORTED_LANGUAGES',
    'DEFAULT_LANGUAGE',
    'get_language',
    'set_language',
    'translate',
    'get_dictionary',
    'get_direction',
    'localize_legacy_message',
    'localize_error_payload',
    'get_error_code_for_legacy_message',
]

# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""
The platform-docs visibility grant (#16664).

AutoBot's own documentation lives in the ``autobot_docs`` ChromaDB collection, outside the facts
store, so no ``knowledge_facts`` row or ownership index ever stamped it. Its writer stamps this
grant into every chunk's metadata instead, and the one read decision
(``KnowledgeOwnership.check_access`` for a signed-in user, ``is_non_private`` for ``SYSTEM_SCOPE``)
then admits it without a second filter or a special case.
"""

from knowledge.ownership import AccessLevel, VisibilityLevel

#: ``access_level: autobot`` is "platform documentation, available to every authenticated user"
#: (``AccessLevel.AUTOBOT``); ``visibility: system`` makes it platform-wide for the non-private
#: scope too. Chunks indexed before this grant existed carry neither key and read as private,
#: so a deploy re-indexes the collection through the builtin updater.
PLATFORM_DOCS_GRANT = {
    "visibility": VisibilityLevel.SYSTEM.value,
    "access_level": AccessLevel.AUTOBOT.value,
}

# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one upload-filename policy, shared by every upload route (#18704).

``api/files.py`` and ``api/conversation_files.py`` each carried an
``is_safe_file``. Only the general file API refused a name with a path
component, so a conversation upload accepted ``a/b.txt``; their allow-lists
also drifted although both said they were "consistent with" the other. Owner
decision 2026-10-17: the strict rules for both, one allow-list (the union:
the general list plus ``.xls``, which conversation uploads already took).
"""

from pathlib import Path

# Allowed file extensions for security
ALLOWED_EXTENSIONS = {
    # Text and data formats
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".csv",
    ".log",
    ".cfg",
    ".ini",
    ".conf",
    # Code files
    ".py",
    ".js",
    ".ts",
    ".sh",
    ".bat",
    ".sql",
    # Web files
    ".html",
    ".css",
    # Office document formats (Microsoft)
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    # Office document formats (OpenDocument/LibreOffice)
    ".odt",  # OpenDocument Text (Writer)
    ".ods",  # OpenDocument Spreadsheet (Calc)
    ".odp",  # OpenDocument Presentation (Impress)
    ".odg",  # OpenDocument Graphics (Draw)
    # Image formats
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
}

# Issue #380: Module-level frozenset for dangerous filename characters
_DANGEROUS_FILENAME_CHARS = frozenset({"<", ">", '"', "|", "?", "*", "\0", "\r", "\n"})

# Issue #398: Module-level sets for is_safe_file validation
_DANGEROUS_FILENAMES = frozenset(
    {
        ".htaccess",
        ".env",
        "passwd",
        "shadow",
        "config",
        "web.config",
        "autoexec.bat",
        "boot.ini",
        "hosts",
        "httpd.conf",
        "nginx.conf",
    }
)

_DANGEROUS_EXTENSIONS = frozenset(
    {
        ".exe",
        ".bat",
        ".cmd",
        ".com",
        ".scr",
        ".pif",
        ".vbs",
        ".js",
        ".jar",
        ".app",
        ".deb",
        ".rpm",
        ".dmg",
        ".pkg",
        ".msi",
    }
)


def is_safe_file(filename: str) -> bool:
    """
    Check if file type is allowed and filename is safe.

    Issue #398: Refactored to use module-level constants.
    """
    if not filename:
        return False

    # Check for dangerous characters (Issue #380: module-level constant)
    if any(char in filename for char in _DANGEROUS_FILENAME_CHARS):
        return False

    # An uploaded filename is a bare name, never a path (#13394). Backslash is
    # checked explicitly because Path.name is platform-dependent — on POSIX
    # "dir\\evil.py" is one valid component (#13162).
    if "/" in filename or "\\" in filename or Path(filename).name != filename:
        return False

    if len(filename) > 255:
        return False

    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return False

    # Issue #398: Use module-level sets
    if filename.lower() in _DANGEROUS_FILENAMES:
        return False

    if extension in _DANGEROUS_EXTENSIONS and extension not in ALLOWED_EXTENSIONS:
        return False

    # Prevent null bytes and control characters
    if "\0" in filename or any(ord(c) < 32 for c in filename):
        return False

    return True

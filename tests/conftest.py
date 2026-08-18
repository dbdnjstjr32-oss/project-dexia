"""Shared pytest setup for the dexia test suite.

Auth now fails closed by default (no working credentials are shipped in source
or config). The funnel tests drive the control plane with the built-in
``dexia-commander`` / ``dexia-operator`` keys, so enable the explicit dev opt-in
for the whole suite. Set before any module imports ``dexia.api.auth`` so the
flag is visible when the principal table is first resolved.
"""

import os

os.environ.setdefault("DEXIA_ALLOW_DEFAULT_KEYS", "1")

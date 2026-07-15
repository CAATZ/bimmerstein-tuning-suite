"""Built-in communication protocols and their registry side effects."""
from __future__ import annotations

# Import shipped protocols once so composition through PROTOCOLS works in source, wheel, and
# frozen builds without relying on an unrelated module-import order.
from ecueditor.core.comms.protocol import ds2 as _ds2  # noqa: F401

"""Group channel adapter: interface only (internal).

Meta's Groups API is open only to Official Business Accounts and limits a group to 8
participants, so most chamas cannot use it. The in-app feed plus push is the guaranteed group
channel; an adapter that posts a feed summary into a WhatsApp group (or a chama's own webhook)
can implement this protocol later without changes elsewhere. No implementation is configured.
"""

import uuid
from typing import Protocol


class GroupChannel(Protocol):
    def post(self, chama_id: uuid.UUID, kind: str, text: str) -> bool:
        """Post one short, member-safe line to the chama's group. True when accepted."""
        ...


def group_channel_for(chama_id: uuid.UUID) -> GroupChannel | None:
    """The chama's configured group channel. None until an adapter is verified and enabled."""
    return None

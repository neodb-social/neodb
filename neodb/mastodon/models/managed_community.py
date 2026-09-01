"""The role-qualified SocialAccount used for the managed Community edge.

This is a typed proxy, not a second account table.  Its inherited token
fields remain protected by ``EncryptedTextField`` while its no-op sync hooks
keep ordinary NeoDB profile synchronisation away from the managed account.
"""

from .mastodon import MastodonAccount


class ManagedVinylHubCommunityAccount(MastodonAccount):
    class Meta:
        verbose_name = "Managed VinylHub Community account"

    def sync(self, *args, **kwargs) -> bool:
        return False

    def check_alive(self, *args, **kwargs) -> bool:
        return False

    def refresh(self, *args, **kwargs) -> bool:
        return False

    def sync_graph(self, *args, **kwargs) -> int:
        return 0

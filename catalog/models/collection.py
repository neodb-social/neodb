from typing import TYPE_CHECKING

from .item import Item, ItemCategory


class Collection(Item):
    if TYPE_CHECKING:
        from journal.models import Collection as JournalCollection

        journal_item: "JournalCollection"
    category = ItemCategory.Collection

    @property
    def url(self):
        return self.journal_item.url if hasattr(self, "journal_item") else super().url

    @property
    def owner_id(self):
        return self.journal_item.owner_id if self.journal_item else None

    def to_indexable_doc(self):
        return {}

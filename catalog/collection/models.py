from catalog.common import *


class Collection(Item):
    category = ItemCategory.Collection

    @property
    def owner_id(self):
        return self.journal_item.owner_id if self.journal_item else None

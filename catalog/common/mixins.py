class SoftDeleteMixin:
    """
    SoftDeleteMixin

    Model must add this:
    is_deleted = models.BooleanField(default=False, db_index=True)

    Model may override this:
    def clear(self):
        pass
    """

    def clear(self):
        pass

    def delete(self, using=None, keep_parents=False, soft=True, *args, **kwargs):
        if soft:
            self.clear()
            self.is_deleted = True
            self.save(using=using)  # type: ignore
            return 0, {}
        else:
            return super().delete(using=using, keep_parents=keep_parents, *args, **kwargs)  # type: ignore

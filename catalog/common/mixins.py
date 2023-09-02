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

    def delete(self, using=None, soft=True, *args, **kwargs):
        if soft:
            self.clear()
            self.is_deleted = True
            self.save(using=using)  # type: ignore
        else:
            return super().delete(using=using, *args, **kwargs)  # type: ignore

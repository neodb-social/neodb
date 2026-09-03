import pickle
from datetime import date, datetime, time
from datetime import timezone as dt_tz

import pytest
from django.utils import timezone

from catalog.models import Item, Movie, People, PeopleType
from common.models.jsondata import (
    DateField,
    DateTimeField,
    EncryptedTextField,
    JSONField,
    JSONFieldMixin,
    TimeField,
    decrypt_str,
    encrypt_str,
)
from mastodon.models import MastodonAccount


class TestEncryptDecrypt:
    def test_roundtrip(self):
        original = "hello world"
        encrypted = encrypt_str(original)
        assert encrypted != original
        decrypted = decrypt_str(encrypted)
        assert decrypted == original

    def test_empty_string(self):
        encrypted = encrypt_str("")
        decrypted = decrypt_str(encrypted)
        assert decrypted == ""

    def test_unicode_roundtrip(self):
        original = "Hello, multi-language text"
        encrypted = encrypt_str(original)
        decrypted = decrypt_str(encrypted)
        assert decrypted == original


class TestEncryptedTextField:
    def setup_method(self):
        self.field = EncryptedTextField()

    def test_to_json_with_value(self):
        result = self.field.to_json("secret")
        assert result is not None
        assert result != "secret"
        # verify we can decrypt it
        assert decrypt_str(result) == "secret"

    def test_to_json_with_none(self):
        result = self.field.to_json(None)
        assert result is None

    def test_to_json_with_empty_string(self):
        result = self.field.to_json("")
        assert result is None

    def test_from_json_with_value(self):
        encrypted = encrypt_str("secret")
        result = self.field.from_json(encrypted)
        assert result == "secret"

    def test_from_json_with_none(self):
        result = self.field.from_json(None)
        assert result is None

    def test_from_json_with_empty_string(self):
        result = self.field.from_json("")
        assert result is None


class TestDateField:
    def setup_method(self):
        self.field = DateField()

    def test_to_json_with_date(self):
        d = date(2024, 1, 15)
        assert self.field.to_json(d) == "2024-01-15"

    def test_to_json_with_datetime(self):
        dt = datetime(2024, 1, 15, 12, 30)
        assert self.field.to_json(dt) == "2024-01-15"

    def test_to_json_with_string(self):
        result = self.field.to_json("2024-01-15")
        assert result == "2024-01-15"

    def test_to_json_with_none(self):
        result = self.field.to_json(None)
        assert result is None

    def test_to_json_with_empty_string(self):
        result = self.field.to_json("")
        assert result is None

    def test_from_json_with_value(self):
        result = self.field.from_json("2024-01-15")
        assert result == date(2024, 1, 15)

    def test_from_json_with_none(self):
        result = self.field.from_json(None)
        assert result is None


class TestDateTimeField:
    def setup_method(self):
        self.field = DateTimeField()

    def test_to_json_with_aware_datetime(self):
        dt = timezone.now()
        result = self.field.to_json(dt)
        assert result is not None
        assert "T" in result

    def test_to_json_with_naive_datetime(self):
        dt = datetime(2024, 1, 15, 12, 30)
        result = self.field.to_json(dt)
        assert result is not None
        # should have made it aware
        assert "+" in result or "Z" in result

    def test_to_json_with_date(self):
        d = date(2024, 1, 15)
        result = self.field.to_json(d)
        assert result is not None
        # date should be converted to datetime
        assert "T" in result

    def test_to_json_with_valid_string(self):
        result = self.field.to_json("2024-01-15")
        assert result is not None

    def test_to_json_with_invalid_string(self):
        with pytest.raises(ValueError, match="invalid datetime format"):
            self.field.to_json("not-a-date")

    def test_from_json_with_value(self):
        result = self.field.from_json("2024-01-15T12:30:00+00:00")
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_from_json_with_none(self):
        result = self.field.from_json(None)
        assert result is None

    def test_from_json_with_empty_string(self):
        result = self.field.from_json("")
        assert result is None


class TestTimeField:
    def setup_method(self):
        self.field = TimeField()

    def test_to_json_with_aware_time(self):
        t = time(12, 30, 0, tzinfo=dt_tz.utc)
        result = self.field.to_json(t)
        assert result is not None
        assert "12:30" in result

    def test_to_json_with_naive_time(self):
        t = time(12, 30, 0)
        result = self.field.to_json(t)
        assert result is not None

    def test_to_json_with_none(self):
        result = self.field.to_json(None)
        assert result is None

    def test_from_json_with_value(self):
        result = self.field.from_json("12:30:00")
        assert isinstance(result, time)
        assert result.hour == 12
        assert result.minute == 30

    def test_from_json_with_none(self):
        result = self.field.from_json(None)
        assert result is None

    def test_from_json_with_empty_string(self):
        result = self.field.from_json("")
        assert result is None


class TestChildModelFieldBinding:
    """Virtual fields on a multi-table child point at the JSON column's model."""

    def test_model_is_json_column_owner(self):
        assert Item._meta.get_field("localized_title").model is Item
        assert Movie._meta.get_field("localized_title").model is Item
        orig_title = Movie._meta.get_field("orig_title")
        assert isinstance(orig_title, JSONFieldMixin)
        assert orig_title.model is Item
        assert orig_title.attached_model is Movie

    def test_proxy_model_is_unchanged(self):
        field = MastodonAccount._meta.get_field("access_token")
        assert isinstance(field, JSONFieldMixin)
        assert field.model is MastodonAccount
        assert field.attached_model is MastodonAccount

    def test_formfield_uses_attached_model(self):
        field = Movie._meta.get_field("localized_title")
        assert isinstance(field, JSONField)
        formfield = field.formfield()
        assert formfield is not None
        assert getattr(formfield.widget, "model_name") == "Movie"

    def test_pickle_reloads_attached_copy(self):
        for name in ("orig_title", "localized_title"):
            field = Movie._meta.get_field(name)
            assert pickle.loads(pickle.dumps(field)) is field
        query = Movie.objects.values("orig_title").query
        assert str(pickle.loads(pickle.dumps(query))) == str(query)


@pytest.mark.django_db(databases="__all__")
class TestChildModelLookups:
    """Lookups on virtual fields of Item subclasses must join catalog_item."""

    def _movies(self):
        movie = Movie.objects.create(
            title="Movie A",
            orig_title="Orig A",
            localized_title=[{"lang": "en", "text": "Movie A"}],
        )
        other = Movie.objects.create(
            title="Movie B",
            orig_title="Orig B",
            localized_title=[{"lang": "en", "text": "Movie B"}],
        )
        return movie, other

    def test_field_declared_on_child(self):
        movie, _ = self._movies()
        assert list(Movie.objects.filter(orig_title="Orig A")) == [movie]

    def test_field_inherited_from_parent(self):
        movie, other = self._movies()
        qs = Movie.objects.filter(
            is_deleted=False,
            merged_to_item__isnull=True,
            localized_title__contains=[{"text": "Movie A"}],
        ).exclude(pk=other.pk)[:5]
        assert list(qs) == [movie]

    def test_count_and_values_join_parent(self):
        movie, _ = self._movies()
        qs = Movie.objects.filter(orig_title="Orig A")
        assert qs.count() == 1
        assert list(qs.values_list("pk", flat=True)) == [movie.pk]

    def test_base_model_lookup(self):
        movie, _ = self._movies()
        qs = Item.objects.filter(localized_title__contains=[{"text": "Movie A"}])
        assert list(qs) == [movie]

    def test_people_localized_name(self):
        person = People.objects.create(
            people_type=PeopleType.PERSON,
            localized_name=[{"lang": "en", "text": "Some One"}],
        )
        qs = People.objects.filter(localized_name__contains=[{"text": "Some One"}])
        assert list(qs) == [person]

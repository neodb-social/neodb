from datetime import date, datetime, time
from datetime import timezone as dt_tz

import pytest
from django.utils import timezone

from common.models.jsondata import (
    DateField,
    DateTimeField,
    EncryptedTextField,
    TimeField,
    decrypt_str,
    encrypt_str,
)


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

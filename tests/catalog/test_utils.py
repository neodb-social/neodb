from catalog.models.book import Edition
from catalog.models.common import IdType
from catalog.models.utils import (
    binding_to_format,
    check_digit_10,
    check_digit_13,
    detect_isbn_asin,
    is_asin,
    is_isbn_10,
    is_isbn_13,
    isbn_10_to_13,
    isbn_13_to_10,
    upc_to_gtin_13,
)


class TestCheckDigit10:
    def test_known_digit(self):
        # ISBN-10: 0306406152 — first 9 digits give check digit 2
        assert check_digit_10("030640615") == "2"

    def test_x_digit(self):
        # ISBN-10: 097522980X — check digit is X (remainder 10)
        assert check_digit_10("097522980") == "X"


class TestCheckDigit13:
    def test_known_digit(self):
        # ISBN-13: 9780306406157 — first 12 digits give check digit 7
        assert check_digit_13("978030640615") == "7"

    def test_zero_digit(self):
        # When sum is divisible by 10, returns "0"
        # "978000000020": 9*1+7*3+8*1+2*1 = 40; 10-(40%10)=10 → "0"
        assert check_digit_13("978000000020") == "0"


class TestIsbn10To13:
    def test_valid_conversion(self):
        assert isbn_10_to_13("0306406152") == "9780306406157"

    def test_x_check_digit(self):
        assert isbn_10_to_13("097522980X") == "9780975229804"

    def test_none_input(self):
        assert isbn_10_to_13(None) is None

    def test_wrong_length(self):
        assert isbn_10_to_13("123") is None

    def test_empty_string(self):
        assert isbn_10_to_13("") is None


class TestIsbn13To10:
    def test_valid_conversion(self):
        assert isbn_13_to_10("9780306406157") == "0306406152"

    def test_non_978_prefix(self):
        # 979 prefix cannot be converted to ISBN-10
        assert isbn_13_to_10("9791032374191") is None

    def test_none_input(self):
        assert isbn_13_to_10(None) is None

    def test_wrong_length(self):
        assert isbn_13_to_10("978030640615") is None

    def test_empty_string(self):
        assert isbn_13_to_10("") is None


class TestIsbnAsinFormats:
    def test_is_isbn_13_valid(self):
        assert is_isbn_13("9780306406157") is True

    def test_is_isbn_13_wrong_length(self):
        assert is_isbn_13("978030640615") is False

    def test_is_isbn_13_rejects_letters(self):
        assert is_isbn_13("978030640615X") is False

    def test_is_isbn_10_valid(self):
        assert is_isbn_10("0306406152") is True

    def test_is_isbn_10_with_x(self):
        assert is_isbn_10("097522980X") is True

    def test_is_isbn_10_wrong_length(self):
        assert is_isbn_10("030640615") is False

    def test_is_asin_valid(self):
        assert is_asin("B000TEST12") is True

    def test_is_asin_wrong_prefix(self):
        assert is_asin("0306406152") is False

    def test_is_asin_wrong_length(self):
        assert is_asin("B0001") is False


class TestDetectIsbnAsin:
    def test_isbn_13(self):
        id_type, value = detect_isbn_asin("9780306406157")
        assert id_type == IdType.ISBN
        assert value == "9780306406157"

    def test_isbn_10_converts_to_13(self):
        id_type, value = detect_isbn_asin("0306406152")
        assert id_type == IdType.ISBN
        assert value == "9780306406157"

    def test_isbn_with_hyphens(self):
        # Hyphens are stripped before processing
        id_type, value = detect_isbn_asin("978-0-306-40615-7")
        assert id_type == IdType.ISBN
        assert value == "9780306406157"

    def test_asin(self):
        id_type, value = detect_isbn_asin("B000TEST12")
        assert id_type == IdType.ASIN
        assert value == "B000TEST12"

    def test_invalid_string(self):
        id_type, value = detect_isbn_asin("not-an-isbn")
        assert id_type is None
        assert value is None

    def test_empty_string(self):
        id_type, value = detect_isbn_asin("")
        assert id_type is None
        assert value is None

    def test_invalid_isbn13_checkdigit(self):
        # Valid format but wrong check digit
        id_type, value = detect_isbn_asin("9780306406150")
        assert id_type is None
        assert value is None


class TestUpcToGtin13:
    def test_12_digit_upc_padded(self):
        # 12-digit UPC-A gets padded to 13 digits
        assert upc_to_gtin_13("012345678901") == "0012345678901"

    def test_13_digit_passthrough(self):
        assert upc_to_gtin_13("0012345678901") == "0012345678901"

    def test_14_digit_strip_leading_zeros(self):
        # 14 digits with one leading zero → strip it
        assert upc_to_gtin_13("00012345678901") == "0012345678901"

    def test_14_digit_non_zero_prefix_invalid(self):
        # 14 digits where excess digit is non-zero → invalid
        assert upc_to_gtin_13("10012345678901") is None

    def test_non_numeric(self):
        assert upc_to_gtin_13("abc") is None

    def test_empty_string(self):
        assert upc_to_gtin_13("") is None

    def test_whitespace_stripped(self):
        assert upc_to_gtin_13(" 0012345678901 ") == "0012345678901"


class TestBindingToFormat:
    def test_none_returns_none(self):
        assert binding_to_format(None) is None

    def test_empty_returns_none(self):
        assert binding_to_format("") is None

    def test_audiobook(self):
        assert binding_to_format("Audiobook") == Edition.BookFormat.AUDIOBOOK
        assert binding_to_format("Audible Edition") == Edition.BookFormat.AUDIOBOOK
        assert binding_to_format("音频") == Edition.BookFormat.AUDIOBOOK

    def test_ebook(self):
        assert binding_to_format("eBook") == Edition.BookFormat.EBOOK
        assert binding_to_format("Kindle Edition") == Edition.BookFormat.EBOOK
        assert binding_to_format("电子书") == Edition.BookFormat.EBOOK

    def test_web(self):
        assert binding_to_format("Web") == Edition.BookFormat.WEB
        assert binding_to_format("网络版") == Edition.BookFormat.WEB

    def test_hardcover(self):
        assert binding_to_format("Hardcover") == Edition.BookFormat.HARDCOVER
        assert binding_to_format("精装") == Edition.BookFormat.HARDCOVER

    def test_paperback(self):
        assert binding_to_format("Paperback") == Edition.BookFormat.PAPERBACK
        assert binding_to_format("Softcover") == Edition.BookFormat.PAPERBACK
        assert binding_to_format("平装") == Edition.BookFormat.PAPERBACK

    def test_unknown_returns_none(self):
        assert binding_to_format("Leatherbound") is None

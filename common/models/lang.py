"""
    language support utilities

    https://en.wikipedia.org/wiki/IETF_language_tag
"""

import re
from typing import Any

from django.conf import settings
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from langdetect import detect
from loguru import logger

PREFERRED_LANGUAGES: list[str] = settings.PREFERRED_LANGUAGES

DEFAULT_CATALOG_LANGUAGE = PREFERRED_LANGUAGES[0] if PREFERRED_LANGUAGES else "en"

ISO_639_1 = {
    "aa": _("Afar"),
    "af": _("Afrikaans"),
    "ak": _("Akan"),
    "an": _("Aragonese"),
    "as": _("Assamese"),
    "av": _("Avaric"),
    "ae": _("Avestan"),
    "ay": _("Aymara"),
    "az": _("Azerbaijani"),
    "ba": _("Bashkir"),
    "bm": _("Bambara"),
    "bi": _("Bislama"),
    "bo": _("Tibetan"),
    "br": _("Breton"),
    "ca": _("Catalan"),
    "cs": _("Czech"),
    "ce": _("Chechen"),
    "cu": _("Slavic"),
    "cv": _("Chuvash"),
    "kw": _("Cornish"),
    "co": _("Corsican"),
    "cr": _("Cree"),
    "cy": _("Welsh"),
    "da": _("Danish"),
    "de": _("German"),
    "dv": _("Divehi"),
    "dz": _("Dzongkha"),
    "eo": _("Esperanto"),
    "et": _("Estonian"),
    "eu": _("Basque"),
    "fo": _("Faroese"),
    "fj": _("Fijian"),
    "fi": _("Finnish"),
    "fr": _("French"),
    "fy": _("Frisian"),
    "ff": _("Fulah"),
    "gd": _("Gaelic"),
    "ga": _("Irish"),
    "gl": _("Galician"),
    "gv": _("Manx"),
    "gn": _("Guarani"),
    "gu": _("Gujarati"),
    "ht": _("Haitian; Haitian Creole"),
    "ha": _("Hausa"),
    "sh": _("Serbo-Croatian"),
    "hz": _("Herero"),
    "ho": _("Hiri Motu"),
    "hr": _("Croatian"),
    "hu": _("Hungarian"),
    "ig": _("Igbo"),
    "io": _("Ido"),
    "ii": _("Yi"),
    "iu": _("Inuktitut"),
    "ie": _("Interlingue"),
    "ia": _("Interlingua"),
    "id": _("Indonesian"),
    "ik": _("Inupiaq"),
    "is": _("Icelandic"),
    "it": _("Italian"),
    "jv": _("Javanese"),
    "ja": _("Japanese"),
    "kl": _("Kalaallisut"),
    "kn": _("Kannada"),
    "ks": _("Kashmiri"),
    "kr": _("Kanuri"),
    "kk": _("Kazakh"),
    "km": _("Khmer"),
    "ki": _("Kikuyu"),
    "rw": _("Kinyarwanda"),
    "ky": _("Kirghiz"),
    "kv": _("Komi"),
    "kg": _("Kongo"),
    "ko": _("Korean"),
    "kj": _("Kuanyama"),
    "ku": _("Kurdish"),
    "lo": _("Lao"),
    "la": _("Latin"),
    "lv": _("Latvian"),
    "li": _("Limburgish"),
    "ln": _("Lingala"),
    "lt": _("Lithuanian"),
    "lb": _("Letzeburgesch"),
    "lu": _("Luba-Katanga"),
    "lg": _("Ganda"),
    "mh": _("Marshall"),
    "ml": _("Malayalam"),
    "mr": _("Marathi"),
    "mg": _("Malagasy"),
    "mt": _("Maltese"),
    "mo": _("Moldavian"),
    "mn": _("Mongolian"),
    "mi": _("Maori"),
    "ms": _("Malay"),
    "my": _("Burmese"),
    "na": _("Nauru"),
    "nv": _("Navajo"),
    "nr": _("Ndebele"),
    "nd": _("Ndebele"),
    "ng": _("Ndonga"),
    "ne": _("Nepali"),
    "nl": _("Dutch"),
    "nn": _("Norwegian Nynorsk"),
    "nb": _("Norwegian Bokmål"),
    "no": _("Norwegian"),
    "ny": _("Chichewa; Nyanja"),
    "oc": _("Occitan"),
    "oj": _("Ojibwa"),
    "or": _("Oriya"),
    "om": _("Oromo"),
    "os": _("Ossetian; Ossetic"),
    "pi": _("Pali"),
    "pl": _("Polish"),
    "pt": _("Portuguese"),
    "qu": _("Quechua"),
    "rm": _("Raeto-Romance"),
    "ro": _("Romanian"),
    "rn": _("Rundi"),
    "ru": _("Russian"),
    "sg": _("Sango"),
    "sa": _("Sanskrit"),
    "si": _("Sinhalese"),
    "sk": _("Slovak"),
    "sl": _("Slovenian"),
    "se": _("Northern Sami"),
    "sm": _("Samoan"),
    "sn": _("Shona"),
    "sd": _("Sindhi"),
    "so": _("Somali"),
    "st": _("Sotho"),
    "es": _("Spanish"),
    "sq": _("Albanian"),
    "sc": _("Sardinian"),
    "sr": _("Serbian"),
    "ss": _("Swati"),
    "su": _("Sundanese"),
    "sw": _("Swahili"),
    "sv": _("Swedish"),
    "ty": _("Tahitian"),
    "ta": _("Tamil"),
    "tt": _("Tatar"),
    "te": _("Telugu"),
    "tg": _("Tajik"),
    "tl": _("Tagalog"),
    "th": _("Thai"),
    "ti": _("Tigrinya"),
    "to": _("Tonga"),
    "tn": _("Tswana"),
    "ts": _("Tsonga"),
    "tk": _("Turkmen"),
    "tr": _("Turkish"),
    "tw": _("Twi"),
    "ug": _("Uighur"),
    "uk": _("Ukrainian"),
    "ur": _("Urdu"),
    "uz": _("Uzbek"),
    "ve": _("Venda"),
    "vi": _("Vietnamese"),
    "vo": _("Volapük"),
    "wa": _("Walloon"),
    "wo": _("Wolof"),
    "xh": _("Xhosa"),
    "yi": _("Yiddish"),
    "za": _("Zhuang"),
    "zu": _("Zulu"),
    "ab": _("Abkhazian"),
    "zh": _("Chinese"),
    "ps": _("Pushto"),
    "am": _("Amharic"),
    "ar": _("Arabic"),
    "bg": _("Bulgarian"),
    "mk": _("Macedonian"),
    "el": _("Greek"),
    "fa": _("Persian"),
    "he": _("Hebrew"),
    "hi": _("Hindi"),
    "hy": _("Armenian"),
    "en": _("English"),
    "ee": _("Ewe"),
    "ka": _("Georgian"),
    "pa": _("Punjabi"),
    "bn": _("Bengali"),
    "bs": _("Bosnian"),
    "ch": _("Chamorro"),
    "be": _("Belarusian"),
    "yo": _("Yoruba"),
    "x": _("Unknown or Other"),
}
TOP_USED_LANG = [
    "en",
    "de",
    "es",
    "zh",
    "fr",
    "ja",
    "it",
    "ru",
    "pt",
    "nl",
    "kr",
    "hi",
    "ar",
    "bn",
]
ZH_LOCALE_SUBTAGS_PRIO = {
    "zh-cn": _("Simplified Chinese (Mainland)"),
    "zh-tw": _("Traditional Chinese (Taiwan)"),
    "zh-hk": _("Traditional Chinese (Hongkong)"),
}
ZH_LOCALE_SUBTAGS = {
    "zh-sg": _("Simplified Chinese (Singapore)"),
    "zh-my": _("Simplified Chinese (Malaysia)"),
    "zh-mo": _("Traditional Chinese (Macau)"),
}
ZH_LANGUAGE_SUBTAGS_PRIO = {
    "cmn": _("Mandarin Chinese"),
    "yue": _("Yue Chinese"),
}
ZH_LANGUAGE_SUBTAGS = {
    "nan": _("Min Nan Chinese"),
    "wuu": _("Wu Chinese"),
    "hak": _("Hakka Chinese"),
}

ZH_LOCALE_SUBTAGS_PRIO.keys()


def get_base_lang_list():
    langs = {}
    for k in PREFERRED_LANGUAGES + TOP_USED_LANG:
        if k not in langs:
            if k in ISO_639_1:
                langs[k] = ISO_639_1[k]
            else:
                logger.error(f"{k} is not a supported ISO-639-1 language tag")
    for k, v in ISO_639_1.items():
        if k not in langs:
            langs[k] = v
    return langs


BASE_LANG_LIST: dict[str, Any] = get_base_lang_list()


def get_locale_choices():
    choices = []
    for k, v in BASE_LANG_LIST.items():
        if k == "zh":
            choices += ZH_LOCALE_SUBTAGS_PRIO.items()
        else:
            choices.append((k, v))
    choices += ZH_LOCALE_SUBTAGS.items()
    return choices


def get_script_choices():
    return list(BASE_LANG_LIST.items())


def get_language_choices():
    choices = []
    for k, v in BASE_LANG_LIST.items():
        if k == "zh":
            choices += ZH_LANGUAGE_SUBTAGS_PRIO.items()
        else:
            choices.append((k, v))
    choices += ZH_LANGUAGE_SUBTAGS.items()
    return choices


LOCALE_CHOICES: list[tuple[str, Any]] = get_locale_choices()
SCRIPT_CHOICES: list[tuple[str, Any]] = get_script_choices()
LANGUAGE_CHOICES: list[tuple[str, Any]] = get_language_choices()


def get_current_locales() -> list[str]:
    lang = get_language().lower()
    if lang == "zh-hans":
        return ["zh-cn", "zh-sg", "zh-my", "zh-hk", "zh-tw", "zh-mo", "en"]
    elif lang == "zh-hant":
        return ["zh-tw", "zh-hk", "zh-mo", "zh-cn", "zh-sg", "zh-my", "en"]
    else:
        lng = lang.split("-")
        return ["en"] if lng[0] == "en" else [lng[0], "en"]


_eng = re.compile(r"^[A-Za-z0-9\s]{1,13}$")


def detect_language(s: str) -> str:
    try:
        if _eng.match(s):
            return "en"
        return detect(s).lower()
    except Exception:
        return "x"


def migrate_languages(languages: list[str]) -> list[str]:
    return []

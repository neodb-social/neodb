"""Background music for the annual summary page.

The owner picks one of these tracks in the Edit sheet (no music by default), and the card page offers the
viewer a sound toggle, off until the viewer turns it on; the track repeats. The files are played straight
from Wikimedia Commons, which allows it without a guarantee, so a track that fails to load leaves the page
silent. Each track keeps its licence and its Commons page, where the licence is stated. The list and its
order are the user's choice of 2026-11-27, including four tracks with a weaker licence basis (see the
comment on each).
"""

from typing import Any

from django.utils.translation import gettext, gettext_noop

TRACKS: list[dict[str, Any]] = [
    {
        "id": "mozart-k545",
        "title": gettext_noop("Piano Sonata No. 16, K. 545, I. Allegro"),
        "composer": gettext_noop("W. A. Mozart"),
        "performer": gettext_noop("Performer not named (Musopen)"),
        "seconds": 134,
        "licence": "Public domain",
        # why the licence holds: Statement of the Musopen uploader; released by the rights holder: no
        "page": "https://commons.wikimedia.org/wiki/File:Wolfgang_Amadeus_Mozart_-_sonata_no._16_in_c_major,_k.545_%27sonata_facile%27_-_i._allegro.ogg",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/transcoded/3/38/Wolfgang_Amadeus_Mozart_-_sonata_no._16_in_c_major%2C_k.545_%27sonata_facile%27_-_i._allegro.ogg/Wolfgang_Amadeus_Mozart_-_sonata_no._16_in_c_major%2C_k.545_%27sonata_facile%27_-_i._allegro.ogg.mp3",
    },
    {
        "id": "bach-bwv850",
        "title": gettext_noop("Prelude in D major, BWV 850"),
        "composer": gettext_noop("J. S. Bach"),
        "performer": gettext_noop(
            "Kimiko Ishizaka, piano (Open Well-Tempered Clavier, 2015)"
        ),
        "seconds": 93,
        "licence": "CC0",
        # why the licence holds: Released by the performer's project; released by the rights holder: yes
        "page": "https://commons.wikimedia.org/wiki/File:Kimiko_Ishizaka_-_Bach_-_Well-Tempered_Clavier,_Book_1_-_09_Prelude_No._5_in_D_major,_BWV_850.ogg",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/transcoded/a/a0/Kimiko_Ishizaka_-_Bach_-_Well-Tempered_Clavier%2C_Book_1_-_09_Prelude_No._5_in_D_major%2C_BWV_850.ogg/Kimiko_Ishizaka_-_Bach_-_Well-Tempered_Clavier%2C_Book_1_-_09_Prelude_No._5_in_D_major%2C_BWV_850.ogg.mp3",
    },
    {
        "id": "bach-goldberg-var1",
        "title": gettext_noop("Goldberg Variations, Variation 1"),
        "composer": gettext_noop("J. S. Bach"),
        "performer": gettext_noop(
            "Kimiko Ishizaka, piano (Open Goldberg Variations, 2012)"
        ),
        "seconds": 115,
        "licence": "CC0",
        # why the licence holds: Released by the performer's project; released by the rights holder: yes
        "page": "https://commons.wikimedia.org/wiki/File:Kimiko_Ishizaka_-_02_-_Variatio_1_a_1_Clav.ogg",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/transcoded/2/20/Kimiko_Ishizaka_-_02_-_Variatio_1_a_1_Clav.ogg/Kimiko_Ishizaka_-_02_-_Variatio_1_a_1_Clav.ogg.mp3",
    },
    {
        "id": "bach-bwv846",
        "title": gettext_noop("Prelude in C major, BWV 846"),
        "composer": gettext_noop("J. S. Bach"),
        "performer": gettext_noop(
            "Kimiko Ishizaka, piano (Open Well-Tempered Clavier, 2015)"
        ),
        "seconds": 163,
        "licence": "CC0",
        # why the licence holds: Released by the performer's project; released by the rights holder: yes
        "page": "https://commons.wikimedia.org/wiki/File:Kimiko_Ishizaka_-_Bach_-_Well-Tempered_Clavier,_Book_1_-_01_Prelude_No._1_in_C_major,_BWV_846.ogg",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/transcoded/b/b6/Kimiko_Ishizaka_-_Bach_-_Well-Tempered_Clavier%2C_Book_1_-_01_Prelude_No._1_in_C_major%2C_BWV_846.ogg/Kimiko_Ishizaka_-_Bach_-_Well-Tempered_Clavier%2C_Book_1_-_01_Prelude_No._1_in_C_major%2C_BWV_846.ogg.mp3",
    },
    {
        "id": "beethoven-pastoral",
        "title": gettext_noop('Symphony No. 6 "Pastoral", I. Allegro ma non troppo'),
        "composer": gettext_noop("L. van Beethoven"),
        "performer": gettext_noop("Performer not named (Musopen)"),
        "seconds": 566,
        "licence": "Public domain",
        # why the licence holds: Statement of the Musopen uploader; released by the rights holder: no
        "page": "https://commons.wikimedia.org/wiki/File:Ludwig_van_Beethoven_-_symphony_no._6_in_f_major_%27pastoral%27,_op._68_-_i._allegro_non_troppo.ogg",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/transcoded/d/d5/Ludwig_van_Beethoven_-_symphony_no._6_in_f_major_%27pastoral%27%2C_op._68_-_i._allegro_non_troppo.ogg/Ludwig_van_Beethoven_-_symphony_no._6_in_f_major_%27pastoral%27%2C_op._68_-_i._allegro_non_troppo.ogg.mp3",
    },
    {
        "id": "chopin-minute-waltz",
        "title": gettext_noop('Waltz Op. 64 No. 1 "Minute"'),
        "composer": gettext_noop("F. Chopin"),
        "performer": gettext_noop("Olga Gurevich, piano (Musopen, Set Chopin Free)"),
        "seconds": 107,
        "licence": "Public domain",
        # why the licence holds: Musopen paid for the recording and released it; released by the rights holder: yes
        "page": "https://commons.wikimedia.org/wiki/File:Chopin_-_Waltz_No._6_in_D-flat_major,_Op._64,_No._1_%27Minute%27_(Olga_Gurevich).flac",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/transcoded/9/93/Chopin_-_Waltz_No._6_in_D-flat_major%2C_Op._64%2C_No._1_%27Minute%27_%28Olga_Gurevich%29.flac/Chopin_-_Waltz_No._6_in_D-flat_major%2C_Op._64%2C_No._1_%27Minute%27_%28Olga_Gurevich%29.flac.mp3",
    },
    {
        "id": "tchaikovsky-waltz-flowers",
        "title": gettext_noop("The Nutcracker, Waltz of the Flowers"),
        "composer": gettext_noop("P. I. Tchaikovsky"),
        "performer": gettext_noop("United States Air Force Band, concert band (1996)"),
        "seconds": 450,
        "licence": "Public domain",
        # why the licence holds: Work of the US government; outside the US, check; released by the rights holder: no
        "page": "https://commons.wikimedia.org/wiki/File:Waltz_of_the_Flowers_-_Concert_Band_-_United_States_Air_Force_Band.mp3",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/b/b5/Waltz_of_the_Flowers_-_Concert_Band_-_United_States_Air_Force_Band.mp3",
    },
    {
        "id": "gonzaga-corta-jaca",
        "title": gettext_noop("Corta-Jaca (maxixe)"),
        "composer": gettext_noop("Chiquinha Gonzaga, Brazil"),
        "performer": gettext_noop(
            "Grupo Chiquinha Gonzaga: flute, guitar, cavaquinho (about 1912)"
        ),
        "seconds": 178,
        "licence": "Public domain",
        # why the licence holds: Disc of about 1912; public domain by age; released by the rights holder: yes
        "page": "https://commons.wikimedia.org/wiki/File:Corta_jaca_1912.ogg",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/transcoded/c/cf/Corta_jaca_1912.ogg/Corta_jaca_1912.ogg.mp3",
    },
    {
        "id": "nazareth-odeon",
        "title": gettext_noop("Odeon (Brazilian tango), clean rendering"),
        "composer": gettext_noop("E. Nazareth, Brazil"),
        "performer": gettext_noop("Computer rendering by Eduardo Feld"),
        "seconds": 197,
        "licence": "Public domain",
        # why the licence holds: Computer rendering, released by its maker on Commons; released by the rights holder: no
        "page": "https://commons.wikimedia.org/wiki/File:Odeon.mp3",
        "mp3": "https://upload.wikimedia.org/wikipedia/commons/9/9b/Odeon.mp3",
    },
]
BY_ID = {t["id"]: t for t in TRACKS}
LICENCE = {"Public domain": gettext_noop("Public domain"), "CC0": "CC0"}


def track(track_id: str | None) -> dict[str, Any] | None:
    """One track in the page language, as the page needs it; None for no music or an id not on the list."""
    t = BY_ID.get(track_id or "")
    if t is None:
        return None
    return {
        "id": t["id"],
        "title": gettext(t["title"]),
        "composer": gettext(t["composer"]),
        "performer": gettext(t["performer"]),
        "licence": gettext(LICENCE[t["licence"]]),
        "page": t["page"],
        "mp3": t["mp3"],
    }


def menu() -> list[dict[str, Any]]:
    """Every track, for the owner's menu."""
    return [track(t["id"]) for t in TRACKS]  # pyrefly: ignore[bad-return]

from django.conf import settings


def rating_to_emoji(score, star_mode=0):
    """convert score to mastodon star emoji code"""
    if score is None or score == "" or score == 0:
        return ""
    solid_stars = score // 2
    half_star = int(bool(score % 2))
    empty_stars = 5 - solid_stars if not half_star else 5 - solid_stars - 1
    if star_mode == 1:
        emoji_code = "🌕" * solid_stars + "🌗" * half_star + "🌑" * empty_stars
    else:
        emoji_code = (
            settings.STAR_SOLID * solid_stars
            + settings.STAR_HALF * half_star
            + settings.STAR_EMPTY * empty_stars
        )
    emoji_code = emoji_code.replace("::", ": :")
    emoji_code = " " + emoji_code + " "
    return emoji_code

"""Text of the annual summary cards.

The cards are drawn in the browser (journal/static/js/wrapped); their text is marked there with _(), N_() and _n(),
with the English text as the key. This module lists the same text for gettext, and card_text() gives the page the
translation of each key in the active language. tests/journal/test_wrapped.py checks that the two lists agree.
"""

from django.utils.translation import (
    get_language,
    gettext,
    gettext_noop,
    ngettext,
    override,
)

TEXT = [
    gettext_noop("Books"),
    gettext_noop("Movies"),
    gettext_noop("TV"),
    gettext_noop("Music"),
    gettext_noop("Games"),
    gettext_noop("Podcasts"),
    gettext_noop("Performances"),
    gettext_noop("books"),
    gettext_noop("movies"),
    gettext_noop("music"),
    gettext_noop("games"),
    gettext_noop("podcasts"),
    gettext_noop("performances"),
    gettext_noop("director"),
    gettext_noop("author"),
    gettext_noop("artist"),
    gettext_noop("host"),
    gettext_noop("developer"),
    gettext_noop("designer"),
    gettext_noop("actor"),
    gettext_noop("Everything that got five stars this year"),
    gettext_noop("Your best-rated titles of the year"),
    gettext_noop("Your highest marks, then the titles others rate highest"),
    gettext_noop(
        "No stars given this year, so here are the titles others rate highest"
    ),
    gettext_noop("no stars"),
    # Translators: {nth} is an English ordinal such as tenth; use {n}, the number, instead
    # xgettext:no-python-brace-format
    gettext_noop("your {nth} year on NeoDB"),
    gettext_noop(", with marks back to {year}"),
    gettext_noop("no earlier years in the data"),
    gettext_noop("Your biggest year ever"),
    gettext_noop("Your biggest year in {n} years"),
    gettext_noop("Your biggest year since {year}"),
    gettext_noop("More {shelf} than in any year since {year}"),
    # xgettext:no-python-format
    gettext_noop("Up {pct}% on {year}"),
    # xgettext:no-python-format
    gettext_noop("{pct}% above your average"),
    gettext_noop("A steady year"),
    gettext_noop("Most titles"),
    gettext_noop("Most {shelf}"),
    gettext_noop("Most five-star titles"),
    gettext_noop("Most days finishing a title"),
    gettext_noop("Highest average rating"),
    gettext_noop("Year over year"),
    gettext_noop(", {n} of them on one bulk day"),
    gettext_noop("; {n} a year on average since {year}"),
    gettext_noop("earlier years, {start} to {end}"),
    gettext_noop("earlier years"),
    gettext_noop("{year} by shelf"),
    gettext_noop("first year, not compared"),
    gettext_noop("bulk days, left out"),
    gettext_noop("the full year"),
    gettext_noop("Personal bests"),
    gettext_noop("no record broken against the years since {year}"),
    gettext_noop("Three records"),
    gettext_noop("Four records"),
    gettext_noop("Five records"),
    gettext_noop("set this year, against {start} to {end} of every year since {year}"),
    gettext_noop("set this year, against every year since {year}"),
    gettext_noop("Titles marked in bulk on one day are left out."),
    gettext_noop("previous best {value} in {year}"),
    gettext_noop("How your taste moved"),
    gettext_noop(
        "no shelf moved by 15 points; the largest change is {shelf}, {n} points"
    ),
    gettext_noop("too few titles to compare"),
    # xgettext:no-python-format
    gettext_noop("{shelf} went from {a}% to {b}%"),
    gettext_noop("of your year so far, against your average since {year}"),
    gettext_noop("of your year, against your average since {year}"),
    gettext_noop("{shelf}, share of each year"),
    gettext_noop("other shelves"),
    gettext_noop("Nothing was finished in {year}."),
    gettext_noop(
        "Nothing finished this year yet. Mark a title as complete and the cards fill in."
    ),
    gettext_noop("Nothing finished this year yet."),
    gettext_noop("{n} titles marked on {date} are left out"),
    gettext_noop("Intro"),
    gettext_noop("Annual summary"),
    gettext_noop("{name}’s year"),
    gettext_noop("Your year in covers"),
    gettext_noop("only in a small year, under {n} titles"),
    gettext_noop("Shelves"),
    gettext_noop("Completed"),
    gettext_noop("{n} marked in bulk on {date}"),
    gettext_noop("{n} dropped, not counted"),
    gettext_noop("Busiest month"),
    gettext_noop("{month} is only {x} times the monthly mean, under 1.6"),
    gettext_noop("{n} titles in one month; {month} was the quietest with {quiet}"),
    # xgettext:no-python-format
    gettext_noop("{n} read this year, {pct}% of everything"),
    # xgettext:no-python-format
    gettext_noop("{n} watched this year, {pct}% of everything"),
    # xgettext:no-python-format
    gettext_noop("{n} listened to this year, {pct}% of everything"),
    # xgettext:no-python-format
    gettext_noop("{n} played this year, {pct}% of everything"),
    # xgettext:no-python-format
    gettext_noop("{n} followed this year, {pct}% of everything"),
    # xgettext:no-python-format
    gettext_noop("{n} attended this year, {pct}% of everything"),
    gettext_noop("Biggest shelf"),
    # xgettext:no-python-format
    gettext_noop("the biggest shelf holds only {pct}%"),
    gettext_noop("only one shelf this year"),
    gettext_noop("Highest mark"),
    gettext_noop("Five stars"),
    gettext_noop("Top shelf"),
    gettext_noop("fewer than three rated titles"),
    gettext_noop(
        "{n} titles got five stars; here are ten, the ones you wrote about first"
    ),
    gettext_noop("{n} favourites"),
    gettext_noop("The highest marks"),
    gettext_noop("Quiet favourite"),
    gettext_noop("your favourites all have 20 or more marks here"),
    gettext_noop("no top mark to pick from"),
    gettext_noop("The quiet favourite"),
    gettext_noop("from you, and no other mark on {site}."),
    gettext_noop("{site} {value}"),
    gettext_noop("Where you disagreed"),
    gettext_noop(
        "no rating {gap} points (of 10) from the site average on a title with {n} or more marks"
    ),
    gettext_noop("Against the grain"),
    gettext_noop("The disappointment"),
    gettext_noop("The surprise"),
    gettext_noop(
        "with something logged; the longest run, outlined, was {n} days, {start} to {end}"
    ),
    gettext_noop("with something logged; never two days in a row"),
    gettext_noop("Active days"),
    gettext_noop("Streak"),
    gettext_noop(
        "{days} with something logged, under {min}, and the longest run is {run}"
    ),
    gettext_noop("something logged every day, {start} to {end}"),
    gettext_noop("a title finished every day, {start} to {end}"),
    gettext_noop("Longest streak"),
    gettext_noop("{start} to {end}"),
    gettext_noop("a title finished"),
    gettext_noop("progress or a comment"),
    gettext_noop("nothing logged"),
    gettext_noop("the run"),
    gettext_noop("Week by week"),
    gettext_noop("{n} titles is too few for a week strip"),
    gettext_noop("{n} in a week"),
    gettext_noop(
        "the busiest seven days began {date}; {n} weeks of the year had a title finished"
    ),
    gettext_noop("titles finished that week"),
    gettext_noop("the busiest week"),
    gettext_noop("The era"),
    gettext_noop("fewer than ten titles with a release year"),
    gettext_noop("release years span under 15 years"),
    # xgettext:no-python-format
    gettext_noop("{pct}% of titles come from one decade"),
    gettext_noop("Mostly new"),
    # xgettext:no-python-format
    gettext_noop(
        "{pct}% came out in the last three years; the oldest title is from {year}."
    ),
    gettext_noop("The era you lived in"),
    gettext_noop("older"),
    gettext_noop("the last three years"),
    # xgettext:no-python-format
    gettext_noop(
        "Only {pct}% came out in the last three years. Half your year was made before {year}."
    ),
    # xgettext:no-python-format
    gettext_noop(
        "Half your year was made before {year}, and {pct}% came out in the last three years."
    ),
    gettext_noop("{d}s"),
    gettext_noop("the decade of {year}"),
    gettext_noop("titles by decade of release"),
    gettext_noop("People"),
    gettext_noop("nobody appears on two titles"),
    gettext_noop("On repeat"),
    gettext_noop("People you kept returning to"),
    gettext_noop("fewer than three followed members with visible marks this year"),
    gettext_noop("In good company"),
    gettext_noop("no title shared with two or more followed members"),
    gettext_noop("You and {n}"),
    gettext_noop("people you follow finished the same title this year"),
    gettext_noop("Taste twin"),
    gettext_noop("nobody you follow shares two titles with you"),
    gettext_noop(
        "@{handle} shares {n} of your titles this year, more than anyone you follow"
    ),
    gettext_noop("Who got there first"),
    gettext_noop("too few shared titles with dates"),
    gettext_noop("times someone you follow finished a title after you did"),
    gettext_noop("Most followed"),
    gettext_noop("{n} of them came after you"),
    gettext_noop("times you came after someone,"),
    gettext_noop("times you came after someone"),
    gettext_noop("most often after {name}"),
    gettext_noop("Off the beaten path"),
    gettext_noop("no mark counts"),
    # xgettext:no-python-format
    gettext_noop("only {pct}% of titles have fewer than ten marks"),
    gettext_noop("of your year is titles fewer than ten people on {site} have marked"),
    gettext_noop("With the crowd, against it"),
    gettext_noop("no rated title with a known distribution"),
    gettext_noop("Both sides"),
    gettext_noop("With the crowd"),
    gettext_noop("How others rated it"),
    # xgettext:no-python-format
    gettext_noop("your five stars put you with the {pct}%"),
    gettext_noop("who gave it five too"),
    # xgettext:no-python-format
    gettext_noop("you rated it higher than {pct}%"),
    gettext_noop("of the {n} people who rated it"),
    gettext_noop("Early"),
    gettext_noop(
        "no title where you were among the first ten markers, or the data is not public"
    ),
    gettext_noop("#{n}"),
    gettext_noop("to mark it on {site}"),
    gettext_noop("{n} people have marked it since."),
    gettext_noop("Part of the year"),
    gettext_noop("the instance totals are not ready yet"),
    gettext_noop("finished this title, more than any other"),
    gettext_noop("{site} in {year}"),
    gettext_noop("The most finished"),
    gettext_noop("You were one of them."),
    gettext_noop("Maybe next year."),
    gettext_noop("Ahead of the pack"),
    gettext_noop("below the median this year, so this card stays quiet"),
    gettext_noop("the instance percentile is not ready yet"),
    gettext_noop(
        "of active members on {site} completed fewer titles than you this year"
    ),
    gettext_noop("the median member"),
    gettext_noop("you"),
    gettext_noop("fewer titles"),
    gettext_noop("more"),
    gettext_noop("Versus last year"),
    gettext_noop("Year over year takes its place with five or more years of marks"),
    gettext_noop("no previous year on record"),
    gettext_noop("{last}: {a}. {year}: {b}."),
    gettext_noop("{year}, in each shelf's colour"),
    gettext_noop("{date}, {year}"),
    gettext_noop("{n} years"),
    gettext_noop("over a year"),
    gettext_noop("{n} months"),
    gettext_noop("Finally"),
    gettext_noop("wishlist history is not in this data"),
    gettext_noop("only {n} came off the wishlist, and none waited a year"),
    gettext_noop("nothing came off the wishlist this year"),
    gettext_noop("came off your wishlist this year; the longest wait was {wait}"),
    gettext_noop("Waited longest"),
    gettext_noop("saved {date}"),
    gettext_noop("finished {date}"),
    gettext_noop("Unfinished business"),
    gettext_noop("Finally takes its place; show both if you like"),
    gettext_noop("wishlist history is not public here"),
    gettext_noop("on your wishlist, and counting"),
    gettext_noop("Maybe {year}."),
    gettext_noop("Marathon day"),
    gettext_noop("no day with four or more marks"),
    gettext_noop("{n} titles marked in one day"),
    gettext_noop("and {n} more"),
    gettext_noop("Your tags"),
    gettext_noop("tags are not in this data"),
    gettext_noop("no tags on this year's titles"),
    gettext_noop("on {n} titles; you used “{tag}” most"),
    gettext_noop("tagged “{tag}”"),
    gettext_noop("The essay"),
    gettext_noop(
        "longest comment is {n} characters, under 140, and no review this year"
    ),
    gettext_noop("no comments or reviews this year"),
    gettext_noop("In your own words"),
    gettext_noop("{n} characters, your longest review of {count}"),
    gettext_noop("{n} words, your longest review of {count}"),
    gettext_noop("{n} characters, your longest review"),
    gettext_noop("{n} words, your longest review"),
    gettext_noop("{n} characters, your longest comment"),
    gettext_noop("Outro"),
    gettext_noop("See you in"),
    gettext_noop("Could not save the deck; try again"),
    gettext_noop("My pick"),
    gettext_noop(
        "fewer than three items can be shown, so the collection may have changed"
    ),
    gettext_noop("Public"),
    gettext_noop(
        "Anyone with the link sees the cards; counts use public entries only."
    ),
    gettext_noop("Followers only"),
    gettext_noop(
        "Only your followers see the cards; counts include followers-only entries."
    ),
    gettext_noop("Only me"),
    gettext_noop("Only you see the cards; counts include private entries."),
    gettext_noop("hidden by you"),
    gettext_noop("not shown in a small year, under {n} titles"),
    gettext_noop("could not be drawn from this data"),
    gettext_noop("Post this card"),
    gettext_noop("Download PNG"),
    gettext_noop("Hide this card"),
    gettext_noop("always first"),
    gettext_noop("always last"),
    gettext_noop("Move {name} earlier"),
    gettext_noop("Move {name} later"),
    gettext_noop("Make a card"),
    gettext_noop(
        "Drag a card to a new place, or use its arrows. The intro stays first and the outro last."
    ),
    gettext_noop(
        "Hide or show a card with its eye, change the order with Arrange, or make your own with the plus."
    ),
    gettext_noop("{shown} cards shown, {hidden} hidden."),
    gettext_noop("Done"),
    gettext_noop("Arrange"),
    gettext_noop("Show this card"),
    gettext_noop("Back to the default order"),
    gettext_noop("titles completed"),
    gettext_noop("days with something logged"),
    gettext_noop("average rating, of 10"),
    gettext_noop("longest streak"),
    gettext_noop("All"),
    gettext_noop("Highest marks this year"),
    gettext_noop("Every shelf"),
    gettext_noop("Your collections"),
    gettext_noop("No collections of your own yet"),
    gettext_noop("Collections you liked"),
    gettext_noop("No liked collections yet"),
    gettext_noop("Your own list"),
    gettext_noop("Pick titles or paste links"),
    gettext_noop(
        "The card shows the first ones in the collection's order and follows it when it changes."
    ),
    gettext_noop("Pick from it by hand"),
    gettext_noop("My {n} picks of {year}"),
    gettext_noop("My top picks of {year}"),
    gettext_noop("Chosen by hand"),
    gettext_noop("From @{owner}'s collection"),
    gettext_noop("From my collection"),
    gettext_noop("My top {n} titles of {year}"),
    gettext_noop("My top {n} {shelf} of {year}"),
    gettext_noop("Highest marks"),
    gettext_noop("{n} picked. A card takes 3 to 10; drag to reorder."),
    gettext_noop("from a link"),
    gettext_noop("from a collection"),
    gettext_noop("marked {date}"),
    gettext_noop("not marked in {year}"),
    gettext_noop("Remove"),
    gettext_noop("A card takes at most ten titles"),
    gettext_noop("Already on the list"),
    gettext_noop("From the link"),
    gettext_noop("{site} is fetching this page; still checking…"),
    gettext_noop("Looking it up on {site}…"),
    gettext_noop("Still not ready after a minute. Try the link again later."),
    gettext_noop(
        "This site is not one this instance can read. Try a link to the title on a supported site, or to its page here."
    ),
    gettext_noop("No title at this link on {site}."),
    gettext_noop("Too many lookups just now. Try again in a moment."),
    gettext_noop("That does not look like a full link."),
    gettext_noop("The lookup failed. Try again."),
    gettext_noop("in “{title}”"),
    gettext_noop("Marked in {year}"),
    gettext_noop("In your collections"),
    gettext_noop("Nothing found"),
    gettext_noop("Paste a link to add a title from anywhere."),
    gettext_noop("A card needs at least three titles"),
    gettext_noop("Card added to the deck"),
    gettext_noop("Pick at least one card"),
    gettext_noop("Posting failed: {error}"),
    gettext_noop("Link copied"),
    gettext_noop("Export failed: {error}"),
    gettext_noop("the video encoder stopped working"),
    gettext_noop("GIF preview"),
    gettext_noop("{size} MB, {n} frames at 2.5 s"),
    gettext_noop("{size} MB, {n} files"),
    gettext_noop("{size} MB, {seconds} s video"),
    gettext_noop("{size} MB, {seconds} s video with music"),
    gettext_noop("The music could not be added, so the video has no sound."),
    gettext_noop("Every card is hidden from this audience"),
    gettext_noop("Back to the suggested cards"),
    gettext_noop("Not shared"),
    gettext_noop("Suggested cards"),
    gettext_noop("Posted the video to your timeline"),
    gettext_noop("This audience sees no cards. Show some in Edit."),
    gettext_noop("Sound on"),
    gettext_noop("Sound off"),
    gettext_noop("No music"),
    gettext_noop("The music could not be loaded"),
    gettext_noop("My comment"),
    gettext_noop("My note"),
    gettext_noop("My review"),
    gettext_noop("One title, your words"),
    gettext_noop("Pick a text"),
    gettext_noop(
        "This text is for followers only. Your followers and you get this card; the public page leaves it out."
    ),
    gettext_noop("This text is only for you. Only you get this card."),
    gettext_noop("This text is public. Every audience gets this card."),
    gettext_noop("You wrote no comment, note or review in {year}."),
    gettext_noop("comment"),
    gettext_noop("marked sensitive"),
    gettext_noop("review"),
    gettext_noop("the text is gone, or this audience cannot see it"),
    gettext_noop("Change in Edit"),
    gettext_noop("Change it in Settings"),
    gettext_noop("Could not copy: {error}"),
    gettext_noop("Could not share: {error}"),
    gettext_noop("Image copied"),
    gettext_noop(
        "Only you can see the page now, so the code opens nothing for other people."
    ),
    gettext_noop("Only your followers can open this page, after they sign in."),
    gettext_noop(
        "People who are not signed in cannot open this page, because your profile is hidden from them."
    ),
    gettext_noop("Show the cards the code opens"),
    gettext_noop("The code opens {link}."),
    # the stage, the piles of covers and the end of the page
    gettext_noop("Year"),
    gettext_noop("Copy link to this card"),
    gettext_noop("Play"),
    gettext_noop("Pause"),
    gettext_noop("Replay"),
    gettext_noop("Share"),
    gettext_noop("See the whole year"),
    gettext_noop("Make your own {year}"),
    gettext_noop("Edit the deck"),
    gettext_noop("{month}, {titles}"),
]

# each pair as a function of n, so gettext sees the literal forms; the key is the singular
PLURALS = [
    lambda n: ngettext("{n} five-star", "{n} five-star", n),
    lambda n: ngettext("{n} shelf", "{n} shelves", n),
    lambda n: ngettext("{n} character", "{n} characters", n),
    lambda n: ngettext("{n} note", "{n} notes", n),
    lambda n: ngettext("{n} card", "{n} cards", n),
    lambda n: ngettext(
        "marks go back {n} year; the trend cards need five",
        "marks go back {n} years; the trend cards need five",
        n,
    ),
    lambda n: ngettext(
        "{n} earlier year since {year} with ten or more titles; the trend cards need three",
        "{n} earlier years since {year} with ten or more titles; the trend cards need three",
        n,
    ),
    lambda n: ngettext("{n} title so far", "{n} titles so far", n),
    lambda n: ngettext("{n} title this year", "{n} titles this year", n),
    lambda n: ngettext("{n} title", "{n} titles", n),
    lambda n: ngettext(
        "only {n} record broken, under three", "only {n} records broken, under three", n
    ),
    lambda n: ngettext(
        "{n} titles marked on {date} and {days} other bulk day are left out",
        "{n} titles marked on {date} and {days} other bulk days are left out",
        n,
    ),
    lambda n: ngettext(
        "{titles} across {n} shelf, so far", "{titles} across {n} shelves, so far", n
    ),
    lambda n: ngettext("{titles} across {n} shelf", "{titles} across {n} shelves", n),
    lambda n: ngettext(
        "the busiest month has {n} title, under {min}",
        "the busiest month has {n} titles, under {min}",
        n,
    ),
    lambda n: ngettext(
        "from you, and only {n} other mark on {site}.",
        "from you, and only {n} other marks on {site}.",
        n,
    ),
    lambda n: ngettext("{n} day", "{n} days", n),
    lambda n: ngettext("{n} day finishing a title", "{n} days finishing a title", n),
    lambda n: ngettext(
        "{n} day of progress and comments", "{n} days of progress and comments", n
    ),
    lambda n: ngettext("{n} mark", "{n} marks", n),
    lambda n: ngettext("{n} person", "{n} people", n),
    lambda n: ngettext(
        "only {n} title tagged, under {min}", "only {n} titles tagged, under {min}", n
    ),
    lambda n: ngettext("{n} tag", "{n} tags", n),
    lambda n: ngettext("busiest month, {n} title", "busiest month, {n} titles", n),
    lambda n: ngettext("Your collection, {n} item.", "Your collection, {n} items.", n),
    lambda n: ngettext(
        "A collection by @{owner}, {n} item.", "A collection by @{owner}, {n} items.", n
    ),
    lambda n: ngettext(
        "Posted {n} card to your timeline", "Posted {n} cards to your timeline", n
    ),
]


def card_text() -> dict[str, str | list[str]]:
    """The card text in the active language, keyed by the English; empty for English, which the page has."""
    if (get_language() or "en").startswith("en"):
        return {}
    text: dict[str, str | list[str]] = {s: gettext(s) for s in TEXT}
    with override(None):
        keys = [f(1) for f in PLURALS]
    for key, f in zip(keys, PLURALS):
        text[key] = [f(1), f(2)]
    return text

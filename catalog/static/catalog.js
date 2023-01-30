function catalog_init(context) {
    // readonly star rating of detail display section
    let ratingLabels = $(".grid__main .rating-star", context);
    $(ratingLabels).each( function(index, value) {
        let ratingScore = $(this).data("rating-score") / 2;
        $(this).starRating({
            initialRating: ratingScore,
            readOnly: true,
        });
    });
    // readonly star rating at aside section
    ratingLabels = $("#aside .rating-star"), context;
    $(ratingLabels).each( function(index, value) {
        let ratingScore = $(this).data("rating-score") / 2;
        $(this).starRating({
            initialRating: ratingScore,
            readOnly: true,
            starSize: 15,
        });
    });
    // hide long text
    $(".entity-desc__content", context).each(function() {
        let copy = $(this).clone()
            .addClass('entity-desc__content--folded')
            .css("visibility", "hidden");
        $(this).after(copy);
        if ($(this).height() > copy.height()) {
            $(this).addClass('entity-desc__content--folded');
            $(this).siblings(".entity-desc__unfold-button").removeClass("entity-desc__unfold-button--hidden");
        }
        copy.remove();
    });

    // expand hidden long text
    $(".entity-desc__unfold-button a", context).on('click', function() {
        $(this).parent().siblings(".entity-desc__content").removeClass('entity-desc__content--folded');
        $(this).parent(".entity-desc__unfold-button").remove();
    });

    // spoiler
    $(".spoiler", context).on('click', function(){
        $(this).toggleClass('revealed');
    })

    // podcast
    $('.source-label__rss', context).parent().on('click', (e)=>{
        e.preventDefault();
    })
    $('.source-label__rss', context).parent().attr('title', 'Copy link here and subscribe in your podcast app');
}

$(function() {
    document.body.addEventListener('htmx:load', function(evt) {
        catalog_init(evt.detail.elt);
    });
    catalog_init(document.body);
});

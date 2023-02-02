function create_player(audio) {
    window.player = new Shikwasa.Player({
      container: () => document.querySelector('.player'),
      preload: 'metadata',
      autoplay: true,
      themeColor: '#1190C0',
      fixed: {
        type: 'fixed',
        position: 'bottom'
      },
      audio: audio
    });
    // $('.shk-title').on('click', e=>{
    //  window.location = "#";
    // });
    $('.footer').attr('style', 'margin-bottom: 120px !important');
}

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

    $('.episode', context).on('click', e=>{
        e.preventDefault();
        var ele = e.target;
        var album = $(ele).data('album');
        var artist = $(ele).data('hosts');
        var title = $(ele).data('title');
        var cover_url = $(ele).data('cover');
        var media_url = $(ele).data('media');
        var position = $(ele).data('position');
        if (!media_url) return;
        window.current_item_uuid = $(ele).data('uuid');
        if (!window.player) {
            create_player({
            title: title,
            cover: cover_url,
            src: media_url,
            album: album,
            artist: artist
          })
        } else {
            window.player.update({
            title: title,
            cover: cover_url,
            src: media_url,
            album: album,
            artist: artist
          })
        }
        if (position) window.player._initSeek = position;
        window.player.play()
    });
}

$(function() {
    document.body.addEventListener('htmx:load', function(evt) {
        catalog_init(evt.detail.elt);
    });
    catalog_init(document.body);
});

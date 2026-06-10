function create_player(audio) {
  window.player = new Shikwasa.Player({
    container: () => document.querySelector('.player'),
    preload: 'metadata',
    autoplay: true,
    themeColor: getComputedStyle(document.documentElement).getPropertyValue('--pico-primary'),
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
function set_podcast_comment_button(comment_href) {
  if (window.comment_btn){
    window.comment_btn.remove();
  }
  if (comment_href) {
    window.comment_btn = $('<button class="shk-btn shk-btn_comment" title="comment this episode" aria-label="comment" hx-get="'+comment_href+'" hx-target="body" hx-swap="beforeend"> <svg aria-hidden="true" width="16" height="16" fill="currentColor" class="bi bi-chat-dots" viewBox="0 0 16 16"> <path d="M5 8a1 1 0 1 1-2 0 1 1 0 0 1 2 0m4 0a1 1 0 1 1-2 0 1 1 0 0 1 2 0m3 1a1 1 0 1 0 0-2 1 1 0 0 0 0 2"/> <path d="m2.165 15.803.02-.004c1.83-.363 2.948-.842 3.468-1.105A9 9 0 0 0 8 15c4.418 0 8-3.134 8-7s-3.582-7-8-7-8 3.134-8 7c0 1.76.743 3.37 1.97 4.6a10.4 10.4 0 0 1-.524 2.318l-.003.011a11 11 0 0 1-.244.637c-.079.186.074.394.273.362a22 22 0 0 0 .693-.125m.8-3.108a1 1 0 0 0-.287-.801C1.618 10.83 1 9.468 1 8c0-3.192 3.004-6 7-6s7 2.808 7 6-3.004 6-7 6a8 8 0 0 1-2.088-.272 1 1 0 0 0-.711.074c-.387.196-1.24.57-2.634.893a11 11 0 0 0 .398-2"/> </svg> </button>')[0];
    window.player.ui.extraControls.append(window.comment_btn);
    htmx.process(window.comment_btn);
    // $('<button class="shk-btn shk-btn_info" title="open podcast page" aria-label="info"> <svg aria-hidden="true" width="16" height="16" fill="currentColor" class="bi bi-info-lg" viewBox="0 0 16 16"> <path d="m9.708 6.075-3.024.379-.108.502.595.108c.387.093.464.232.38.619l-.975 4.577c-.255 1.183.14 1.74 1.067 1.74.72 0 1.554-.332 1.933-.789l.116-.549c-.263.232-.65.325-.905.325-.363 0-.494-.255-.402-.704zm.091-2.755a1.32 1.32 0 1 1-2.64 0 1.32 1.32 0 0 1 2.64 0"/> </svg> </button>');
    // $('<button class="shk-btn shk-btn_mark" title="mark this podcast" aria-label="mark"> <svg aria-hidden="true" width="16" height="16" fill="currentColor" class="bi bi-bookmark" viewBox="0 0 16 16"> <path d="M2 2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v13.5a.5.5 0 0 1-.777.416L8 13.101l-5.223 2.815A.5.5 0 0 1 2 15.5zm2-1a1 1 0 0 0-1 1v12.566l4.723-2.482a.5.5 0 0 1 .554 0L13 14.566V2a1 1 0 0 0-1-1z"/> </svg> </button>');
  }
}
function podcast_init(context) {
  $('.episode', context).on('click', e=>{
    e.preventDefault();
    var ele = e.currentTarget;
    var album = $(ele).data('album');
    var artist = $(ele).data('hosts');
    var title = $(ele).data('title');
    var cover_url = $(ele).data('cover');
    var media_url = $(ele).data('media');
    var position = $(ele).data('position');
    var comment_href = $(ele).data('comment-href');
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
    set_podcast_comment_button(comment_href);
    if (position) window.player._initSeek = position;
    window.player.play()
  });
}

$(function() {
  document.body.addEventListener('htmx:load', function(evt) {
    podcast_init(evt.detail.elt);
  });
  podcast_init(document.body);
});

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
    podcast_init(evt.detail.elt);
  });
  podcast_init(document.body);
});

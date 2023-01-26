$(document).ready( function() {

    $(".submit").on('click', function(e) {
        e.preventDefault();
        let form = $("#scrapeForm form");
        if (form.data('submitted') === true) {
            // Previously submitted - don't submit again
        } else {
            // Mark it so that the next submit can be ignored
            form.data('submitted', true);
            $("#scrapeForm form").submit();
        }
    });

    // assume there  is only one input[file] on page
    // $("input[type='file']").each(function() {
    //     $(this).after('<img src="#" alt="" id="previewImage" style="margin:10px 0; max-width:500px;"/>');
    // });

    // preview uploaded pic
    $("input[type='file']").on("change", function() {
        if (this.files && this.files[0]) {
            var reader = new FileReader();

            reader.onload = function (e) {
                $('#previewImage').attr('src', e.target.result);
            }

            reader.readAsDataURL(this.files[0]);
        }
    });

    $("#parser textarea").on('paste', function(e) {

        // access the clipboard using the api
        let pastedData = e.originalEvent.clipboardData.getData('text');
        let lines = pastedData.split('\n')
        lines.forEach(line => {
            words = line.split(': ');
            if (words.length > 1) {
                switch (words[0]) {
                    case '作者':
                        authors = words[1].replace(' / ', ',');
                        $("input[name='author']").val(authors);
                        break;
                    case '译者':
                        translators = words[1].replace(' / ', ',');
                        $("input[name='translator']").val(translators);
                        break;
                    case '出版社':
                        $("input[name='pub_house']").val(words[1]);
                        break;
                    case '页数':
                        let tmp = Number(words[1]);
                        $("input[name='pages']").val(tmp);
                        break;
                    case '出版年':
                        let regex = /\d+\d*/g;
                        let figures = words[1].match(regex)
                        figures.forEach(figure => {
                            if (figure > 1000) $("input[name='pub_year']").val(figure);
                            else if (figure < 13) $("input[name='pub_month']").val(figure);
                        });
                        break;
                    case '定价':
                        $("input[name='price']").val(words[1]);
                        break;
                    case '装帧':
                        $("input[name='binding']").val(words[1]);
                        break;
                    case 'ISBN':
                        $("input[name='isbn']").val(words[1]);
                        break;
                    case '副标题':
                        $("input[name='subtitle']").val(words[1]);
                        break;
                    case '原作名':
                        $("input[name='orig_title']").val(words[1]);
                        break;
                    case '语言':
                        $("input[name='language']").val(words[1]);
                        break;
                    default:
                        $(".widget-value-key-input :nth-last-child(2)").val(words[0]);
                        $(".widget-value-key-input :nth-last-child(1)").val(words[1]);
                        $(".widget-value-key-input :nth-last-child(1)").trigger("input");
                        break;
                }
            }
        });
    });

});

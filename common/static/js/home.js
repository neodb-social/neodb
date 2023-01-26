$(document).ready( function() {
    $("#userInfoCard .mast-brief").text($("<div>"+$("#userInfoCard .mast-brief").text().replace(/\<br/g,'\n<br').replace(/\<p/g,'\n<p')+"</div>").text());
    $("#userInfoCard .mast-brief").html($("#userInfoCard .mast-brief").html().replace(/\n/g,'<br/>'));

    let token = $("#oauth2Token").text();
    let mast_domain = $("#mastodonURI").text();
    let mast_uri = 'https://' + mast_domain
    let id = $("#userMastodonID").text();

    if (id && id != 'None' && mast_domain != 'twitter.com') {
        // let userInfoSpinner = $("#spinner").clone().removeAttr("hidden");
        let followersSpinner = $("#spinner").clone().removeAttr("hidden");
        let followingSpinner = $("#spinner").clone().removeAttr("hidden");
        // $("#userInfoCard").append(userInfoSpinner);
        $("#followings h5").after(followingSpinner);
        $("#followers h5").after(followersSpinner);
        $(".mast-following-more").hide();
        $(".mast-followers-more").hide();

        getUserInfo(
            id,
            mast_uri,
            token,
            function(userData) {
                let userName;
                if (userData.display_name) {
                    userName = translateEmojis(userData.display_name, userData.emojis, true);
                } else {
                    userName = userData.username;
                }
                $("#userInfoCard .mast-avatar").attr("src", userData.avatar);
                $("#userInfoCard .mast-displayname").html(userName);
                $("#userInfoCard .mast-brief").text($('<div>'+userData.note+'</div>').text())
                // $(userInfoSpinner).remove();
            }
        );

        getFollowers(
            id,
            mast_uri,
            token,
            function(userList, request) {
                if (userList.length == 0) {
                    $(".mast-followers").hide();
                    $(".mast-followers").before('<div style="margin-bottom: 20px;">暂无</div>');

                } else {
                    if (userList.length > 4){
                        userList = userList.slice(0, 4);
                        $(".mast-followers-more").show();
                    }
                    let template = $(".mast-followers li").clone();
                    $(".mast-followers").html("");
                    userList.forEach(data => {
                        temp = $(template).clone();
                        temp.find("img").attr("src", data.avatar);
                        if (data.display_name) {
                            temp.find(".mast-displayname").html(translateEmojis(data.display_name, data.emojis));
                        } else {
                            temp.find(".mast-displayname").text(data.username);
                        }
                        let url;
                        if (data.acct.includes('@')) {
                            url = $("#userPageURL").text().replace('0', data.acct);
                        } else {
                            url = $("#userPageURL").text().replace('0', data.acct + '@' + mast_domain);
                        }
                        temp.find("a").attr('href', url);
                        $(".mast-followers").append(temp);
                    });
                }
                $(followersSpinner).remove();
            }
        );

        getFollowing(
            id,
            mast_uri,
            token,
            function(userList, request) {
                if (userList.length == 0) {
                    $(".mast-following").hide();
                    $(".mast-following").before('<div style="margin-bottom: 20px;">暂无</div>');
                } else {
                    if (userList.length > 4){
                        userList = userList.slice(0, 4);
                        $(".mast-following-more").show();
                    }
                    let template = $(".mast-following li").clone();
                    $(".mast-following").html("");
                    userList.forEach(data => {
                        temp = $(template).clone()
                        temp.find("img").attr("src", data.avatar);
                        if (data.display_name) {
                            temp.find(".mast-displayname").html(translateEmojis(data.display_name, data.emojis));
                        } else {
                            temp.find(".mast-displayname").text(data.username);
                        }
                        let url;
                        if (data.acct.includes('@')) {
                            url = $("#userPageURL").text().replace('0', data.acct);
                        } else {
                            url = $("#userPageURL").text().replace('0', data.acct + '@' + mast_domain);
                        }
                        temp.find("a").attr('href', url);
                        $(".mast-following").append(temp);
                    });
                }
                $(followingSpinner).remove();

            }
        );
    }

    // mobile dropdown
    $(".relation-dropdown__button").data("collapse", true);
    function onClickDropdownButton(e) {
        const button = $(".relation-dropdown__button");
        button.data("collapse", !button.data("collapse"));
        button.children('.icon-arrow').toggleClass("icon-arrow--expand");
        button.siblings('.relation-dropdown__body').toggleClass("relation-dropdown__body--expand");
    }
    $(".relation-dropdown__button").on('click', onClickDropdownButton);

    // close when click outside
    window.onclick = evt => {
        const target = $(evt.target);
        if (!target.parents('.relation-dropdown').length && !$(".relation-dropdown__button").data("collapse")) {
            onClickDropdownButton();
        }
    };

    // import panel
    $("#uploadBtn").on('click', e => {
        const btn = $("#uploadBtn")
        const form = $(".import-panel__body form")

        // validate form
        let isValidForm = form[0].checkValidity();
        if (!isValidForm) {
            if (form[0].reportValidity) {
                form[0].reportValidity();
            } else {
                alert("Invalid File");
            }
            return
        }
        e.preventDefault();

        let formData = new FormData(form[0]);

        // disable submit button
        btn.prop('disabled', true);

        // show progress bar & hide last status
        $(".import-panel__progress").show();
        $(".import-panel__last-task").hide();
        // flush failed urls
        $("#failedUrls").html("");
        // reset progress bar
        $("#importProgress").attr("max", 1);
        $("#importProgress").attr("value", 0);
        $("#progressPercent").text('0%');

        $.ajax({
            url: form.attr("action"),
            type: 'POST',
            data: formData,
            contentType: false,
            dataType: "json",
            processData: false,
            enctype: form.attr("enctype"),
            success: function (response) {
                // console.log(response);
                // long polling
                poll();
            },
            error: function (response) {
                // console.log(response)
            },
            complete: function (response) {
                // console.log(response)
            },
        });
    });

    // if progress is visible start to poll
    if ($(".import-panel__progress").is(":visible")) {
        poll();
    }

    // long polling function
    const pollingInterval = 1500;
    function poll() {
        $.ajax({
            url: $("#querySyncInfoURL").data("url"),
            success: function (data) {
                // console.log(data);
                const progress = $("#importProgress");
                const percent = $("#progressPercent");
                if (!data.is_finished) {
                    // update progress bar
                    if (!data.total_items == 0) {
                        progress.attr("max", data.total_items);
                        progress.attr("value", data.finished_items);
                        progress.attr("value", data.finished_items);
                        percent.text("" + data.finished_items + "/" + data.total_items);
                    }
                    setTimeout(() => {
                        poll();
                    }, pollingInterval);
                } else {
                    // task finishes
                    // update progress bar
                    percent.text('100%');
                    progress.attr("max", 1);
                    progress.attr("value", 1);
                    $("#uploadBtn").prop('disabled', false);
                    // update last task summary
                    $("#lastTaskTotalItems").text(data.total_items);
                    $("#lastTaskSuccessItems").text(data.success_items);
                    $("#lastTaskStatus").text(data.status);
                    // display failed urls
                    if (data.failed_urls.length > 0) {
                        $(".import-panel__fail-urls").show();
                        data.failed_urls.forEach((v, i) => {
                            $("#failedUrls").append($("<li>" + v + "</li>"));
                        });
                    }
                    // hide progress & show last task
                    $(".import-panel__progress").hide();
                    $(".import-panel__last-task").show();
                }
            },
            dataType: "json",
            complete: () => {
                // setTimeout(() => {
                //     poll();
                // }, pollingInterval);
            },
        });
    }

});

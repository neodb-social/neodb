
$(document).ready( function() {

    let token = $("#oauth2Token").text();
    let mast_uri = $("#mastodonURI").text();
    let mast_domain = new URL(mast_uri);
    mast_domain = mast_domain.hostname;
    let id = $("#userMastodonID").text();
    let nextUrl = null;
    let requesting = false;

    let userInfoSpinner = $("#spinner").clone().removeAttr("hidden");
    let followersSpinner = $("#spinner").clone().removeAttr("hidden");
    let followingSpinner = $("#spinner").clone().removeAttr("hidden");
    let mainSpinner = $("#spinner").clone().removeAttr("hidden");

    $(".mast-user").first().hide();

    $(".mast-user-list").append(mainSpinner);
    $("#userInfoCard").append(userInfoSpinner);
    $("#followings h5").after(followingSpinner);
    $("#followers h5").after(followersSpinner);
    $(".mast-following-more").hide();
    $(".mast-followers-more").hide();

    getUserInfo(
        id,
        mast_uri,
        token,
        function (userData) {
            let userName;
            if (userData.display_name) {
                userName = translateEmojis(userData.display_name, userData.emojis, true);
            } else {
                userName = userData.username;
            }
            $("#userInfoCard .mast-avatar").attr("src", userData.avatar);
            $("#userInfoCard .mast-displayname").html(userName);
            $("#userInfoCard .mast-brief").text($("<div>"+userData.note.replace(/\<br/g,'\n<br').replace(/\<p/g,'\n<p')+"</div>").text());
            $("#userInfoCard .mast-brief").html($("#userInfoCard .mast-brief").html().replace(/\n/g,'<br/>'));
            $(userInfoSpinner).remove();
        }
    );

    getFollowers(
        id,
        mast_uri,
        token,
        function(userList, nextPage) {
            let subUserList = null;
            if (userList.length == 0) {
                $(".mast-followers").hide();
                $(".mast-followers").before('<div style="margin-bottom: 20px;">暂无</div>');
            } else {
                if (userList.length > 4){
                    subUserList = userList.slice(0, 4);
                    $(".mast-followers-more").show();
                } else {
                    subUserList = userList;
                }
                let template = $(".mast-followers li").clone();
                $(".mast-followers").html("");
                subUserList.forEach(data => {
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
            // main
            let template = $(".mast-user").clone().show();

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
                temp.find(".mast-brief").text(data.note.replace(/(<([^>]+)>)/ig,""));
                $(".mast-user").last().after(temp);
            });

            mainSpinner.hide();
            nextUrl = nextPage;
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

    $(document.body).on('touchmove', () => {
        let scrollPosition = $(window).scrollTop();
        // test if scoll to bottom
        // mobile phone has extra offset
        if (scrollPosition + $(window).height() > $(document).height() - 70) {
            onScroll();
        }
    });


    $(window).on("scroll", function () {
        let scrollPosition = $(window).scrollTop();
        // test if scoll to bottom
        if (scrollPosition + $(window).height() > $(document).height() - 0.5) {
            onScroll();
        }
    });


    function onScroll() {
        if (!requesting && nextUrl) {
            // acquire lock
            requesting = true;
            mainSpinner.show();
            $.ajax({
                url: nextUrl,
                method: 'GET',
                data: {
                    'limit': NUMBER_PER_REQUEST,
                },
                headers: {
                    'Authorization': 'Bearer ' + token,
                },
                success: function (userList, status, request) {
                    if (userList.length == 0) {
                        mainSpinner.hide();
                        return;
                    }
                    let template = $(".mast-user").first().clone().show();
                    let newUrlFlag = false;
                    request.getResponseHeader('link').split(',').forEach(link => {
                        if (link.includes('next')) {
                            let regex = /<(.*?)>/;
                            nextUrl = link.match(regex)[1];
                            newUrlFlag = true;
                        }
                    });
                    if (!newUrlFlag) {
                        nextUrl = null;
                    }
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
                        temp.find(".mast-brief").text(data.note.replace(/(<([^>]+)>)/ig, ""));
                        // console.log($(temp).html())
                        $(".mast-user").last().after(temp);
                    });
                    mainSpinner.hide();
                    // release lock
                    // console.log(userList[userList.length-1].username)
                    // console.log(nextUrl)
                    requesting = false;
                },
            });
        }
    }

});

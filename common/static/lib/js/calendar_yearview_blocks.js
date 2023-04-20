(function ($) {

    $.fn.calendar_yearview_blocks = function (options) {

        // Format string
        if (!String.prototype.formatString) {
            String.prototype.formatString = function () {
                var args = arguments;
                return this.replace(/{(\d+)}/g, function (match, number) {
                    return typeof args[number] !== 'undefined'
                        ? args[number]
                        : match
                        ;
                });
            };
        }

        // If the number less than 10, add a zero before it
        var prettyNumber = function (number) {
            return number < 10 ? '0' + number.toString() : number = number.toString();
        };

        var getDisplayDate = function (date_obj) {
            var pretty_month = prettyNumber(date_obj.getMonth() + 1);
            var pretty_date = prettyNumber(date_obj.getDate());
            return "{0}-{1}-{2}".formatString(date_obj.getFullYear(), pretty_month, pretty_date);
        };

        var start = function () {
            obj_timestamp = JSON.parse(settings.data);

            var wrap_chart = _this;

            var end_date = new Date(settings.final_date);
            end_date.setDate(end_date.getDate()+1);
            var current_date = new Date();
            var start_date = new Date();
            start_date.setMonth(end_date.getMonth() - 12);

            var start_weekday = settings.start_monday === true?1:0;
            for (var i = 0; i < 7; i++) {
                var day = start_date.getDay();
                if (day === start_weekday) {
                    break;
                }
                else {
                    // Loop until start_weekday
                    start_date.setDate(start_date.getDate() + 1);
                }
            }
            var loop_html = "";

            // One year has 52 weeks
            var step = 13; // Amount of pixels to move

            var month_position = [];
            month_position.push({month_index: start_date.getMonth(), x: 0});
            var using_month = start_date.getMonth();
            for (var i = 0; i <= 52; i++) { // For each week, generate a column
                var g_x = i * step;
                var item_html = '<g transform="translate(' + g_x.toString() + ',0)">';

                for (var j = 0; j < 7; j++) { // For each weekday, generate a row

                    if (start_date > end_date) {
                        // Break the loop when today's date is found
                        break;
                    }
                    var y = j * step;

                    var month_in_day = start_date.getMonth();
                    var data_date = getDisplayDate(start_date);

                    // Check first day in week
                    if (j === start_weekday && month_in_day !== using_month) {
                        using_month = month_in_day;
                        month_position.push({month_index: using_month, x: g_x});
                    }

                    // Put a box around today's date
                    if (settings.stylize_today) {
                        var match_today = current_date.getTime() === start_date.getTime() ? '" style="stroke:black;stroke-width:2;opacity:0.5"' : '';
                    } else {
                        var match_today = "";
                    }

                    var items = [];
                    var legend = '', items_str = '';
                    if (obj_timestamp[data_date]) {
                        if (obj_timestamp[data_date].items) {
                            items = obj_timestamp[data_date].items;
							items_str = items.join(", ")
							items_str = items_str.replaceAll('&', '&amp;');
							items_str = items_str.replaceAll('"', '&quot;');
                        }
                        if (obj_timestamp[data_date].legend) {
                            legend = obj_timestamp[data_date].legend;
							legend = legend.replaceAll('&', '&amp;');
							legend = legend.replaceAll('"', '&quot;');
                        }
                    }

                    var item_name = items[0]?items[0]:false;
                    var color = settings.colors[item_name]?settings.colors[item_name]:settings.colors['default'];

                    // Fill a square for the 1st item
                    item_html += '<rect class="day" width="11" height="11" y="' + y + '" fill="' + color + match_today + '" data-items="' + items_str + '" data-legend="' + legend + '" data-date="' + data_date + '"/>';
                    if (items.length === 2) { // Fill a trangle for the 2nd
                        var item_name_1 = items[1]?items[1]:false;
                        var color_1 = settings.colors[item_name_1]?settings.colors[item_name_1]:settings.colors['default'];
                        item_html += '<polygon points="' + 0 + ',' + (y+11) + ' ' + 0 + ',' + y + ' ' + 11 + ',' + y + '" fill="' + color_1 + '" data-items="' + items_str + '" data-legend="' + legend + '" data-date="' + data_date + '"/>';
                    } else if (items.length === 3) { // Fill 2 rectangles for 2nd and 3rd
                        var item_name_1 = items[1]?items[1]:false;
                        var color_1 = settings.colors[item_name_1]?settings.colors[item_name_1]:settings.colors['default'];
                        var item_name_2 = items[2]?items[2]:false;
                        var color_2 = settings.colors[item_name_2]?settings.colors[item_name_2]:settings.colors['default'];
                        item_html += '<polygon points="' + 0 + ',' + (y+8) + ' ' + 0 + ',' + y + ' ' + 11 + ',' + y + ' ' + 11 + ',' + (y+8) + '" fill="' + color_1 + '" data-items="' + items_str + '" data-legend="' + legend + '" data-date="' + data_date + '"/>';
                        item_html += '<polygon points="' + 0 + ',' + (y+4) + ' ' + 0 + ',' + y + ' ' + 11 + ',' + y + ' ' + 11 + ',' + (y+4) + '" fill="' + color_2 + '" data-items="' + items_str + '" data-legend="' + legend + '" data-date="' + data_date + '"/>';
                    } else if (items.length === 4) { // Fill 3 cubes for 2nd, 3rd and 4th
                        var item_name_1 = items[1]?items[1]:false;
                        var color_1 = settings.colors[item_name_1]?settings.colors[item_name_1]:settings.colors['default'];
                        var item_name_2 = items[2]?items[2]:false;
                        var color_2 = settings.colors[item_name_2]?settings.colors[item_name_2]:settings.colors['default'];
                        var item_name_3 = items[3]?items[3]:false;
                        var color_3 = settings.colors[item_name_3]?settings.colors[item_name_3]:settings.colors['default'];
                        item_html += '<polygon points="' + 0 + ',' + (y+11) + ' ' + 0 + ',' + (y+6) + ' ' + 6 + ',' + (y+6) + ' ' + 6 + ',' + (y+11) + '" fill="' + color_1 + '" data-items="' + items_str + '" data-legend="' + legend + '" data-date="' + data_date + '"/>';
                        item_html += '<polygon points="' + 0 + ',' + (y+6) + ' ' + 0 + ',' + y + ' ' + 6 + ',' + y + ' ' + 6 + ',' + (y+6) + '" fill="' + color_2 + '" data-items="' + items_str + '" data-legend="' + legend + '" data-date="' + data_date + '"/>';
                        item_html += '<polygon points="' + 6 + ',' + (y+6) + ' ' + 6 + ',' + y + ' ' + 11 + ',' + y + ' ' + 11 + ',' + (y+6) + '" fill="' + color_3 + '" data-items="' + items_str + '" data-legend="' + legend + '" data-date="' + data_date + '"/>';
                    }

                    // Move on to the next day
                    start_date.setDate(start_date.getDate() + 1);

                }

                item_html += "</g>";

                loop_html += item_html;

            }

            // Trick
            if (month_position[1].x - month_position[0].x < 40) {
                // Fix ugly graph by removing the first item
                month_position.shift(0);
            }

            // Add labels for Months
            for (var i = 0; i < month_position.length; i++) {
                var item = month_position[i];
                var month_name = settings.month_names[item.month_index];
                loop_html += '<text x="' + item.x + '" y="-5" class="month">' + month_name + '</text>';
            }

            // Add labels for Weekdays
            if (settings.start_monday === true) {
                loop_html += '<text text-anchor="middle" class="wday" dx="-12" dy="11">{0}</text>'.formatString(settings.day_names[0]) +
                    '<text text-anchor="middle" class="wday" dx="-12" dy="36">{0}</text>'.formatString(settings.day_names[1]) +
                    '<text text-anchor="middle" class="wday" dx="-12" dy="61">{0}</text>'.formatString(settings.day_names[2]) +
                    '<text text-anchor="middle" class="wday" dx="-12" dy="86">{0}</text>'.formatString(settings.day_names[3]);
            } else {
                loop_html += '<text text-anchor="middle" class="wday" dx="-10" dy="22">{0}</text>'.formatString(settings.day_names[0]) +
                    '<text text-anchor="middle" class="wday" dx="-10" dy="48">{0}</text>'.formatString(settings.day_names[1]) +
                    '<text text-anchor="middle" class="wday" dx="-10" dy="74">{0}</text>'.formatString(settings.day_names[2]);
            }

            // Fixed size with width= 721 and height = 110
            var wire_html =
                '<svg width="721" height="110">' +
                '<g transform="translate(25, 20)">' +
                loop_html +
                '</g>' + '"Your browser does not support inline SVG."' +
                '</svg>';

            wrap_chart.html(wire_html);

            _this.find('rect, polygon').on("mouseenter", mouseEnter);
            _this.find('rect, polygon').on("mouseleave", mouseLeave);
            _this.find('rect, polygon').on("click", mouseClick);
            appendTooltip();

        };

        var mouseLeave = function (evt) {
            $('.svg-tip').hide();
        };

        var mouseClick = function (evt) {
            var items = $(evt.target).attr('data-items');
            var date = $(evt.target).attr('data-date');
            $("#moreinfo").text("On {0}, following items are seen: {1}".formatString(date, items))
        };

        // Handle mouseEnter event when entering into rect element
        var mouseEnter = function (evt) {

            var target_offset = $(evt.target).offset();
            var items = $(evt.target).attr('data-items');
            var legend = $(evt.target).attr('data-legend');
            var date = $(evt.target).attr('data-date');

            var text = settings.tooltip_style === 'default' ? "{0}: <br />{1}".formatString(date, legend ? legend : items) : (legend ? legend : items);

            // Depending on settings, only show a tooltip when there's something to be shown
            if (items.length >= 1 ||  settings.always_show_tooltip === true) {
                var svg_tip = $('.svg-tip').show();
                svg_tip.html(text);
                var svg_width = Math.round(svg_tip.width() / 2 + 5);
                var svg_height = svg_tip.height() * 2 + 10;

                svg_tip.css({top: target_offset.top - svg_height - 5});
                svg_tip.css({left: target_offset.left - svg_width});
            }

        };

        // Append tooltip to display when the mouse enters the rect element
        // Default is display:none
        var appendTooltip = function () {
            if ($('.svg-tip').length === 0) {
                $('body').append('<div class="svg-tip svg-tip-one-line" style="display:none" ></div>');
            }
        };

        // Default settings which can be overridden by the user
        var settings = $.extend({
            colors: {
                    'default': '#eeeeee'
            },
            month_names: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
            day_names: ['M', 'W', 'F', 'S'],
            start_monday: true,
            always_show_tooltip: false,
            stylize_today: true,
            final_date: new Date().toISOString().slice(0, 10),
            tooltip_style: 'default', // or 'custom'
            data: []
        }, options);

        var _this = $(this);

        start();

    };

}(jQuery));

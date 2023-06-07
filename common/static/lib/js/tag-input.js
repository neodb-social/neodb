function inputTags(configs) {


    let tagsContainer = configs.container,
        input = configs.container.querySelector('input')

    let _privateMethods = {

        init: function (configs) {

            // this.inspectConfigProperties(configs);

            let self = this,
                input_hidden = document.createElement('input');
            let name = input.getAttribute('name'),
                id = input.getAttribute('id');
            input.removeAttribute('name');
            // input.removeAttribute('id');
            input_hidden.setAttribute('type', 'hidden');
            // input_hidden.setAttribute('id', id);
            input_hidden.setAttribute('name', name);
            input.parentNode.insertBefore(input_hidden, input);
            this.input_hidden = input_hidden

            tagsContainer.addEventListener('click', function () {
                input.focus();
            });

            if (configs.tags) {
                for (let i = 0; i < configs.tags.length; i++) {
                    if (configs.tags[i]) {
                        this.create(configs.tags[i]);
                    }
                }
            }

            input.addEventListener("focusout", function () {

                let tag_txt = this.value.trim(),
                    tag_exists = false;

                if (self.tags_array) {
                    tag_exists = Boolean(self.tags_array.indexOf(tag_txt) + 1);
                }

                if (tag_txt && tag_exists && !configs.allowDuplicateTags) {
                    self.showDuplicate(tag_txt);
                }
                else if (tag_txt && tag_exists && configs.allowDuplicateTags) {
                    self.create(tag_txt);
                }
                else if (tag_txt && !tag_exists) {
                    self.create(tag_txt);
                }
                this.value = "";

            });

            input.addEventListener('keydown', function (ev) {


                if (ev.keyCode === 13 || ev.keyCode === 188 ||
                    (ev.keyCode === 32 && configs.allowDuplicateTags)) { // enter || comma || space
                    let event = new Event('focusout');
                    input.dispatchEvent(event);
                    ev.preventDefault();
                }
                else if (event.which === 8 && !input.value) { // backspace
                    let tag_nodes = document.querySelectorAll('.tag-input__tag');
                    if (tag_nodes.length > 0) {
                        input.addEventListener('keyup', function (event) {
                            if (event.which === 8) {
                                let node_to_del = tag_nodes[tag_nodes.length - 1];
                                node_to_del.remove();
                                self.update();
                            }
                        });
                    }
                    ev.preventDefault();
                }
            });
        },

        create: function (tag_txt) {

            let tag_nodes = document.querySelectorAll('.tag-input__tag');

            if (!configs.maxTags || tag_nodes.length < configs.maxTags) {
                let self = this,
                    span_tag = document.createElement('span'),
                    input_hidden_field = self.input_hidden;

                span_tag.setAttribute('class', 'tag-input__tag');
                span_tag.innerText = tag_txt;

                let span_tag_close = document.createElement('span');
                span_tag_close.setAttribute('class', 'tag-input__close');
                span_tag.appendChild(span_tag_close);

                tagsContainer.insertBefore(span_tag, input_hidden_field);

                span_tag.childNodes[1].addEventListener('click', function () {
                    self.remove(this);
                });

                this.update();

            }
        },

        update: function () {

            let tags = document.getElementsByClassName('tag-input__tag'),
                tags_arr = [];

            for (let i = 0; i < tags.length; i++) {
                tags_arr.push(tags[i].textContent.replace(/\s+/g, ' ').trim());
            }
            this.tags_array = tags_arr;

            this.input_hidden.setAttribute('value', tags_arr.join());
        },

        remove: function (tag) {
            configs.onTagRemove(tag.parentNode.textContent);
            tag.parentNode.remove();
            this.update();
        },

        showDuplicate: function (tag_value) {
            let tags = document.getElementsByClassName('tag-input__tag');

            for (let i = 0; i < tags.length; i++) {
                if (tags[i].textContent === tag_value) {
                    tags[i].classList.add("tag-input__tag--highlight");
                    window.setTimeout(function () {
                        tags[i].classList.remove("tag-input__tag--highlight");
                    }, configs.duplicateTime);
                }
            }
        }
    }

    _privateMethods.init(configs);
    // return false;
}

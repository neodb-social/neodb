{% load i18n %}{
  "id": "{{ site_url }}",
  "name": "{{ site_name }}",
  "short_name": "{{ site_name }}",
  "description": "{{ site_description }}",
  "categories": ["social"],
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "icons": [
    {
      "src": "{{site_url}}{{ site_icon }}",
      "type": "image/png",
      "sizes": "128x128",
      "purpose": "any maskable"
    }
  ],
  "shortcuts": [
    {
      "name": "{% trans 'Discover' %}",
      "url": "{% url 'catalog:discover' %}"
    },
    {
      "name": "{% trans 'Activities' %}",
      "url": "{% url 'social:feed' %}"
    },
    {
      "name": "{% trans 'Profile' %}",
      "url": "{% url 'common:me' %}"
    },
    {
      "name": "{% trans 'Notifications' %}",
      "url": "{% url 'social:notification' %}"
    }
  ],
  "share_target": {
    "url_template": "{% url 'common:share' %}?title={title}\u0026text={text}\u0026url={url}",
    "action": "{% url 'common:share' %}",
    "method": "GET",
    "enctype": "application/x-www-form-urlencoded",
    "params": {
      "title": "title",
      "text": "text",
      "url": "url"
    }
  }
}

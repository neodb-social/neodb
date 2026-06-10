{% load i18n %}<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
<ShortName>{{ site_name }}</ShortName>
<Description>{% trans 'book, movie, tv music, game, podcast and etc' %}</Description>
<InputEncoding>UTF-8</InputEncoding>
<Image type="image/png" width="128" height="128">{{site_url}}{{ site_icon }}</Image>
<Url type="text/html" template="{{site_url}}{% url 'common:search' %}?q={searchTerms}"/>
</OpenSearchDescription>

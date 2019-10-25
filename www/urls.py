#!/usr/bin/python
# coding: utf-8

# maposmatic, the web front-end of the MapOSMatic city map generation system
# Copyright (C) 2009  David Decotigny
# Copyright (C) 2009  Frédéric Lehobey
# Copyright (C) 2009  David Mentré
# Copyright (C) 2009  Maxime Petazzoni
# Copyright (C) 2009  Thomas Petazzoni
# Copyright (C) 2009  Gaël Utard
# Copyright (C) 2018  Hartmut Holzgraefe

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import django
from django.conf.urls import url, include
from django.views.static import serve

# Uncomment the next two lines to enable the admin:
# from django.contrib import admin
# admin.autodiscover()

from .maposmatic import feeds
from .maposmatic import views
from .maposmatic import apis

from . import settings

urlpatterns = [
    url(r'^$',
        views.index,
        name='main'),

    url(r'^new/$',
        views.new,
        name='new'),
    url(r'^recreate/$',
        views.recreate,
        name='recreate'),
    url(r'^cancel/$',
        views.cancel,
        name='cancel'),

    url(r'^maps/(?P<id>\d+)/(?P<nonce>[A-Za-z]{16})$',
        views.map_full,
        name='map-by-id-and-nonce'),
    url(r'^maps/(?P<id>\d+)$',
        views.map_full,
        name='map-by-id'),
    url(r'^maps/(?P<category>[a-z]+)$',
        views.maps,
        name='maps-list-cagetory'),
    url(r'^maps/$',
        views.maps,
        name='maps'),
    url(r'^overview/$',
        views.overview,
        name='overview'),

    url(r'^about/api/$',
        views.documentation_api,
        name='documentation_api'),
    url(r'^about/user-guide/$',
        views.documentation_user_guide,
        name='documentation_user_guide'),
    url(r'^about/$',
        views.about,
        name='about'),
    
    url(r'^donate/$',
        views.donate,
        name='donate'),
    url(r'^donate-thanks/$',
        views.donate_thanks,
        name='donate-thanks'),

    # API calls used by the web frontend
    # url(r'^apis/nominatim/$', views.api_nominatim),
    url(r'^apis/nominatim/$', views.api_geosearch),
    url(r'^apis/reversegeo/([^/]*)/([^/]*)/$', views.api_postgis_reverse),
    url(r'^apis/papersize', views.api_papersize),
    url(r'^apis/boundingbox/([^/]*)/$', views.api_bbox),
    url(r'^apis/polygon/([^/]*)/$',     views.api_polygon),
    url(r'^apis/rendering-status/([^/]*)$', views.api_rendering_status),

    # API calls for direct clients

    # unversioned
    url(r'^apis/paper_formats',         apis.paper_formats),
    url(r'^apis/layouts',               apis.layouts),
    url(r'^apis/styles',                apis.styles),
    url(r'^apis/overlays',              apis.overlays),
    url(r'^apis/job-stati',             apis.job_stati),
    url(r'^apis/jobs$',                 apis.jobs),
    url(r'^apis/jobs/(\d*)$',           apis.jobs),

    # versioned
    url(r'^apis/v1/paper_formats',         apis.paper_formats),
    url(r'^apis/v1/layouts',               apis.layouts),
    url(r'^apis/v1/styles',                apis.styles),
    url(r'^apis/v1/overlays',              apis.overlays),
    url(r'^apis/v1/job-stati',             apis.job_stati),
    url(r'^apis/v1/jobs$',                 apis.jobs),
    url(r'^apis/v1/jobs/(\d*)$',           apis.jobs),

    # Feeds
    url(r'feeds/maps/$', feeds.MapsFeed(), name='rss-feed'),
    url(r'feeds/errors/$', feeds.ErrorFeed(), name='error-feed'),

    # Internationalization
    url(r'^i18n/', include('django.conf.urls.i18n')),
]

if settings.DEBUG:
    urlpatterns.append(
        url(r'^results/(?P<path>.*)$', serve,
         {'document_root': settings.RENDERING_RESULT_PATH}))

    urlpatterns.append(
        url(r'^media/(?P<path>.*)$', serve,
         {'document_root': settings.LOCAL_MEDIA_PATH}))

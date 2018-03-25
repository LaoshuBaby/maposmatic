# coding: utf-8

# maposmatic, the web front-end of the MapOSMatic city map generation system
# Copyright (C) 2009  David Decotigny
# Copyright (C) 2009  Frédéric Lehobey
# Copyright (C) 2009  David Mentré
# Copyright (C) 2009  Maxime Petazzoni
# Copyright (C) 2009  Thomas Petazzoni
# Copyright (C) 2009  Gaël Utard

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
from django.core.urlresolvers import reverse
import django.utils.translation
import feedparser
import datetime

from .models import MapRenderingJob
import www.settings

from www.maposmatic import gisdb
from www.maposmatic import forms
import psycopg2

def get_latest_blog_posts():
    f = feedparser.parse(www.settings.MAPOSMATIC_RSS_FEED)
    return f.entries[:4]

def get_osm_database_last_update():
    """Returns the timestamp of the last PostGIS database update, which is
    placed into the maposmatic_admin table in the PostGIS database by the
    planet-update incremental update script."""

    cursor = None

    try:
        db = gisdb.get()
        if db is None:
            return None

        cursor = db.cursor()
        if cursor is None:
            return None

        cursor.execute("""select last_update from maposmatic_admin""")
        last_update = cursor.fetchone()
        if last_update is None or len(last_update) != 1:
            return None

        return last_update[0]
    except:
        pass
    finally:
        # Close the DB cursor if necessary
        if cursor is not None and not cursor.closed:
            cursor.close()

    return None

def get_waymarked_database_last_update():
    """Returns the timestamp of the last PostGIS database update, which is
    placed into the waymarked_admin table in the PostGIS database by the
    waymarked-update incremental update script."""

    cursor = None

    try:
        db = gisdb.get()
        if db is None:
            return None

        cursor = db.cursor()
        if cursor is None:
            return None

        cursor.execute("""select (last_update - interval '2 hour') as last_update from waymarked_admin""")
        last_update = cursor.fetchone()
        if last_update is None or len(last_update) != 1:
            return None

        return last_update[0]
    except:
        pass
    finally:
        # Close the DB cursor if necessary
        if cursor is not None and not cursor.closed:
            cursor.close()

    return None

def all(request):
    # Do not add the useless overhead of parsing blog entries when generating
    # the rss feed
    if (django.VERSION[1] >= 4 and request.path == reverse('rss-feed')) or \
       (django.VERSION[1] < 4 and request.path == reverse('rss-feed', args=['maps'])):
        return {}

    l = django.utils.translation.get_language()
    if l in www.settings.PAYPAL_LANGUAGES:
        paypal_lang_code = www.settings.PAYPAL_LANGUAGES[l][0]
        paypal_country_code = www.settings.PAYPAL_LANGUAGES[l][1]
    else:
        paypal_lang_code = "en_US"
        paypal_country_code = "US"

    daemon_running = www.settings.is_daemon_running()

    gis_lastupdate = get_osm_database_last_update()
    gis_lag_ok = (gis_lastupdate
        and datetime.datetime.utcnow() - gis_lastupdate < datetime.timedelta(minutes=30)
        or False)

    waymarked_lastupdate = get_waymarked_database_last_update()
    waymarked_lag_ok = (waymarked_lastupdate 
        and datetime.datetime.utcnow() -  waymarked_lastupdate < datetime.timedelta(minutes=120)
        or False)

    platform_status = 'off'

    if daemon_running and gis_lag_ok and waymarked_lag_ok:
        platform_status = 'ok'
    elif daemon_running and gis_lastupdate and not (gis_lag_ok and waymarked_lag_ok):
        platform_status = 'warning-sign'

    return {
        'DEBUG': www.settings.DEBUG,
        'LANGUAGES': www.settings.LANGUAGES,
        'LANGUAGES_LIST': www.settings.LANGUAGES_LIST,
        'MAP_LANGUAGES': www.settings.MAP_LANGUAGES,
        'BBOX_MAXIMUM_LENGTH_IN_METERS': www.settings.BBOX_MAXIMUM_LENGTH_IN_METERS,

        'searchform': forms.MapSearchForm(request.GET),
        'blogposts': get_latest_blog_posts(),

        'daemon_running': daemon_running,
        'gis_lastupdate': gis_lastupdate,
        'gis_lag_ok': gis_lag_ok,
        'waymarked_lastupdate': waymarked_lastupdate,
        'waymarked_lag_ok': waymarked_lag_ok,
        'utc_now': datetime.datetime.utcnow(),
        'platform_status': platform_status,

        'paypal_lang_code': paypal_lang_code,
        'paypal_country_code': paypal_country_code,
    }

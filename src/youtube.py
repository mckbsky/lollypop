# Copyright (c) 2014-2016 Cedric Bellegarde <cedric.bellegarde@adishatz.org>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from gi.repository import GLib, Gio, GObject

from gettext import gettext as _
from threading import Thread
import json

from lollypop.sqlcursor import SqlCursor
from lollypop.tagreader import TagReader
from lollypop.objects import Track, Album
from lollypop.define import Lp, DbPersistent, GOOGLE_API_ID


class Youtube(GObject.GObject):
    """
        Youtube helper
    """
    __gsignals__ = {
        'uri-set': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self):
        """
            Init helper
        """
        GObject.GObject.__init__(self)

    def save_track(self, item, persistent):
        """
            Save item into collection as track
            @param item as SearchItem
            @param persistent as DbPersistent
        """
        t = Thread(target=self.__save_track_thread, args=(item, persistent))
        t.daemon = True
        t.start()

    def save_album(self, item, persistent):
        """
            Save item into collection as album
            @param item as SearchItem
            @param persistent as DbPersistent
        """
        Lp().window.progress.add(self)
        t = Thread(target=self.__save_album_thread, args=(item, persistent))
        t.daemon = True
        t.start()

#######################
# PRIVATE             #
#######################
    def __save_album_thread(self, item, persistent):
        """
            Save item into collection as album
            @param item as SearchItem
            @param persistent as DbPersistent
        """
        nb_items = len(item.subitems)
        start = 0
        album_artist = item.subitems[0].artists[0]
        for track_item in item.subitems:
            (album_id, track_id) = self.__save_track(track_item, persistent,
                                                     album_artist)
            if track_id is None:
                continue
            # Download cover
            if start == 0:
                t = Thread(target=self.__save_cover, args=(item, album_id))
                t.daemon = True
                t.start()
            start += 1
            GLib.idle_add(Lp().window.progress.set_fraction,
                          start / nb_items, self)
        # Play if needed
        if persistent == DbPersistent.NONE:
            Lp().player.clear_albums()
            GLib.idle_add(Lp().player.load, Track(track_id))
            GLib.idle_add(Lp().player.add_album, Album(album_id))
        if Lp().settings.get_value('artist-artwork'):
            Lp().art.cache_artists_info()

    def __save_track_thread(self, item, persistent):
        """
            Save item into collection as track
            @param item as SearchItem
            @param persistent as DbPersistent
        """
        album_artist = item.artists[0]
        (album_id, track_id) = self.__save_track(item, persistent,
                                                 album_artist)
        if track_id is None:
            return
        self.__save_cover(item, album_id)
        if Lp().settings.get_value('artist-artwork'):
            Lp().art.cache_artists_info()
        if persistent == DbPersistent.NONE:
            GLib.idle_add(Lp().player.load, Track(track_id))

    def __save_track(self, item, persistent, album_artist):
        """
            Save item into collection as track
            @param item as SearchItem
            @param persistent as DbPersistent
            @param album artist as str
            @return (album id as int, track id as int)
        """
        yid = self.__get_youtube_id(item)
        if yid is None:
            return (None, None)
        uri = "https://www.youtube.com/watch?v=%s" % yid
        track_id = Lp().tracks.get_id_by_uri(uri)
        # Check if track needs to be updated
        if track_id is not None:
            if Lp().tracks.get_persistent(track_id) == DbPersistent.NONE\
                    and persistent == DbPersistent.EXTERNAL:
                Lp().tracks.set_persistent(track_id, DbPersistent.EXTERNAL)
                return (None, None)
        t = TagReader()
        with SqlCursor(Lp().db) as sql:
            artists = "; ".join(item.artists)
            (artist_ids, new_artist_ids) = t.add_artists(artists,
                                                         album_artist,
                                                         "")
            (album_artist_ids, new_album_artist_ids) = t.add_album_artists(
                                                               album_artist,
                                                               "")
            (album_id, new_album) = t.add_album(item.album,
                                                album_artist_ids,
                                                "", 0, 0)

            (genre_ids, new_genre_ids) = t.add_genres(_("Youtube"), album_id)

            # Add track to db
            uri = "https://www.youtube.com/watch?v=%s" % yid
            track_id = Lp().tracks.add(item.name, uri, item.duration,
                                       0, item.discnumber, "",
                                       album_id, None, 0, 0, 0, persistent)
            t.update_track(track_id, artist_ids, genre_ids)
            t.update_album(album_id, album_artist_ids, genre_ids, None)
            sql.commit()
        # Notify about new artists/genres
        if new_genre_ids or new_artist_ids:
            for genre_id in new_genre_ids:
                GLib.idle_add(Lp().scanner.emit, 'genre-updated',
                              genre_id, True)
            for artist_id in new_artist_ids:
                GLib.idle_add(Lp().scanner.emit, 'artist-updated',
                              artist_id, album_id, True)
        return (album_id, track_id)

    def __get_youtube_id(self, item):
        """
            Get youtube id
            @param item as SearchItem
        """
        search = "%s %s" % (item.artists[0],
                            item.name)
        key = Lp().settings.get_value('cs-api-key').get_string()
        try:
            f = Gio.File.new_for_uri("https://www.googleapis.com/youtube/v3/"
                                     "search?part=snippet&q=%s&"
                                     "type=video&key=%s&cx=%s" % (
                                                              search,
                                                              key,
                                                              GOOGLE_API_ID))
            (status, data, tag) = f.load_contents(None)
            if status:
                decode = json.loads(data.decode('utf-8'))
                return decode['items'][0]['id']['videoId']
        except Exception as e:
            print("Youtube::__get_youtube_id():", e, f.get_uri())
        return None

    def __save_cover(self, item, album_id):
        """
            Save cover to store
            @param item as SearchItem
            @param album id as int
        """
        f = Gio.File.new_for_uri(item.cover)
        (status, data, tag) = f.load_contents(None)
        if status:
            Lp().art.save_album_artwork(data, album_id)

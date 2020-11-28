#!/usr/bin/env python
# -*- encoding: utf-8
"""
Download podcast files based on your Overcast export.

If you have an Overcast account, you can download an OPML file with
a list of every episode you've played from https://overcast.fm/account.

This tool can read that OPML file, and save a local copy of the audio files
for every episode you've listened to.
"""

import argparse
import datetime
import errno
import filecmp
import functools
import glob
import itertools
import json
import os
import shutil
import sqlite3
import sys
from urllib.parse import urlparse
from urllib.request import build_opener, install_opener, urlretrieve
import xml.etree.ElementTree as ET


def parse_args(argv):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "OPML_PATH",
        help="Path to an OPML file downloaded from https://overcast.fm/account",
    )

    parser.add_argument(
        "--download_dir",
        default="audiofiles",
        help="directory to save podcast information to to",
    )

    args = parser.parse_args(argv)

    return {
        "opml_path": os.path.abspath(args.OPML_PATH),
        "download_dir": os.path.abspath(args.download_dir),
    }


def get_episodes(xml_string):
    """
    Given the XML string of the Overcast OPML, generate a sequence of entries
    that represent a single, played podcast episode.
    """
    root = ET.fromstring(xml_string)

    # The Overcast OPML has the following form:
    #
    #   <?xml version="1.0" encoding="utf-8"?>
    #   <opml version="1.0">
    #       <head><title>Overcast Podcast Subscriptions</title></head>
    #       <body>
    #           <outline text="playlists">...</outline>
    #           <outline text="feeds">...</outline>
    #       </body>
    #   </opml>
    #
    # Within the <outline text="feeds"> block of XML, there's a list of feeds
    # with the following structure (some attributes omitted):
    #
    #   <outline type="rss"
    #            title="My Example Podcast"
    #            xmlUrl="https://example.org/podcast.xml">
    #       <outline type="podcast-episode"
    #                overcastId="12345"
    #                pubDate="2001-01-01T01:01:01-00:00"
    #                title="The first episode"
    #                url="https://example.net/podcast/1"
    #                overcastUrl="https://overcast.fm/+ABCDE"
    #                enclosureUrl="https://example.net/files/1.mp3"/>
    #       ...
    #   </outline>
    #
    # We use an XPath expression to find the <outline type="rss"> entries
    # (so we get the podcast metadata), and then find the individual
    # "podcast-episode" entries in that feed.

    for feed in root.findall("./body/outline[@text='feeds']/outline[@type='rss']"):
        podcast = {
            "title": feed.get("title"),
            "text": feed.get("text"),
            "xml_url": feed.get("xmlUrl"),
        }

        for episode_xml in feed.findall("./outline[@type='podcast-episode']"):
            episode = {
                "published_date": episode_xml.get("pubDate"),
                "title": episode_xml.get("title"),
                "url": episode_xml.get("url"),
                "overcast_id": episode_xml.get("overcastId"),
                "overcast_url": episode_xml.get("overcastUrl"),
                "enclosure_url": episode_xml.get("enclosureUrl"),
            }

            yield {
                "podcast": podcast,
                "episode": episode,
            }


def has_episode_been_downloaded_already(episode, download_dir):
    try:
        conn = sqlite3.connect(os.path.join(download_dir, "overcast.db"))
    except sqlite3.OperationalError as err:
        if err.args[0] == "unable to open database file":
            return False
        else:
            raise

    c = conn.cursor()

    try:
        c.execute(
            "SELECT * FROM downloaded_episodes WHERE overcast_id=?",
            (episode["episode"]["overcast_id"],),
        )
    except sqlite3.OperationalError as err:
        if err.args[0] == "no such table: downloaded_episodes":
            return False
        else:
            raise

    return c.fetchone() is not None


def mark_episode_as_downloaded(episode, download_dir):
    conn = sqlite3.connect(os.path.join(download_dir, "overcast.db"))
    c = conn.cursor()

    try:
        c.execute("CREATE TABLE downloaded_episodes (overcast_id text PRIMARY KEY)")
    except sqlite3.OperationalError as err:
        if err.args[0] == "table downloaded_episodes already exists":
            pass
        else:
            raise

    c.execute(
        "INSERT INTO downloaded_episodes VALUES (?)",
        (episode["episode"]["overcast_id"],),
    )
    conn.commit()
    conn.close()


def _escape(s):
    return s.replace(":", "-").replace("/", "-")


def get_filename(*, download_url, title):
    url_path = urlparse(download_url).path

    extension = os.path.splitext(url_path)[-1]
    base_name = _escape(title)

    return base_name + extension


def download_url(*, url, path, description):
    # Some sites block the default urllib User-Agent headers, so we can customise
    # it to something else if necessary.
    opener = build_opener()
    opener.addheaders = [("User-agent", "Mozilla/5.0")]
    install_opener(opener)

    try:
        tmp_path, _ = urlretrieve(url)
    except Exception as err:
        print(f"Error downloading {description}: {err}")
    else:
        print(f"Downloading {description} successful!")
        try:
            os.rename(tmp_path, path)
        except OSError as err:
            if err.errno == errno.EXDEV:
                shutil.move(tmp_path, path)
            else:
                raise


def download_episode(episode, download_dir):
    """
    Given a blob of episode data from get_episodes, download the MP3 file and
    save the metadata to ``download_dir``.
    """
    if has_episode_been_downloaded_already(episode=episode, download_dir=download_dir):
        return

    # If the MP3 URL is https://example.net/mypodcast/podcast1.mp3 and the
    # title is "Episode 1: My Great Podcast", the filename is
    # ``Episode 1- My Great Podcast.mp3``.
    audio_url = episode["episode"]["enclosure_url"]

    filename = get_filename(download_url=audio_url, title=episode["episode"]["title"])

    # Within the download_dir, put the episodes for each podcast in the
    # same folder.
    podcast_dir = os.path.join(download_dir, _escape(episode["podcast"]["title"]))
    os.makedirs(podcast_dir, exist_ok=True)

    # Download the podcast audio file if it hasn't already been downloaded.
    download_path = os.path.join(podcast_dir, filename)
    base_name = _escape(episode["episode"]["title"])
    json_path = os.path.join(podcast_dir, base_name + ".json")

    # If the MP3 file already exists, check to see if it's the same episode,
    # or if this podcast isn't using unique filenames.
    #
    # If a podcast has multiple episodes with the same filename in its feed,
    # append the Overcast ID to disambiguate.
    if os.path.exists(download_path):
        try:
            cached_metadata = json.load(open(json_path, "r"))
        except Exception as err:
            print(err, json_path)
            raise

        cached_overcast_id = cached_metadata["episode"]["overcast_id"]
        this_overcast_id = episode["episode"]["overcast_id"]

        if cached_overcast_id != this_overcast_id:
            filename = filename.replace(".mp3", "_%s.mp3" % this_overcast_id)
            old_download_path = download_path
            download_path = os.path.join(podcast_dir, filename)
            json_path = download_path + ".json"

            print(
                "Downloading %s: %s to %s"
                % (episode["podcast"]["title"], audio_url, filename)
            )
            download_url(url=audio_url, path=download_path, description=audio_url)

            try:
                if filecmp.cmp(download_path, old_download_path, shallow=False):
                    print("Duplicates detected! %s" % download_path)
                    os.unlink(download_path)
                    download_path = old_download_path
            except FileNotFoundError:
                # This can occur if the download fails -- say, the episode is
                # in the Overcast catalogue, but no longer available from source.
                pass

        else:
            # Already downloaded and it's the same episode.
            pass

    # This episode has never been downloaded before, so we definitely have
    # to download it fresh.
    else:
        print(
            "Downloading %s: %s to %s"
            % (episode["podcast"]["title"], audio_url, filename)
        )
        download_url(url=audio_url, path=download_path, description=audio_url)

    # Save a blob of JSON with some episode metadata
    episode["filename"] = filename

    json_string = json.dumps(episode, indent=2, sort_keys=True)

    with open(json_path, "w") as outfile:
        outfile.write(json_string)

    save_rss_feed(episode=episode, download_dir=download_dir)
    mark_episode_as_downloaded(episode=episode, download_dir=download_dir)


def save_rss_feed(*, episode, download_dir):
    _save_rss_feed(
        title=episode["podcast"]["title"],
        xml_url=episode["podcast"]["xml_url"],
        download_dir=download_dir
    )


# Use caching so we only have to download this RSS feed once.
@functools.lru_cache()
def _save_rss_feed(*, title, xml_url, download_dir):
    podcast_dir = os.path.join(download_dir, _escape(title))

    today = datetime.datetime.now().strftime("%Y-%m-%d")

    rss_path = os.path.join(podcast_dir, f"feed.{today}.xml")

    if not os.path.exists(rss_path):
        print("Downloading RSS feed for %s" % title)
        download_url(
            url=xml_url,
            path=rss_path,
            description="RSS feed for %s" % title,
        )

    matching_feeds = sorted(glob.glob(os.path.join(podcast_dir, "feed.*.xml")))

    while (
        len(matching_feeds) >= 2 and
        filecmp.cmp(matching_feeds[-2], matching_feeds[-1], shallow=False)
    ):
        os.unlink(matching_feeds[-1])
        matching_feeds.remove(matching_feeds[-1])


if __name__ == "__main__":
    args = parse_args(argv=sys.argv[1:])

    opml_path = args["opml_path"]
    download_dir = args["download_dir"]

    try:
        with open(opml_path) as infile:
            xml_string = infile.read()
    except OSError as err:
        if err.errno == errno.ENOENT:
            sys.exit("Could not find an OPML file at %s" % opml_path)
        else:
            raise

    for episode in get_episodes(xml_string):
        download_episode(episode, download_dir=download_dir)

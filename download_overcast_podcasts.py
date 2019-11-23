#!/usr/bin/env python
# -*- encoding: utf-8
"""
Download podcast files based on your Overcast export.

If you have an Overcast account, you can download an OPML file with
a list of every episode you've played from https://overcast.fm/account

This tool can read that OPML file, and save a local copy of the audio files
for every episode you've listened to.
"""

import argparse
import errno
import json
import os
import sys
import xml.etree.ElementTree as ET

try:
    from urllib.parse import urlparse
    from urllib.request import urlretrieve
except ImportError:  # Python 2.7
    from urllib import urlretrieve
    from urlparse import urlparse


def parse_args(argv):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "OPML_PATH",
        help="Path to an OPML file downloaded from https://overcast.fm/account",
    )

    parser.add_argument(
        "--download_dir", default="audiofiles", help="Directory to save audio files to"
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


def mkdir_p(path):
    """Create a directory if it doesn't already exist."""
    try:
        os.makedirs(path)
    except OSError as err:
        if err.errno == errno.EEXIST:
            pass
        else:
            raise


def download_episode(episode, download_dir):
    """
    Given a blob of episode data from get_episodes, download the MP3 file and
    save the metadata to ``download_dir``.
    """
    # If the MP3 URL is https://example.net/mypodcast/podcast1.mp3,
    # get the filename ``podcast1.mp3``.
    audio_url = episode["episode"]["enclosure_url"]
    url_path = urlparse(audio_url).path
    filename = os.path.basename(url_path)

    # Within the download_dir, put the episodes for each podcast in the
    # same folder.
    podcast_dir = os.path.join(download_dir, episode["podcast"]["title"].replace("/", "_"))
    mkdir_p(podcast_dir)

    # Download the podcast audio file if it hasn't already been downloaded.
    download_path = os.path.join(podcast_dir, filename)
    json_path = download_path + ".json"

    # If the MP3 file already exists, check to see if it's the same episode,
    # or if this podcast isn't using unique filenames.
    #
    # If a podcast has multiple episodes with the same filename in its feed,
    # append the Overcast ID to disambiguate.
    if os.path.exists(download_path):
        cached_metadata = json.load(open(json_path))

        cached_overcast_id = cached_metadata["episode"]["overcast_id"]
        this_overcase_id = episode["episode"]["overcast_id"]

        if cached_overcast_id != this_overcase_id:
            filename = filename.replace(".mp3", "_%s.mp3" % this_overcase_id)
            download_path = os.path.join(podcast_dir, filename)
            json_path = download_path + ".json"

    # Download the MP3 file for the episode, if it hasn't been downloaded already.
    if os.path.exists(download_path):
        print("*** Already downloaded %s, skipping" % audio_url)
    else:
        print("*** Downloading %s" % audio_url)
        try:
            tmp_path, _ = urlretrieve(audio_url)
        except Exception as err:
            print("*** Error downloading audio file: %s" % err)
        else:
            print("*** Download successful!")
            os.rename(tmp_path, download_path)
            filename = None

    # Save a blob of JSON with some episode metadata
    if filename is not None:
        episode["filename"] = filename

    json_string = json.dumps(episode, indent=2, sort_keys=True)

    with open(json_path, "w") as outfile:
        outfile.write(json_string)


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
        download_episode(episode=episode, download_dir=download_dir)

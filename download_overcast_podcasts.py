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
import errno
import logging
import json
import os
import sys
from urllib.parse import urlparse
from urllib.request import build_opener, install_opener, urlretrieve
import xml.etree.ElementTree as ET

import daiquiri


daiquiri.setup(level=logging.INFO)

logger = daiquiri.getLogger(__name__)


def parse_args(argv):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "OPML_PATH",
        help="Path to an OPML file downloaded from https://overcast.fm/account",
    )

    parser.add_argument(
        "--download_dir", default="audiofiles",
        help="directory to save podcast information to to"
    )

    parser.add_argument(
        "--user_agent", default="Python-urllib/%d.%d" % sys.version_info[:2],
        help="""
        user-agent to send in requests.  Some sites return a 403 Error if you try
        to download files with urllib.  You could use (for example) 'Mozilla/5.0',
        which might get files which otherwise fail to download.
        """
    )

    args = parser.parse_args(argv)

    return {
        "opml_path": os.path.abspath(args.OPML_PATH),
        "download_dir": os.path.abspath(args.download_dir),
        "user_agent": args.user_agent,
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


def _escape(s):
    return s.replace(":", "-").replace("/", "-")


def download_episode(episode, download_dir):
    """
    Given a blob of episode data from get_episodes, download the MP3 file and
    save the metadata to ``download_dir``.
    """
    # If the MP3 URL is https://example.net/mypodcast/podcast1.mp3 and the
    # title is "Episode 1: My Great Podcast", the filename is
    # ``Episode 1- My Great Podcast.mp3``.
    audio_url = episode["episode"]["enclosure_url"]
    url_path = urlparse(audio_url).path

    extension = os.path.splitext(url_path)[-1]
    base_name = _escape(episode["episode"]["title"])

    filename = base_name + extension

    # Within the download_dir, put the episodes for each podcast in the
    # same folder.
    podcast_dir = os.path.join(download_dir, _escape(episode["podcast"]["title"]))
    mkdir_p(podcast_dir)

    # Download the podcast audio file if it hasn't already been downloaded.
    download_path = os.path.join(podcast_dir, filename)
    json_path = os.path.join(podcast_dir, base_name + ".json")

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
        logger.debug("Already downloaded %s, skipping", audio_url)
    else:
        logger.info(
            "Downloading %s: %s to %s", episode["podcast"]["title"], audio_url, filename
        )
        try:
            tmp_path, _ = urlretrieve(audio_url)
        except Exception as err:
            logger.error("Error downloading audio file: %s", err)
        else:
            logger.info("Download successful!")
            os.rename(tmp_path, download_path)

    # Save a blob of JSON with some episode metadata
    episode["filename"] = filename

    json_string = json.dumps(episode, indent=2, sort_keys=True)

    with open(json_path, "w") as outfile:
        outfile.write(json_string)


if __name__ == "__main__":
    args = parse_args(argv=sys.argv[1:])

    opml_path = args["opml_path"]
    download_dir = args["download_dir"]

    # Some sites block the default urllib User-Agent headers, so we can customise
    # it to something else if necessary.
    opener = build_opener()
    opener.addheaders = [("User-agent", args["user_agent"])]
    install_opener(opener)

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

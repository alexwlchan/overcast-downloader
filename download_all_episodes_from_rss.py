#!/usr/bin/env python
"""
The main downloader script will also get a copy of the RSS feed.

If there are episodes in the RSS feeds that you haven't listened to in Overcast,
but you still want in your podcast archive (for example, if you listened to them
in a different podcast app), you can use this script to download them all.
"""

import glob
import html
import os
import sys

from lxml import etree
import smartypants

from download_overcast_podcasts import download_url, get_filename, logger


def download_files_for_xml(xml_path):
    logger.info("Inspecting %r", xml_path)
    tree = etree.parse(xml_path)

    download_dir = os.path.dirname(xml_path)

    for item in tree.xpath(".//item"):
        title = item.find("title").text
        logger.debug("Checking episode %r", title)

        audio_url = item.find("enclosure").attrib["url"]

        filename = get_filename(
            download_url=audio_url,
            # We have to replicate some of the processing done by Overcast's
            # title cleanups.
            title=html.unescape(smartypants.smartypants(title)),
        )
        download_path = os.path.join(download_dir, filename)

        if os.path.exists(download_path):
            logger.debug("This episode is already downloaded, skipping")
            continue

        logger.info("Downloading episode %r", title)

        download_url(url=audio_url, path=download_path, description="audio file")


if __name__ == "__main__":
    try:
        audiofile_dir = sys.argv[1]
    except IndexError:
        sys.exit(f"{__file__} <AUDIOFILE_DIR>")

    for xml_path in glob.iglob(os.path.join(audiofile_dir, "feed.*.xml")):
        download_files_for_xml(xml_path)

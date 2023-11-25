#!/usr/bin/env bash

set -o errexit
set -o nounset

python3 download_overcast_podcasts.py ~/Desktop/overcast.opml --download_dir "/Volumes/Media (Sapphire)/backups/overcast/audiofiles"
# mv ~/Desktop/overcast.opml "/Volumes/Media (Sapphire)/backups/overcast/overcast.$(date +'%Y-%m-%d').xml"

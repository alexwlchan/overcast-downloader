import os
import sys
import uuid

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed
import urllib3.exceptions


@retry(
    retry=(
        retry_if_exception_type(httpx.HTTPError)
        | retry_if_exception_type(urllib3.exceptions.HTTPError)
    ),
    stop=stop_after_attempt(10),
    wait=wait_fixed(60),
)
def download_file(*, url, path, client=None):
    """
    Atomically download a file from ``url`` to ``path``.

    If ``path`` already exists, the file will not be downloaded again.
    This means that different URLs should be saved to different paths.

    This function is meant to be used in cases where the contents of ``url``
    is immutable -- calling it more than once should always return the same bytes.

    Returns the download path.

    """
    # If the URL has already been downloaded, we can skip downloading it again.
    if os.path.exists(path):
        return path

    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)

    if client is None:
        client = httpx.Client(follow_redirects=True)

    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()

            # Download to a temporary path first.  That way, we only get
            # something at the destination path if the download is successful.
            #
            # We download to a path in the same directory so we can do an
            # atomic ``os.rename()`` later -- atomic renames don't work
            # across filesystem boundaries.
            tmp_path = f"{path}.{uuid.uuid4()}.tmp"

            with open(tmp_path, "wb") as out_file:
                for chunk in resp.iter_raw():
                    out_file.write(chunk)

    # If something goes wrong, it will probably be retried by tenacity.
    # Log the exception in case a programming bug has been introduced in
    # the ``try`` block or there's a persistent error.
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise

    os.rename(tmp_path, path)
    return path

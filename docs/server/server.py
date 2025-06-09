import json
import time
import logging

from argparse import ArgumentParser
from pathlib import Path

import requests

from flask import Flask, Response, request, redirect
from rich_argparse import RichHelpFormatter

from keydive.utils import configure_logging

# Suppress urllib3 warnings
logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)

# Initialize Flask application
app = Flask(__name__)

# Define paths, constants, and global flags
PARENT = Path(__file__).parent
VERSION = '1.0.2'
KEYBOX = False
DELAY = 10


@app.route('/', methods=['GET'])
def health_check() -> Response:
    """
    Health check endpoint to confirm the server is running.

    Returns:
        Response: A simple 'pong' message with a 200 OK status.
    """
    return Response(response='pong', status=200, content_type='text/html; charset=utf-8')


@app.route('/shaka-demo-assets/angel-one-widevine/<path:file>', methods=['GET'])
def shaka_demo_assets(file) -> Response:
    """
    Serves cached assets for Widevine demo content. If the requested file is
    not available locally, it fetches it from a remote server and caches it.

    Args:
        file (str): File path requested by the client.

    Returns:
        Response: File content as a byte stream, or a 404 error if not found.
    """
    logger = logging.getLogger('Shaka')
    logger.info('%s %s', request.method, request.path)

    try:
        path = PARENT / '.assets' / file
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.is_file():
            # Serve cached file content if available
            content = path.read_bytes()
        else:
            # Fetch the file from remote storage if not cached locally
            r = requests.get(
                url=f'https://storage.googleapis.com/shaka-demo-assets/angel-one-widevine/{file}',
                headers={
                    'Accept': '*/*',
                    'User-Agent': 'KalturaDeviceInfo/1.4.1 (Linux;Android 10) ExoPlayerLib/2.9.3'
                }
            )
            r.raise_for_status()
            path.write_bytes(r.content)  # Cache the downloaded content
            content = r.content
            logger.debug('Downloaded assets: %s', path)

        return Response(response=content, status=200, content_type='application/octet-stream')
    except Exception as e:
        return Response(response=str(e), status=404, content_type='text/html; charset=utf-8')


@app.route('/certificateprovisioning/v1/devicecertificates/create', methods=['POST'])
def certificate_provisioning() -> Response:
    """
    Handles device certificate provisioning requests by intercepting the request,
    saving it as a curl command, and then responding based on cached data
    or redirecting if no cached response is available.

    Returns:
        Response: JSON response if provisioning is complete, else a redirection.
    """
    global KEYBOX, DELAY
    logger = logging.getLogger('Google')
    logger.info('%s %s', request.method, request.path)

    if KEYBOX:
        logger.warning('Provisioning request aborted to prevent keybox spam')
        return Response(response='Internal Server Error', status=500, content_type='text/html; charset=utf-8')

    # Generate a curl command from the incoming request for debugging or testing
    user_agent = request.headers.get('User-Agent', 'Unknown')
    url = request.url.replace('http://', 'https://')
    prompt = [
        'curl',
        '--request', 'POST',
        '--compressed',
        '--header', '"Accept-Encoding: gzip"',
        '--header', '"Connection: Keep-Alive"',
        '--header', '"Content-Type: application/x-www-form-urlencoded"',
        '--header', '"Host: www.googleapis.com"',
        '--header', f'"User-Agent: {user_agent}"'
    ]

    # Save the curl command for potential replay or inspection
    curl = PARENT / 'curl.txt'
    curl.write_text(' \\\n  '.join(prompt))
    logger.debug('Saved curl command to: %s', curl)

    # Wait for provisioning response data with retries
    logger.warning('Waiting for provisioning response...')
    provision = PARENT / 'provisioning.json'
    provision.unlink(missing_ok=True)
    provision.write_bytes(b'')  # Create empty file for manual input if needed

    # Poll for the presence of a response up to DELAY times with 1-second intervals
    for _ in range(DELAY):
        try:
            content = json.loads(provision.read_bytes())
            if content:
                # Cleanup after successful response
                curl.unlink(missing_ok=True)
                provision.unlink(missing_ok=True)
                return Response(response=content, status=200, content_type='application/json')
        except Exception as e:
            pass  # Continue waiting if file is empty or not yet ready
        time.sleep(1)

    # Redirect to the secure URL if response is not available
    logger.warning('Redirecting to avoid timeout')
    return redirect(url, code=302)


def main() -> None:
    """
    Main entry point for the application. Parses command-line arguments
    to set global parameters and configures logging, then starts the Flask server.
    """
    global VERSION, DELAY, KEYBOX
    parser = ArgumentParser(
        description='Local DRM provisioning video player.',
        formatter_class=RichHelpFormatter)

    global_group = parser.add_argument_group('Global Options')
    global_group.add_argument('--host', type=str, default='127.0.0.1', metavar='<host>', help='Host address for the server to bind to.')
    global_group.add_argument('--port', type=int, default=9090, metavar='<port>', help='Port number for the server to listen on.')
    global_group.add_argument('-v', '--verbose', action='store_true', help='Enable detailed logging for debugging.')
    global_group.add_argument('-l', '--log', type=Path, metavar='<dir>', help='Directory to save log files.')
    global_group.add_argument('--version', action='store_true', help='Show tool version and exit.')

    advanced_group = parser.add_argument_group('Advanced Options')
    advanced_group.add_argument('-d', '--delay', type=int, metavar='<delay>', default=10, help='Delay (in seconds) between successive checks for provisioning responses.')
    advanced_group.add_argument('-k', '--keybox', action='store_true', help='Enable keybox mode, which aborts provisioning requests to prevent spam.')

    args = parser.parse_args()

    # Handle version flag early and exit
    if args.version:
        print(f'Server {VERSION}')
        return

    # Set up logging (to file if specified, otherwise stdout)
    log_path = configure_logging(path=args.log, verbose=args.verbose)
    logger = logging.getLogger('Server')
    logger.info('Version: %s', VERSION)

    try:
        # Set global variables based on parsed arguments
        DELAY = args.delay
        KEYBOX = args.keybox

        # Start Flask app with specified host, port, and debug mode
        logging.getLogger('werkzeug').setLevel(logging.INFO if args.verbose else logging.ERROR)
        app.run(host=args.host, port=args.port, debug=False)
    except KeyboardInterrupt:
        # Graceful exit on user interrupt (Ctrl+C)
        pass
    except Exception as e:
        logger.critical(e, exc_info=args.verbose)

    # Log the path to the log file if it was created
    if log_path:
        logger.info('Log file: %s' % log_path)

    logger.info('Exiting')


if __name__ == '__main__':
    main()

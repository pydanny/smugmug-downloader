#!/usr/bin/env python3
"""SmugMug file downloader - downloads all files from a user's account."""

from os import getenv
from pathlib import Path
from urllib.parse import urljoin
import typer

import requests
from requests_oauthlib import OAuth1Session
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

console = Console()

app = typer.Typer()

# API credentials

API_KEY = getenv('SMUGMUG_API_KEY')
API_SECRET = getenv('SMUGMUG_API_SECRET') 

if API_KEY is None or API_SECRET is None:
    console.print('[bold red]ERROR!!![/bold red]')
    console.print('[bold red]Set SMUGMUG_API_KEY and SMUGMUG_API_SECRET env variables[/bold red]')
    raise typer.Abort

# OAuth endpoints
REQUEST_TOKEN_URL = 'https://api.smugmug.com/services/oauth/1.0a/getRequestToken'
AUTHORIZATION_URL = 'https://api.smugmug.com/services/oauth/1.0a/authorize'
ACCESS_TOKEN_URL = 'https://api.smugmug.com/services/oauth/1.0a/getAccessToken'

API_BASE = 'https://api.smugmug.com'
TOKEN_FILE = Path.home() / '.smugmug_tokens'


def load_tokens() -> tuple[str, str] | None:
    """Load saved OAuth tokens from file."""
    if TOKEN_FILE.exists():
        content = TOKEN_FILE.read_text().strip().split('\n')
        if len(content) == 2:
            return content[0], content[1]
    return None


def save_tokens(access_token: str, access_token_secret: str):
    """Save OAuth tokens to file."""
    TOKEN_FILE.write_text(f"{access_token}\n{access_token_secret}")
    TOKEN_FILE.chmod(0o600)


def authenticate() -> OAuth1Session:
    """Authenticate with SmugMug using OAuth1.0a."""
    tokens = load_tokens()

    if tokens:
        access_token, access_token_secret = tokens
        session = OAuth1Session(
            API_KEY,
            client_secret=API_SECRET,
            resource_owner_key=access_token,
            resource_owner_secret=access_token_secret
        )
        # Verify tokens are still valid
        response = session.get(f'{API_BASE}/api/v2!authuser', headers={'Accept': 'application/json'})
        if response.status_code == 200:
            console.print("[green]Using saved authentication tokens[/green]")
            return session
        console.print("[yellow]Saved tokens expired, re-authenticating...[/yellow]")

    # Need to do OAuth flow
    console.print("[bold]SmugMug Authentication Required[/bold]")

    # Get request token
    oauth = OAuth1Session(API_KEY, client_secret=API_SECRET, callback_uri='oob')
    fetch_response = oauth.fetch_request_token(REQUEST_TOKEN_URL)
    resource_owner_key = fetch_response.get('oauth_token')
    resource_owner_secret = fetch_response.get('oauth_token_secret')

    # Get authorization
    authorization_url = oauth.authorization_url(AUTHORIZATION_URL, Access='Full', Permissions='Read')
    console.print(f"\n[bold]Please visit this URL to authorize:[/bold]\n{authorization_url}\n")
    verifier = console.input("[bold]Enter the 6-digit PIN: [/bold]")

    # Get access token
    oauth = OAuth1Session(
        API_KEY,
        client_secret=API_SECRET,
        resource_owner_key=resource_owner_key,
        resource_owner_secret=resource_owner_secret,
        verifier=verifier
    )
    oauth_tokens = oauth.fetch_access_token(ACCESS_TOKEN_URL)
    access_token = oauth_tokens['oauth_token']
    access_token_secret = oauth_tokens['oauth_token_secret']

    save_tokens(access_token, access_token_secret)
    console.print("[green]Authentication successful! Tokens saved.[/green]")

    return OAuth1Session(
        API_KEY,
        client_secret=API_SECRET,
        resource_owner_key=access_token,
        resource_owner_secret=access_token_secret
    )


class SmugMugDownloader:
    """Downloads all files from a SmugMug user."""

    def __init__(self, session: OAuth1Session, output_dir: str = "downloads"):
        self.session = session
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {'Accept': 'application/json'}

    def api_get(self, url: str, params: dict = None) -> dict:
        """Make an authenticated GET request to the API."""
        if not url.startswith('http'):
            url = urljoin(API_BASE, url)
        response = self.session.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        return response.json()

    def get_user(self, username: str) -> dict:
        """Get user information."""
        data = self.api_get(f'/api/v2/user/{username}')
        return data['Response']['User']

    def get_node_children(self, node_uri: str) -> list:
        """Get all children of a node (folders and albums), handling pagination."""
        children = []
        params = {'count': 100}

        while True:
            data = self.api_get(f"{node_uri}!children", params=params)
            response = data.get('Response', {})

            nodes = response.get('Node', [])
            if isinstance(nodes, dict):
                nodes = [nodes]
            children.extend(nodes)

            # Check for more pages
            pages = response.get('Pages', {})
            next_page = pages.get('NextPage')
            if not next_page:
                break
            params['start'] = params.get('start', 0) + params['count']

        return children

    def get_album_images(self, album_uri: str) -> list:
        """Get all images in an album, handling pagination."""
        images = []
        params = {'count': 100}

        while True:
            try:
                data = self.api_get(f"{album_uri}!images", params=params)
            except requests.HTTPError as e:
                if e.response.status_code == 404:
                    break
                raise

            response = data.get('Response', {})
            album_images = response.get('AlbumImage', [])
            if isinstance(album_images, dict):
                album_images = [album_images]
            images.extend(album_images)

            # Check for more pages
            pages = response.get('Pages', {})
            next_page = pages.get('NextPage')
            if not next_page:
                break
            params['start'] = params.get('start', 0) + params['count']

        return images

    def get_image_download_url(self, image: dict) -> str | None:
        """Get the best download URL for an image."""
        # Try to get the ImageSizeDetails for original/largest size
        uris = image.get('Uris', {})

        # Check for ImageSizeDetails
        size_details_uri = uris.get('ImageSizeDetails', {}).get('Uri')
        if size_details_uri:
            try:
                data = self.api_get(size_details_uri)
                sizes = data.get('Response', {}).get('ImageSizeDetails', {})

                # Prefer original, then largest available
                for size_key in ['ImageSizeOriginal', 'ImageSizeX5Large', 'ImageSizeX4Large',
                                 'ImageSizeX3Large', 'ImageSizeX2Large', 'ImageSizeXLarge', 'ImageSizeLarge']:
                    size_info = sizes.get(size_key, {})
                    if size_info.get('Url'):
                        return size_info['Url']
            except Exception:
                pass

        # Fallback: try ArchivedUri directly from image
        if image.get('ArchivedUri'):
            return image['ArchivedUri']

        # Try LargestImage uri
        largest_uri = uris.get('LargestImage', {}).get('Uri')
        if largest_uri:
            try:
                data = self.api_get(largest_uri)
                largest = data.get('Response', {}).get('LargestImage', {})
                if largest.get('Url'):
                    return largest['Url']
            except Exception:
                pass

        return None

    def download_file(self, url: str, filepath: Path) -> bool:
        """Download a file from URL to filepath."""
        if filepath.exists():
            console.print(f"  [dim]Skipping (exists): {filepath.name}[/dim]")
            return False

        try:
            # Use OAuth session for authenticated downloads
            response = self.session.get(url, stream=True)
            response.raise_for_status()

            filepath.parent.mkdir(parents=True, exist_ok=True)

            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except Exception as e:
            console.print(f"  [red]Error downloading {filepath.name}: {e}[/red]")
            return False

    def traverse_and_download(self, node_uri: str, current_path: Path, progress: Progress, task_id):
        """Recursively traverse nodes and download all images."""
        children = self.get_node_children(node_uri)

        for child in children:
            node_type = child.get('Type', '')
            name = child.get('Name', 'Unknown')
            url_name = child.get('UrlName', name)

            # Sanitize name for filesystem
            safe_name = "".join(c for c in url_name if c.isalnum() or c in ' -_').strip()
            if not safe_name:
                safe_name = 'unnamed'

            child_path = current_path / safe_name

            if node_type == 'Folder':
                progress.update(task_id, description=f"[cyan]Scanning folder: {name}[/cyan]")
                child_uri = child.get('Uris', {}).get('Node', {}).get('Uri')
                if child_uri:
                    self.traverse_and_download(child_uri, child_path, progress, task_id)

            elif node_type == 'Album':
                progress.update(task_id, description=f"[cyan]Processing album: {name}[/cyan]")
                album_uri = child.get('Uris', {}).get('Album', {}).get('Uri')
                if album_uri:
                    self.download_album(album_uri, child_path, name, progress)

    def download_album(self, album_uri: str, album_path: Path, album_name: str, progress: Progress):
        """Download all images in an album."""
        images = self.get_album_images(album_uri)

        if not images:
            console.print(f"  [dim]Album '{album_name}' is empty[/dim]")
            return

        console.print(f"\n[bold]Album: {album_name}[/bold] ({len(images)} files)")
        album_path.mkdir(parents=True, exist_ok=True)

        download_task = progress.add_task(f"[green]Downloading {album_name}...", total=len(images))

        downloaded = 0
        skipped = 0
        failed = 0

        for image in images:
            filename = image.get('FileName', f"image_{image.get('ImageKey', 'unknown')}")
            filepath = album_path / filename

            download_url = self.get_image_download_url(image)
            if download_url:
                if self.download_file(download_url, filepath):
                    downloaded += 1
                else:
                    skipped += 1
            else:
                console.print(f"  [yellow]No download URL for: {filename}[/yellow]")
                failed += 1

            progress.update(download_task, advance=1)

        progress.remove_task(download_task)
        console.print(f"  [green]Downloaded: {downloaded}[/green], [dim]Skipped: {skipped}[/dim], [red]Failed: {failed}[/red]")

    def download_user(self, username: str):
        """Download all files from a user."""
        console.print(f"\n[bold blue]SmugMug Downloader[/bold blue]")
        console.print(f"Downloading files for user: [bold]{username}[/bold]\n")

        try:
            user = self.get_user(username)
        except Exception as e:
            console.print(f"[red]Error: Could not find user '{username}': {e}[/red]")
            return

        display_name = user.get('Name', username)
        console.print(f"User found: [green]{display_name}[/green]")

        # Get the user's root node
        node_uri = user.get('Uris', {}).get('Node', {}).get('Uri')
        if not node_uri:
            console.print("[red]Error: Could not find user's root node[/red]")
            return

        user_dir = self.output_dir / username

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Scanning...", total=None)
            self.traverse_and_download(node_uri, user_dir, progress, task)
            progress.remove_task(task)

        console.print(f"\n[bold green]Download complete![/bold green]")
        console.print(f"Files saved to: {user_dir.absolute()}")





@app.command()
def main(
    username: str = typer.Argument(help="SmugMug username to download from"),
    output_dir: str = typer.Option("downloads", "--output", "-o", help="Output directory for downloads"),
):
    """Download all files from a SmugMug user's account."""
    session = authenticate()
    downloader = SmugMugDownloader(session, output_dir)
    downloader.download_user(username)


if __name__ == "__main__":
    app()


duff = """
  Usage

  # Install dependencies
  uv sync

  # Download all files from a user
  uv run python main.py <username>

  # Specify custom output directory
  uv run python main.py <username> /path/to/downloads

  On first run, you'll be prompted to:
  1. Visit an authorization URL
  2. Enter a 6-digit PIN

  After that, tokens are saved and reused automatically.
"""
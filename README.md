# SmugMug Downloader

A command-line tool to download all photos from a SmugMug account.

## Features

- Downloads all images at their highest available quality
- Preserves folder and album structure locally
- Skips already-downloaded files
- OAuth 1.0a authentication with token caching

## Prerequisites

1. **Python 3.14+** and **uv** package manager
2. **SmugMug API credentials** - Create an API key at https://api.smugmug.com/api/developer/apply

Set your credentials as environment variables:

```bash
export SMUGMUG_API_KEY="your-api-key"
export SMUGMUG_API_SECRET="your-api-secret"
```

## Installation

```bash
uv sync
```

## Usage

```bash
# Download all files from a user's account
uv run python main.py <username>

# Specify a custom output directory
uv run python main.py <username> --output /path/to/downloads
```

The default output directory is `downloads/`.

## Authentication

On first run, the tool will:

1. Display an authorization URL
2. Prompt you to visit the URL and authorize access
3. Ask for the 6-digit PIN from SmugMug

Tokens are cached in `~/.smugmug_tokens` for subsequent runs.

## License

MIT

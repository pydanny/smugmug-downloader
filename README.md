# SmugMug Downloader

A command-line tool to download all photos from a SmugMug account.

## Features

- Downloads all images at their highest available quality
- **Parallel downloads** - Download multiple files simultaneously for faster transfers
- Preserves folder and album structure locally
- Skips already-downloaded files with intelligent caching
- OAuth 1.0a authentication with token caching
- Configurable worker threads for optimal performance

## Prerequisites

1. **Python 3.14+** and **uv** package manager
2. **SmugMug API credentials** - Create an API key at https://api.smugmug.com/api/developer/apply

Set your credentials as environment variables:

```bash
export SMUGMUG_API_KEY="your-api-key"
export SMUGMUG_API_SECRET="your-api-secret"
```

## Installation

Using `uv`, add the project:

```bash
uv add smugmug-downloader
```

## Usage

```bash
# Download all files from a user's account
smugmug-downloader <username>

# Specify a custom output directory
smugmug-downloader <username> --output /path/to/downloads

# Use more parallel workers for faster downloads (default: 10)
smugmug-downloader <username> --workers 20

# Conservative mode for slower connections
smugmug-downloader <username> --workers 5
```

The default output directory is `downloads/`.

### Performance Tuning

The `--workers` (or `-w`) option controls how many files are downloaded simultaneously:

- **Default (10)**: Good balance for most connections
- **Fast connections (15-20)**: Maximize throughput on high-speed internet
- **Slow/unstable (3-5)**: Reduce load on limited bandwidth
- **API rate limits**: If you encounter rate limiting, reduce workers

Example for optimal performance:
```bash
smugmug-downloader alpixpunctro -o ./backup -w 20
```

## Authentication

On first run, the tool will:

1. Display an authorization URL
2. Prompt you to visit the URL and authorize access
3. Ask for the 6-digit PIN from SmugMug

Tokens are cached in `~/.smugmug_tokens` for subsequent runs.

## License

MIT

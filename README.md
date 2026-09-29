# Ranobes Scraper — Web Novel Scraping Engine & Desktop Suite

A high-performance web novel scraping and content parsing suite equipped with anti-bot bypass mechanisms, DOM normalization, and dual interaction modes: a high-throughput CLI engine and an interactive Desktop GUI.

## System Architecture

### 1. High-Throughput Engine (`/`)
- **`ranobes_scraper_v8.py`**: Asynchronous/multithreaded core pipeline handling chapter pagination, DOM sanitization, and structured serialization.
- **`cf_proxy.py`**: Anti-detection proxy layer bypassing Cloudflare protection and rate limits.
- **`run.bat` / `run_proxy.bat`**: Automated runtime launch scripts for background proxy routing.

### 2. Desktop GUI Application (`gui/`)
- **`ranobes_gui.py`**: Graphical user interface enabling single-click novel scraping, progress visualizers, and batch exports.
- **`ranobes_gui.spec`**: PyInstaller standalone executable bundling specification for zero-dependency Windows distribution.
- **`build_exe.bat`**: Automated one-click packaging pipeline compiling the GUI into a standalone Windows binary.

## License
MIT License

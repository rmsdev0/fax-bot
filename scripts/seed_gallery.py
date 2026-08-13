"""Publish the built-in fictional house samples to the configured gallery.

Usage: uv run python -m scripts.seed_gallery
"""

from app.config import get_settings
from app.db import init_db
from app.gallery_seeds import seed_gallery_samples


def main() -> None:
    init_db()
    count = seed_gallery_samples(get_settings())
    print(f"published or repaired {count} house gallery sample(s)")


if __name__ == "__main__":
    main()

"""PyInstaller entry point for the bundled `revenant` executable."""
import sys

from revenant_cli.cli import main

if __name__ == "__main__":
    sys.exit(main())

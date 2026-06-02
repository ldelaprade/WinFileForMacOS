try:
    from .file_explorer import run
except ImportError:
    # PyInstaller may execute this as a top-level script where package-relative
    # imports are unavailable.
    from src.file_explorer import run


if __name__ == "__main__":
    run()

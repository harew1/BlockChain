"""
export_zip.py — Creates a clean ZIP of the platform (excludes .env, __pycache__, .db).
Usage: python export_zip.py
"""

import os
import zipfile
import datetime

EXCLUDE = {
    ".env", "*.db", "__pycache__", ".pytest_cache",
    "*.pyc", "*.pyo", ".DS_Store", "trading.db",
    "test_trading.db", "*.log", "models/*.joblib",
    "models/sample_buffer.json",
}

def _should_exclude(path: str) -> bool:
    name = os.path.basename(path)
    for pattern in EXCLUDE:
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return True
        elif name == pattern or "__pycache__" in path:
            return True
    return False


def export():
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"cryptotrader_pro_{ts}.zip"
    base     = os.path.dirname(os.path.abspath(__file__))

    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(base):
            # Skip hidden + excluded dirs
            dirs[:] = [d for d in dirs
                       if not d.startswith(".") and d != "__pycache__"
                       and d != ".pytest_cache"]
            for file in files:
                full = os.path.join(root, file)
                rel  = os.path.relpath(full, base)
                if not _should_exclude(full):
                    zf.write(full, os.path.join("cryptotrader_pro", rel))
                    print(f"  + {rel}")

    size_kb = os.path.getsize(zip_name) // 1024
    print(f"\n✅ Exported: {zip_name} ({size_kb} KB)")
    return zip_name


if __name__ == "__main__":
    export()

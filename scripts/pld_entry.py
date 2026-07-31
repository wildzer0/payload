"""
Entry point per PyInstaller: a differenza di 'pip install', PyInstaller
ha bisogno di uno script Python reale da analizzare e congelare — non
basta l'entry_point 'pld = payload.cli:app' dichiarato in pyproject.toml,
quello è un meccanismo di pip/setuptools, non di PyInstaller.

Uso: pyinstaller --onefile --name pld --copy-metadata payload scripts/pld_entry.py
"""
from payload.cli import app

if __name__ == "__main__":
    app()

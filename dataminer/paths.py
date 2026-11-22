"""Dónde vive cada cosa, tanto desde el código fuente como desde el ejecutable empaquetado."""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))          # True dentro del .exe de PyInstaller
ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """Carpeta de datos: DATAMINER_HOME si está definida; junto al código en desarrollo;
    y en la carpeta del usuario cuando se ejecuta el .exe (su propia carpeta puede ser de solo lectura)."""
    env = os.environ.get("DATAMINER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / "DataMiner" if FROZEN else ROOT


DATA = _data_dir()
OUT = DATA / "output"


def self_cmd() -> list[str]:
    """Orden para relanzar DataMiner como proceso hijo (cada búsqueda corre en el suyo)."""
    return [sys.executable] if FROZEN else [sys.executable, "-m", "dataminer"]


def workdir() -> str:
    """Directorio de trabajo de los procesos hijo. Desde el código fuente debe ser la raíz del proyecto,
    porque `python -m dataminer` busca ahí el paquete; el .exe no lo necesita y usa la carpeta de datos."""
    DATA.mkdir(parents=True, exist_ok=True)
    return str(DATA if FROZEN else ROOT)

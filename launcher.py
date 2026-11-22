"""Punto de entrada del ejecutable: sin argumentos abre la aplicación de escritorio; con ellos, es la CLI."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()   # los procesos de extracción del .exe arrancan por aquí
    from dataminer.cli import main

    if len(sys.argv) == 1:
        sys.argv.append("app")
    main()

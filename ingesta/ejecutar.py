"""Lanza las fuentes definidas en ingesta/config/fuentes.yml.

Ejemplos (desde la raíz del repo):
    python -m ingesta.ejecutar incibe_avisos --max-paginas 2
    python -m ingesta.ejecutar incibe_avisos --completa
    python -m ingesta.ejecutar --todas
"""
import argparse
import importlib
import pkgutil

import yaml

from ingesta import scrapers
from ingesta.scrapers.base import RAIZ, REGISTRO


def cargar_plugins():
    """Importa todos los módulos de scrapers/ para que se registren solos."""
    for m in pkgutil.iter_modules(scrapers.__path__):
        importlib.import_module(f"ingesta.scrapers.{m.name}")


def main():
    cargar_plugins()
    ap = argparse.ArgumentParser(description="Ingesta de fuentes")
    ap.add_argument("fuente", nargs="?", help=f"una de: {', '.join(sorted(REGISTRO))}")
    ap.add_argument("--todas", action="store_true", help="ejecuta todas las fuentes activas")
    ap.add_argument("--max-paginas", type=int, help="limita las páginas del listado (pruebas)")
    ap.add_argument("--completa", action="store_true", help="no parar al encontrar páginas ya conocidas")
    ap.add_argument("--refrescar", action="store_true", help="volver a descargar también lo ya guardado")
    args = ap.parse_args()

    config = yaml.safe_load((RAIZ / "ingesta" / "config" / "fuentes.yml").read_text(encoding="utf-8"))
    nombres = [n for n, c in config.items() if c.get("activa")] if args.todas else [args.fuente]
    if not nombres or nombres == [None]:
        ap.error("indica una fuente o usa --todas")
    for nombre in nombres:
        if nombre not in REGISTRO or nombre not in config:
            ap.error(f"fuente desconocida: {nombre}")
        REGISTRO[nombre](config[nombre]).ejecutar(
            max_paginas=args.max_paginas, completa=args.completa, refrescar=args.refrescar)


if __name__ == "__main__":
    main()
"""Interfaz común de las fuentes de ingesta.

Cada fuente es una clase que hereda de `Fuente` y se registra con `@registrar`.
Añadir una fuente nueva = crear un fichero en `ingesta/scrapers/` y una entrada
en `ingesta/config/fuentes.yml`. No hay que tocar nada más.
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RAIZ = Path(__file__).resolve().parents[2]  # raíz del repositorio
REGISTRO = {}  # nombre de fuente -> clase


def registrar(clase):
    """Decorador: da de alta una fuente por su atributo `nombre`."""
    REGISTRO[clase.nombre] = clase
    return clase


def configurar_log():
    """Log de texto con formato estable (lo leerá Logstash con grok)."""
    log = logging.getLogger("scraping")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                datefmt="%Y-%m-%dT%H:%M:%S")
    carpeta = RAIZ / "data" / "logs"
    carpeta.mkdir(parents=True, exist_ok=True)
    for h in (logging.FileHandler(carpeta / "scraping.log", encoding="utf-8"),
              logging.StreamHandler()):
        h.setFormatter(formato)
        log.addHandler(h)
    return log


def parsear_robots(texto):
    """RobotFileParser de Python corta el grupo en cada línea en blanco, y el
    robots.txt de INCIBE tiene reglas tras una línea en blanco. Las quitamos
    para que se apliquen todas."""
    rp = RobotFileParser()
    rp.parse([l for l in texto.splitlines() if l.strip()])
    return rp


def calcular_hash(registro):
    """Hash del contenido (sin campos de control) para detectar cambios."""
    datos = {k: v for k, v in registro.items() if k not in ("scraped_at", "hash")}
    texto = json.dumps(datos, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class Fuente:
    nombre = ""

    def __init__(self, config):
        self.cfg = config
        self.pausa = float(config.get("pausa_segundos", 2.5))
        self.timeout = float(config.get("timeout_segundos", 20))
        self.max_errores_seguidos = int(config.get("max_errores_seguidos", 5))
        self.session = self._crear_sesion(config["user_agent"])
        self.log = configurar_log()
        self.robots = None
        self._ultima_peticion = 0.0
        self.dir_raw = RAIZ / "data" / "raw" / self.nombre
        self.salida = RAIZ / "data" / "clean" / f"{self.nombre}.jsonl"
        self.stats = {"peticiones": 0, "nuevos": 0, "sin_cambios": 0, "errores": 0}

    # ---- a implementar en cada fuente -------------------------------------
    def listar(self, max_paginas=None):
        """Generador: devuelve una lista de URLs de detalle por página."""
        raise NotImplementedError

    def parsear(self, html, url):
        """Devuelve un dict con los campos comunes (sin hash ni scraped_at)."""
        raise NotImplementedError

    # ---- red: sesión, robots.txt, pausa, reintentos y caché ---------------
    def _crear_sesion(self, user_agent):
        reintentos = Retry(total=3, backoff_factor=2,
                           status_forcelist=[429, 500, 502, 503, 504],
                           allowed_methods=["GET"], respect_retry_after_header=True)
        s = requests.Session()
        s.headers["User-Agent"] = user_agent
        s.mount("https://", HTTPAdapter(max_retries=reintentos))
        s.mount("http://", HTTPAdapter(max_retries=reintentos))
        return s

    def _cargar_robots(self, url):
        p = urlparse(url)
        r = self.session.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=self.timeout)
        r.raise_for_status()
        self.robots = parsear_robots(r.text)

    def permitido(self, url):
        return self.robots.can_fetch(self.session.headers["User-Agent"], url)

    def descargar(self, url, cache=False):
        """Descarga una URL respetando robots.txt y la pausa entre peticiones."""
        ruta = self.dir_raw / (hashlib.sha1(url.encode()).hexdigest() + ".html")
        if cache and ruta.exists():
            return ruta.read_text(encoding="utf-8")
        if not self.permitido(url):
            raise PermissionError(f"robots.txt no permite {url}")
        espera = self.pausa - (time.monotonic() - self._ultima_peticion)
        if espera > 0:
            time.sleep(espera)
        r = self.session.get(url, timeout=self.timeout)
        self._ultima_peticion = time.monotonic()
        self.stats["peticiones"] += 1
        r.raise_for_status()
        r.encoding = "utf-8"
        self.dir_raw.mkdir(parents=True, exist_ok=True)
        ruta.write_text(r.text, encoding="utf-8")
        return r.text

    # ---- salida incremental ------------------------------------------------
    def _cargar_vistos(self):
        """url -> hash de lo que ya está en el JSONL (la última versión gana)."""
        vistos = {}
        if self.salida.exists():
            with open(self.salida, encoding="utf-8") as f:
                for linea in f:
                    if linea.strip():
                        r = json.loads(linea)
                        vistos[r["url"]] = r["hash"]
        return vistos

    def ejecutar(self, max_paginas=None, completa=False, refrescar=False):
        """Recorre la fuente. Por defecto es incremental: solo guarda lo nuevo
        o modificado y para en cuanto una página entera ya es conocida."""
        inicio = time.monotonic()
        self.salida.parent.mkdir(parents=True, exist_ok=True)
        vistos = self._cargar_vistos()
        paginas, seguidos = 0, 0
        try:
            self._cargar_robots(self.cfg["url_base"])
            with open(self.salida, "a", encoding="utf-8") as out:
                for urls in self.listar(max_paginas):
                    paginas += 1
                    conocidas = 0
                    for url in urls:
                        if url in vistos and not refrescar:
                            conocidas += 1
                            self.stats["sin_cambios"] += 1
                            continue
                        try:
                            html = self.descargar(url, cache=False)
                            reg = self.parsear(html, url)
                            reg["fuente"] = self.nombre
                            reg["url"] = url
                            reg["hash"] = calcular_hash(reg)
                            reg["scraped_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                            seguidos = 0
                            if vistos.get(url) == reg["hash"]:
                                self.stats["sin_cambios"] += 1
                                continue
                            out.write(json.dumps(reg, ensure_ascii=False) + "\n")
                            out.flush()
                            vistos[url] = reg["hash"]
                            self.stats["nuevos"] += 1
                        except Exception as e:  # un fallo no para la ejecución
                            self.stats["errores"] += 1
                            seguidos += 1
                            self.log.error("fuente=%s url=%s error=%r", self.nombre, url, e)
                            if seguidos >= self.max_errores_seguidos:
                                raise RuntimeError("demasiados errores seguidos") from e
                    if urls and conocidas == len(urls) and not completa and not refrescar:
                        break  # modo incremental: ya tenemos todo lo siguiente
        finally:
            self.log.info("fuente=%s paginas=%d items_nuevos=%d sin_cambios=%d errores=%d "
                          "peticiones=%d duracion=%.1fs", self.nombre, paginas,
                          self.stats["nuevos"], self.stats["sin_cambios"],
                          self.stats["errores"], self.stats["peticiones"],
                          time.monotonic() - inicio)
        return self.stats
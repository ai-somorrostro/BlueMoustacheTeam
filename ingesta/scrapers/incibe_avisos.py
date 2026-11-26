"""Scraper de los avisos de INCIBE-CERT (listado paginado + página de detalle)."""
import re
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from ingesta.scrapers.base import Fuente, registrar

SOLO_SIMBOLOS = {"", "+", "-"}  # botones de la lista de etiquetas


def _limpiar(texto):
    """Quita espacios sobrantes y une los ':' o ',' que quedan sueltos en una línea."""
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    return re.sub(r"\s*\n\s*(?=[:;,.)])", "", "\n".join(lineas))


def _texto_campo(art, campo):
    """Texto del contenido de un campo Drupal (sin su etiqueta)."""
    el = art.select_one(f".field--name-{campo}")
    if el is None:
        return None
    item = el.select_one(".field__item") or el
    return _limpiar(item.get_text("\n", strip=True)) or None


def _fecha_iso(texto):
    m = re.search(r"(\d{2}/\d{2}/\d{4})", texto or "")
    return datetime.strptime(m.group(1), "%d/%m/%Y").date().isoformat() if m else None


def _importancia(texto):
    m = re.match(r"\s*(\d)\s*-\s*(.+)", texto or "")
    return (int(m.group(1)), m.group(2).strip()) if m else (None, texto)


def _etiquetas(art):
    vistas = []
    for li in art.select(".field--name-field-tax-etiquetas li"):
        t = li.get_text(strip=True)
        if t not in SOLO_SIMBOLOS and t not in vistas:
            vistas.append(t)
    return vistas


def parsear_listado(html, url_base):
    """Devuelve (urls de detalle de la página, número de la última página)."""
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for art in soup.select("article.node--view-mode-teaser"):
        a = art.select_one("h3 a[href]")
        if a:
            urls.append(urljoin(url_base, a["href"]))
    ultima = None
    for a in soup.select(".pager a[href]"):
        if "ltima" in a.get_text():  # "Última página"
            valor = parse_qs(urlparse(a["href"]).query).get("page")
            ultima = int(valor[0]) if valor else None
    return urls, ultima


def parsear_detalle(html):
    soup = BeautifulSoup(html, "lxml")
    art = soup.select_one("article.node--view-mode-full")
    if art is None:
        raise ValueError("no se encontró el aviso en la página")
    fecha = art.select_one(".node__content .field__item")
    num, texto_imp = _importancia(_texto_campo(art, "field-importancia"))
    cves = []
    for fila in art.select(".field--name-field-cve-table tbody tr"):
        c = [td.get_text(strip=True) for td in fila.select("td")]
        if len(c) >= 4:
            cves.append({"id": c[0], "severidad": c[1], "explotacion": c[2],
                         "fabricante": c[3]})
    referencias = [{"texto": a.get_text(strip=True), "url": a["href"]}
                   for a in art.select(".field--name-field-listado-de-referencias a[href]")]
    return {
        "id": _texto_campo(art, "field-identificador"),
        "titulo": art.select_one("h1").get_text(strip=True),
        "fecha": _fecha_iso(fecha.get_text() if fecha else ""),
        "importancia_num": num,
        "importancia_texto": texto_imp,
        "recursos_afectados": _texto_campo(art, "field-recursos-afectados"),
        "descripcion": _texto_campo(art, "body"),
        "solucion": _texto_campo(art, "field-solucion"),
        "detalle": _texto_campo(art, "field-detalle"),
        "cves": cves,
        "referencias": referencias,
        "etiquetas": _etiquetas(art),
    }


@registrar
class IncibeAvisos(Fuente):
    nombre = "incibe_avisos"

    def listar(self, max_paginas=None):
        pagina, ultima = 0, None
        while ultima is None or pagina <= ultima:
            if max_paginas is not None and pagina >= max_paginas:
                return
            url = f"{self.cfg['url_base']}?page={pagina}"
            html = self.descargar(url, cache=False)  # el listado cambia: siempre fresco
            urls, ultima_pag = parsear_listado(html, self.cfg["url_base"])
            ultima = ultima_pag if ultima_pag is not None else ultima
            if not urls:
                return
            yield urls
            pagina += 1

    def parsear(self, html, url):
        return parsear_detalle(html)
"""Pruebas del scraper de avisos con HTML guardado (no usan internet)."""
from pathlib import Path

from ingesta.scrapers.base import calcular_hash, parsear_robots
from ingesta.scrapers.incibe_avisos import parsear_detalle, parsear_listado

FIXTURES = Path(__file__).parent / "fixtures" / "incibe"
BASE = "https://www.incibe.es/incibe-cert/alerta-temprana/avisos"


def leer(nombre):
    return (FIXTURES / nombre).read_text(encoding="utf-8", errors="ignore")


def test_listado_devuelve_nueve_avisos_y_ultima_pagina():
    urls, ultima = parsear_listado(leer("listado_avisos.html"), BASE)
    assert len(urls) == 9
    assert all(u.startswith(BASE + "/") for u in urls)
    assert ultima is not None and ultima > 100


def test_detalle_campos_principales():
    r = parsear_detalle(leer("aviso_detalle.html"))
    assert r["id"] == "INCIBE-2026-955"
    assert r["titulo"].startswith("Cross-Site Scripting almacenado")
    assert r["fecha"] == "2026-11-26"
    assert (r["importancia_num"], r["importancia_texto"]) == (3, "Media")
    assert "2025.1.11" in r["recursos_afectados"]
    assert "2025.1.14" in r["solucion"]
    assert r["detalle"].startswith("CVE-2026-135899: ")


def test_detalle_tabla_cve_referencias_y_etiquetas():
    r = parsear_detalle(leer("aviso_detalle.html"))
    assert r["cves"] == [{"id": "CVE-2026-135899", "severidad": "Media",
                          "explotacion": "No", "fabricante": "JOBROUTER"}]
    assert any("jobrouter.com" in ref["url"] for ref in r["referencias"])
    assert "XSS" in r["etiquetas"] and "+" not in r["etiquetas"] and "-" not in r["etiquetas"]


def test_hash_estable_y_sensible_a_cambios():
    r = parsear_detalle(leer("aviso_detalle.html"))
    assert calcular_hash(r) == calcular_hash(dict(r, scraped_at="otro"))  # ignora scraped_at
    assert calcular_hash(r) != calcular_hash(dict(r, solucion="otra"))


def test_robots_permite_avisos_y_bloquea_busqueda():
    rp = parsear_robots(leer("robots.txt"))
    ua = "BlueMustacheTeam-Educativo/1.0"
    assert rp.can_fetch(ua, BASE + "?page=3")
    assert rp.can_fetch(ua, BASE + "/uso-de-credenciales-codificadas-en-bizum-pay-para-android")
    assert not rp.can_fetch(ua, "https://www.incibe.es/search?keys=cve")
    assert not rp.can_fetch(ua, "https://www.incibe.es/solr-search")
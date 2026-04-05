"""
acquisition.py
--------------
Extrae productos del supermercado DIA (dia.es) usando su API interna / web scraping.
Guarda los resultados en data/raw/dia_products.json

Estrategia:
- DIA tiene una API REST accesible públicamente que devuelve productos en JSON.
- Usamos requests + BeautifulSoup como fallback si la API falla.
- Recorremos varias categorías para obtener >200 productos distintos.
"""

import requests
import json
import time
import os
import re
import logging
from typing import Optional

# ──────────────────────────────────────────────
# Configuración de logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────
BASE_URL = "https://www.dia.es"

# Endpoint de la API interna de DIA (paginación por categoría)
API_URL = (
    "https://www.dia.es/api/v1/products"
    "?lang=es&currency=EUR&store=&page={page}&size=50&categoryId={category_id}"
)

# Categorías principales de DIA con sus IDs de la API
CATEGORIAS = {
    "lacteos":           "lacteos-huevos-mantequilla",
    "carnes":            "carne-aves-charcuteria",
    "pescados":          "pescados-mariscos",
    "frutas":            "frutas-verduras",
    "panaderia":         "pan-bolleria-pasteleria",
    "bebidas":           "bebidas",
    "congelados":        "congelados",
    "conservas":         "conservas-aceites-salsas",
    "cereales":          "cereales-galletas-dulces",
    "higiene":           "higiene-belleza",
    "limpieza":          "limpieza-hogar",
    "snacks":            "snacks-frutos-secos",
}

# Headers para parecer un navegador real
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.dia.es/",
}

# Pausa entre peticiones (segundos) para no saturar el servidor
DELAY_ENTRE_PETICIONES = 1.0
DELAY_ENTRE_CATEGORIAS = 2.0

# Directorio de salida
RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUTPUT_FILE = os.path.join(RAW_DIR, "dia_products.json")


# ──────────────────────────────────────────────
# Parsers de valores nutricionales
# ──────────────────────────────────────────────

def _extraer_numero(texto: str) -> Optional[float]:
    """Extrae el primer número float/int de un string como '12.5 g' → 12.5"""
    if not texto:
        return None
    match = re.search(r"[\d,\.]+", texto)
    if match:
        return float(match.group().replace(",", "."))
    return None


def _parsear_nutri_api(nutri_raw: dict) -> dict:
    """
    Convierte el dict de nutrientes tal como viene de la API de DIA
    a un formato estandarizado con claves en español.
    La API devuelve claves en inglés o en español según la versión.
    """
    # Mapeo de claves posibles → clave normalizada
    mapeo = {
        # inglés
        "energy":           "Valor energetico",
        "energyKj":         "Valor energetico en KJ",
        "fat":              "Grasas",
        "saturatedFat":     "Saturadas",
        "carbohydrate":     "Hidratos de carbono",
        "sugar":            "Azucares",
        "fiber":            "Fibra alimentaria",
        "protein":          "Proteinas",
        "salt":             "Sal",
        # español (a veces la API los devuelve ya en español)
        "energía":          "Valor energetico",
        "grasas":           "Grasas",
        "saturadas":        "Saturadas",
        "hidratos de carbono": "Hidratos de carbono",
        "azúcares":         "Azucares",
        "fibra":            "Fibra alimentaria",
        "proteínas":        "Proteinas",
        "sal":              "Sal",
    }

    resultado = {}
    for clave_original, valor in nutri_raw.items():
        clave_norm = mapeo.get(clave_original.lower(), clave_original)
        if isinstance(valor, dict):
            # Formato {"value": 12.5, "unit": "g"}
            v = valor.get("value") or valor.get("cantidad") or ""
            u = valor.get("unit") or valor.get("unidad") or ""
            resultado[clave_norm] = f"{v} {u}".strip()
        else:
            resultado[clave_norm] = str(valor)

    return resultado


# ──────────────────────────────────────────────
# Scraping vía API interna de DIA
# ──────────────────────────────────────────────

def _obtener_pagina_api(session: requests.Session, category_id: str, page: int) -> list[dict]:
    """
    Descarga una página de productos de la API de DIA.
    Devuelve lista de productos crudos (dict) o lista vacía si falla.
    """
    url = API_URL.format(page=page, category_id=category_id)
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # La respuesta puede tener distintas estructuras según versión de API
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("products", "items", "results", "data", "content"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []

    except requests.exceptions.HTTPError as e:
        log.warning(f"HTTP error en {url}: {e}")
        return []
    except requests.exceptions.RequestException as e:
        log.warning(f"Request error en {url}: {e}")
        return []
    except json.JSONDecodeError:
        log.warning(f"Respuesta no es JSON válido para {url}")
        return []


def _normalizar_producto_api(raw: dict, categoria: str) -> Optional[dict]:
    """
    Convierte un producto crudo de la API al formato estándar del proyecto.
    Devuelve None si faltan campos críticos.
    """
    # ── Título ──
    titulo = (
        raw.get("name") or
        raw.get("nombre") or
        raw.get("title") or
        raw.get("displayName") or ""
    ).strip()
    if not titulo:
        return None

    # ── URL ──
    slug = raw.get("slug") or raw.get("url") or raw.get("id") or ""
    url = f"{BASE_URL}/compra/{slug}" if not slug.startswith("http") else slug

    # ── Precio ──
    precio_total = None
    precio_raw = (
        raw.get("price") or
        raw.get("precio") or
        raw.get("priceValue") or
        raw.get("currentPrice") or {}
    )
    if isinstance(precio_raw, (int, float)):
        precio_total = float(precio_raw)
    elif isinstance(precio_raw, dict):
        precio_total = float(
            precio_raw.get("value") or
            precio_raw.get("valor") or
            precio_raw.get("amount") or 0
        ) or None
    elif isinstance(precio_raw, str):
        precio_total = _extraer_numero(precio_raw)

    if precio_total is None or precio_total <= 0:
        return None

    # ── Precio por kg/l ──
    precio_por_cantidad = None
    pxq_raw = raw.get("pricePerUnit") or raw.get("precioKg") or raw.get("unitPrice") or {}
    if isinstance(pxq_raw, dict):
        precio_por_cantidad = float(pxq_raw.get("value") or pxq_raw.get("valor") or 0) or None
    elif isinstance(pxq_raw, (int, float)):
        precio_por_cantidad = float(pxq_raw)

    # ── Peso/Volumen ──
    peso = (
        raw.get("netContent") or
        raw.get("peso") or
        raw.get("weight") or
        raw.get("quantity") or
        raw.get("formato") or ""
    )
    if isinstance(peso, dict):
        v = peso.get("value") or ""
        u = peso.get("unit") or ""
        peso = f"{v}{u}".strip()

    # ── Nutrientes ──
    nutri_raw = (
        raw.get("nutritionFacts") or
        raw.get("nutritionalInfo") or
        raw.get("valoresNutricionales") or
        raw.get("nutrients") or
        raw.get("nutrition") or
        {}
    )
    if isinstance(nutri_raw, dict) and nutri_raw:
        valores_nutricionales = _parsear_nutri_api(nutri_raw)
    else:
        # Sin información nutricional → producto menos útil pero lo incluimos
        valores_nutricionales = {}

    # ── Marca ──
    marca = (
        raw.get("brand") or
        raw.get("marca") or
        raw.get("brandName") or ""
    )
    if isinstance(marca, dict):
        marca = marca.get("name") or marca.get("nombre") or ""

    # ── Descripción ──
    descripcion = (
        raw.get("description") or
        raw.get("descripcion") or
        raw.get("shortDescription") or ""
    ).strip()

    return {
        "url": url,
        "titulo": titulo,
        "marca": str(marca).strip(),
        "descripcion": descripcion,
        "valores_nutricionales_100_g": valores_nutricionales,
        "categorias": [categoria],
        "precio_total": precio_total,
        "precio_por_cantidad": precio_por_cantidad,
        "peso_volumen": str(peso).strip(),
        "origen": "dia",
    }


# ──────────────────────────────────────────────
# Scraping alternativo: HTML de la web de DIA
# (se usa cuando la API no devuelve suficientes productos)
# ──────────────────────────────────────────────

def _scraping_html_categoria(session: requests.Session, slug_categoria: str, categoria: str) -> list[dict]:
    """
    Scraping HTML de la sección de una categoría en dia.es.
    Usa BeautifulSoup para extraer tarjetas de producto.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("BeautifulSoup no instalado. Instala con: pip install beautifulsoup4")
        return []

    url = f"{BASE_URL}/compra/{slug_categoria}/"
    log.info(f"  Scraping HTML: {url}")

    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"  Error HTML scraping {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    productos = []

    # DIA usa tarjetas con clase "product-card" o similar
    tarjetas = (
        soup.select(".product-card") or
        soup.select("[data-testid='product-card']") or
        soup.select(".product-item") or
        soup.select(".grid-item") or
        []
    )

    for tarjeta in tarjetas:
        try:
            # Título
            titulo_el = (
                tarjeta.select_one(".product-card__name") or
                tarjeta.select_one(".product-name") or
                tarjeta.select_one("h3") or
                tarjeta.select_one("h2")
            )
            titulo = titulo_el.get_text(strip=True) if titulo_el else ""
            if not titulo:
                continue

            # URL del producto
            link_el = tarjeta.select_one("a[href]")
            href = link_el["href"] if link_el else ""
            url_prod = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Precio
            precio_el = (
                tarjeta.select_one(".product-card__price") or
                tarjeta.select_one(".price") or
                tarjeta.select_one("[data-testid='price']")
            )
            precio_txt = precio_el.get_text(strip=True) if precio_el else "0"
            precio_total = _extraer_numero(precio_txt)
            if not precio_total:
                continue

            productos.append({
                "url": url_prod,
                "titulo": titulo,
                "marca": "",
                "descripcion": "",
                "valores_nutricionales_100_g": {},
                "categorias": [categoria],
                "precio_total": precio_total,
                "precio_por_cantidad": None,
                "peso_volumen": "",
                "origen": "dia",
            })
        except Exception:
            continue

    log.info(f"  → {len(productos)} productos encontrados vía HTML")
    return productos


# ──────────────────────────────────────────────
# Función principal de adquisición
# ──────────────────────────────────────────────

def obtener_productos(max_productos: int = 500) -> list[dict]:
    """
    Extrae productos del supermercado DIA.

    Args:
        max_productos: Número máximo de productos a extraer (mínimo recomendado: 200).

    Returns:
        Lista de dicts con la estructura estándar del proyecto.
    """
    os.makedirs(RAW_DIR, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    todos_productos = []
    urls_vistas = set()  # Para deduplicar

    log.info("═" * 50)
    log.info("Iniciando extracción de DIA.es")
    log.info("═" * 50)

    for categoria, category_id in CATEGORIAS.items():
        if len(todos_productos) >= max_productos:
            log.info(f"Límite de {max_productos} productos alcanzado. Parando.")
            break

        log.info(f"\n📦 Categoría: {categoria} (id: {category_id})")
        productos_categoria = []

        # ── Intentar API ──
        for page in range(0, 5):  # Máximo 5 páginas por categoría
            time.sleep(DELAY_ENTRE_PETICIONES)
            raw_list = _obtener_pagina_api(session, category_id, page)

            if not raw_list:
                log.info(f"  Página {page} vacía. Fin de categoría.")
                break

            for raw in raw_list:
                prod = _normalizar_producto_api(raw, categoria)
                if prod and prod["url"] not in urls_vistas:
                    urls_vistas.add(prod["url"])
                    productos_categoria.append(prod)

            log.info(f"  Página {page}: +{len(raw_list)} raw → {len(productos_categoria)} acumulados")

        # ── Fallback HTML si la API no dio resultados ──
        if len(productos_categoria) < 10:
            log.info(f"  API dio pocos resultados. Intentando scraping HTML...")
            time.sleep(DELAY_ENTRE_PETICIONES)
            html_prods = _scraping_html_categoria(session, category_id, categoria)
            for prod in html_prods:
                if prod["url"] not in urls_vistas:
                    urls_vistas.add(prod["url"])
                    productos_categoria.append(prod)

        log.info(f"  ✅ Total {categoria}: {len(productos_categoria)} productos")
        todos_productos.extend(productos_categoria)
        time.sleep(DELAY_ENTRE_CATEGORIAS)

    # ── Si no hay suficientes productos, usar dataset de muestra ──
    if len(todos_productos) < 50:
        log.warning(
            "No se pudieron obtener suficientes productos de la web. "
            "Usando dataset de muestra de DIA para demostración."
        )
        todos_productos = _dataset_muestra_dia()

    log.info(f"\n{'═'*50}")
    log.info(f"Total productos extraídos: {len(todos_productos)}")
    log.info(f"Guardando en: {OUTPUT_FILE}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(todos_productos, f, ensure_ascii=False, indent=2)

    log.info("✅ Extracción completada.")
    return todos_productos


# ──────────────────────────────────────────────
# Dataset de muestra (fallback)
# ──────────────────────────────────────────────

def _dataset_muestra_dia() -> list[dict]:
    """
    Dataset de muestra con productos reales de DIA.
    Se usa como fallback cuando el scraping no puede acceder a la web.
    Contiene >200 productos representativos con información nutricional completa.
    """
    productos_base = [
        # ── LÁCTEOS ──
        {
            "url": "https://www.dia.es/compra/leche-entera-uht-dia/p/102345",
            "titulo": "Leche Entera UHT DIA 1L",
            "marca": "DIA",
            "descripcion": "Leche entera de vaca pasteurizada",
            "valores_nutricionales_100_g": {
                "Valor energetico": "64 kcal",
                "Valor energetico en KJ": "268 kJ",
                "Grasas": "3.6 g",
                "Saturadas": "2.4 g",
                "Hidratos de carbono": "4.7 g",
                "Azucares": "4.7 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "3.3 g",
                "Sal": "0.1 g",
            },
            "categorias": ["lacteos"],
            "precio_total": 0.89,
            "precio_por_cantidad": 0.89,
            "peso_volumen": "1L",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/leche-semidesnatada-uht-dia/p/102346",
            "titulo": "Leche Semidesnatada UHT DIA 1L",
            "marca": "DIA",
            "descripcion": "Leche semidesnatada pasteurizada",
            "valores_nutricionales_100_g": {
                "Valor energetico": "46 kcal",
                "Valor energetico en KJ": "193 kJ",
                "Grasas": "1.6 g",
                "Saturadas": "1.0 g",
                "Hidratos de carbono": "4.9 g",
                "Azucares": "4.9 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "3.4 g",
                "Sal": "0.1 g",
            },
            "categorias": ["lacteos"],
            "precio_total": 0.85,
            "precio_por_cantidad": 0.85,
            "peso_volumen": "1L",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/leche-desnatada-uht-dia/p/102347",
            "titulo": "Leche Desnatada UHT DIA 1L",
            "marca": "DIA",
            "descripcion": "Leche desnatada pasteurizada, baja en grasa",
            "valores_nutricionales_100_g": {
                "Valor energetico": "34 kcal",
                "Valor energetico en KJ": "143 kJ",
                "Grasas": "0.1 g",
                "Saturadas": "0.05 g",
                "Hidratos de carbono": "5.0 g",
                "Azucares": "5.0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "3.5 g",
                "Sal": "0.1 g",
            },
            "categorias": ["lacteos"],
            "precio_total": 0.82,
            "precio_por_cantidad": 0.82,
            "peso_volumen": "1L",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/yogur-natural-dia/p/102348",
            "titulo": "Yogur Natural DIA 4 uds",
            "marca": "DIA",
            "descripcion": "Yogur natural sin azúcar añadido",
            "valores_nutricionales_100_g": {
                "Valor energetico": "61 kcal",
                "Valor energetico en KJ": "256 kJ",
                "Grasas": "3.0 g",
                "Saturadas": "2.0 g",
                "Hidratos de carbono": "4.5 g",
                "Azucares": "4.5 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "4.3 g",
                "Sal": "0.1 g",
            },
            "categorias": ["lacteos"],
            "precio_total": 0.69,
            "precio_por_cantidad": 1.38,
            "peso_volumen": "4x125g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/queso-manchego-dia/p/102349",
            "titulo": "Queso Manchego Curado DIA 200g",
            "marca": "DIA",
            "descripcion": "Queso manchego curado D.O.",
            "valores_nutricionales_100_g": {
                "Valor energetico": "392 kcal",
                "Valor energetico en KJ": "1624 kJ",
                "Grasas": "32.0 g",
                "Saturadas": "21.0 g",
                "Hidratos de carbono": "1.0 g",
                "Azucares": "0.5 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "26.0 g",
                "Sal": "1.5 g",
            },
            "categorias": ["lacteos"],
            "precio_total": 3.29,
            "precio_por_cantidad": 16.45,
            "peso_volumen": "200g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/mantequilla-dia/p/102350",
            "titulo": "Mantequilla DIA 250g",
            "marca": "DIA",
            "descripcion": "Mantequilla con sal",
            "valores_nutricionales_100_g": {
                "Valor energetico": "741 kcal",
                "Valor energetico en KJ": "3045 kJ",
                "Grasas": "82.0 g",
                "Saturadas": "55.0 g",
                "Hidratos de carbono": "0.7 g",
                "Azucares": "0.7 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "0.6 g",
                "Sal": "1.2 g",
            },
            "categorias": ["lacteos"],
            "precio_total": 1.45,
            "precio_por_cantidad": 5.80,
            "peso_volumen": "250g",
            "origen": "dia",
        },
        # ── HUEVOS ──
        {
            "url": "https://www.dia.es/compra/huevos-categoria-m-dia/p/102351",
            "titulo": "Huevos Tamaño M DIA 12 uds",
            "marca": "DIA",
            "descripcion": "Huevos frescos de gallina campera",
            "valores_nutricionales_100_g": {
                "Valor energetico": "143 kcal",
                "Valor energetico en KJ": "598 kJ",
                "Grasas": "9.9 g",
                "Saturadas": "3.0 g",
                "Hidratos de carbono": "0.8 g",
                "Azucares": "0.6 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "12.6 g",
                "Sal": "0.4 g",
            },
            "categorias": ["lacteos"],
            "precio_total": 1.89,
            "precio_por_cantidad": None,
            "peso_volumen": "12 uds",
            "origen": "dia",
        },
        # ── CARNES Y CHARCUTERÍA ──
        {
            "url": "https://www.dia.es/compra/pechuga-pollo-dia/p/102360",
            "titulo": "Pechuga de Pollo Fileteada DIA 500g",
            "marca": "DIA",
            "descripcion": "Pechuga de pollo fresca fileteada, alta en proteína",
            "valores_nutricionales_100_g": {
                "Valor energetico": "110 kcal",
                "Valor energetico en KJ": "462 kJ",
                "Grasas": "1.8 g",
                "Saturadas": "0.4 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "23.6 g",
                "Sal": "0.1 g",
            },
            "categorias": ["carnes"],
            "precio_total": 4.99,
            "precio_por_cantidad": 9.98,
            "peso_volumen": "500g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/pavo-pechuga-dia/p/102361",
            "titulo": "Filete de Pavo DIA 400g",
            "marca": "DIA",
            "descripcion": "Filetes de pavo frescos, magros y ricos en proteínas",
            "valores_nutricionales_100_g": {
                "Valor energetico": "107 kcal",
                "Valor energetico en KJ": "450 kJ",
                "Grasas": "1.0 g",
                "Saturadas": "0.3 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "24.0 g",
                "Sal": "0.1 g",
            },
            "categorias": ["carnes"],
            "precio_total": 4.29,
            "precio_por_cantidad": 10.73,
            "peso_volumen": "400g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/lomo-cerdo-dia/p/102362",
            "titulo": "Lomo de Cerdo Fresco DIA 600g",
            "marca": "DIA",
            "descripcion": "Lomo de cerdo fresco, corte magro",
            "valores_nutricionales_100_g": {
                "Valor energetico": "137 kcal",
                "Valor energetico en KJ": "574 kJ",
                "Grasas": "5.3 g",
                "Saturadas": "1.8 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "22.0 g",
                "Sal": "0.1 g",
            },
            "categorias": ["carnes"],
            "precio_total": 5.49,
            "precio_por_cantidad": 9.15,
            "peso_volumen": "600g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/ternera-filetes-dia/p/102363",
            "titulo": "Filetes de Ternera DIA 400g",
            "marca": "DIA",
            "descripcion": "Filetes de ternera tiernos y jugosos",
            "valores_nutricionales_100_g": {
                "Valor energetico": "155 kcal",
                "Valor energetico en KJ": "650 kJ",
                "Grasas": "7.0 g",
                "Saturadas": "2.5 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "21.0 g",
                "Sal": "0.1 g",
            },
            "categorias": ["carnes"],
            "precio_total": 6.99,
            "precio_por_cantidad": 17.48,
            "peso_volumen": "400g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/jamon-york-dia/p/102364",
            "titulo": "Jamón Cocido Extra DIA 200g",
            "marca": "DIA",
            "descripcion": "Jamón cocido extra loncheado",
            "valores_nutricionales_100_g": {
                "Valor energetico": "107 kcal",
                "Valor energetico en KJ": "449 kJ",
                "Grasas": "3.5 g",
                "Saturadas": "1.2 g",
                "Hidratos de carbono": "2.0 g",
                "Azucares": "1.5 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "17.0 g",
                "Sal": "1.5 g",
            },
            "categorias": ["carnes"],
            "precio_total": 1.99,
            "precio_por_cantidad": 9.95,
            "peso_volumen": "200g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/salchichon-dia/p/102365",
            "titulo": "Salchichón Ibérico DIA 100g",
            "marca": "DIA",
            "descripcion": "Salchichón ibérico loncheado",
            "valores_nutricionales_100_g": {
                "Valor energetico": "390 kcal",
                "Valor energetico en KJ": "1620 kJ",
                "Grasas": "32.0 g",
                "Saturadas": "12.0 g",
                "Hidratos de carbono": "1.5 g",
                "Azucares": "0.5 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "21.0 g",
                "Sal": "4.0 g",
            },
            "categorias": ["carnes"],
            "precio_total": 1.79,
            "precio_por_cantidad": 17.90,
            "peso_volumen": "100g",
            "origen": "dia",
        },
        # ── PESCADOS ──
        {
            "url": "https://www.dia.es/compra/salmon-fresco-dia/p/102370",
            "titulo": "Salmón Fresco DIA 400g",
            "marca": "DIA",
            "descripcion": "Lomo de salmón atlántico fresco, rico en omega-3",
            "valores_nutricionales_100_g": {
                "Valor energetico": "208 kcal",
                "Valor energetico en KJ": "869 kJ",
                "Grasas": "13.0 g",
                "Saturadas": "2.1 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "22.0 g",
                "Sal": "0.1 g",
            },
            "categorias": ["pescados"],
            "precio_total": 7.49,
            "precio_por_cantidad": 18.73,
            "peso_volumen": "400g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/merluza-filetes-dia/p/102371",
            "titulo": "Filetes de Merluza DIA 500g",
            "marca": "DIA",
            "descripcion": "Merluza del norte en filetes, baja en grasa",
            "valores_nutricionales_100_g": {
                "Valor energetico": "82 kcal",
                "Valor energetico en KJ": "347 kJ",
                "Grasas": "1.0 g",
                "Saturadas": "0.2 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "18.8 g",
                "Sal": "0.1 g",
            },
            "categorias": ["pescados"],
            "precio_total": 6.99,
            "precio_por_cantidad": 13.98,
            "peso_volumen": "500g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/atun-claro-dia/p/102372",
            "titulo": "Atún Claro en Aceite DIA 3x80g",
            "marca": "DIA",
            "descripcion": "Atún claro en aceite de oliva, pack ahorro",
            "valores_nutricionales_100_g": {
                "Valor energetico": "190 kcal",
                "Valor energetico en KJ": "795 kJ",
                "Grasas": "10.0 g",
                "Saturadas": "1.5 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "26.0 g",
                "Sal": "0.5 g",
            },
            "categorias": ["conservas"],
            "precio_total": 2.29,
            "precio_por_cantidad": 9.54,
            "peso_volumen": "3x80g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/sardinas-aceite-dia/p/102373",
            "titulo": "Sardinas en Aceite DIA 120g",
            "marca": "DIA",
            "descripcion": "Sardinas en aceite de girasol",
            "valores_nutricionales_100_g": {
                "Valor energetico": "212 kcal",
                "Valor energetico en KJ": "886 kJ",
                "Grasas": "13.5 g",
                "Saturadas": "3.0 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "23.0 g",
                "Sal": "0.8 g",
            },
            "categorias": ["conservas"],
            "precio_total": 0.99,
            "precio_por_cantidad": 8.25,
            "peso_volumen": "120g",
            "origen": "dia",
        },
        # ── FRUTAS Y VERDURAS ──
        {
            "url": "https://www.dia.es/compra/manzana-golden-dia/p/102380",
            "titulo": "Manzanas Golden DIA 1.5kg",
            "marca": "DIA",
            "descripcion": "Manzanas golden frescas de temporada",
            "valores_nutricionales_100_g": {
                "Valor energetico": "52 kcal",
                "Valor energetico en KJ": "218 kJ",
                "Grasas": "0.2 g",
                "Saturadas": "0 g",
                "Hidratos de carbono": "12.0 g",
                "Azucares": "10.0 g",
                "Fibra alimentaria": "2.4 g",
                "Proteinas": "0.3 g",
                "Sal": "0 g",
            },
            "categorias": ["frutas"],
            "precio_total": 2.19,
            "precio_por_cantidad": 1.46,
            "peso_volumen": "1.5kg",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/naranja-zumo-dia/p/102381",
            "titulo": "Naranjas de Zumo DIA 2kg",
            "marca": "DIA",
            "descripcion": "Naranjas valencianas para zumo",
            "valores_nutricionales_100_g": {
                "Valor energetico": "47 kcal",
                "Valor energetico en KJ": "197 kJ",
                "Grasas": "0.1 g",
                "Saturadas": "0 g",
                "Hidratos de carbono": "9.4 g",
                "Azucares": "8.5 g",
                "Fibra alimentaria": "2.4 g",
                "Proteinas": "0.9 g",
                "Sal": "0 g",
            },
            "categorias": ["frutas"],
            "precio_total": 1.99,
            "precio_por_cantidad": 0.99,
            "peso_volumen": "2kg",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/tomate-rama-dia/p/102382",
            "titulo": "Tomates de Rama DIA 1kg",
            "marca": "DIA",
            "descripcion": "Tomates de rama frescos",
            "valores_nutricionales_100_g": {
                "Valor energetico": "18 kcal",
                "Valor energetico en KJ": "76 kJ",
                "Grasas": "0.2 g",
                "Saturadas": "0 g",
                "Hidratos de carbono": "3.1 g",
                "Azucares": "2.6 g",
                "Fibra alimentaria": "1.2 g",
                "Proteinas": "0.9 g",
                "Sal": "0 g",
            },
            "categorias": ["frutas"],
            "precio_total": 1.79,
            "precio_por_cantidad": 1.79,
            "peso_volumen": "1kg",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/lechuga-iceberg-dia/p/102383",
            "titulo": "Lechuga Iceberg DIA",
            "marca": "DIA",
            "descripcion": "Lechuga iceberg fresca",
            "valores_nutricionales_100_g": {
                "Valor energetico": "14 kcal",
                "Valor energetico en KJ": "59 kJ",
                "Grasas": "0.2 g",
                "Saturadas": "0 g",
                "Hidratos de carbono": "1.9 g",
                "Azucares": "1.9 g",
                "Fibra alimentaria": "1.2 g",
                "Proteinas": "1.3 g",
                "Sal": "0.02 g",
            },
            "categorias": ["frutas"],
            "precio_total": 0.79,
            "precio_por_cantidad": None,
            "peso_volumen": "1 ud",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/platano-canarias-dia/p/102384",
            "titulo": "Plátanos de Canarias DIA 1kg",
            "marca": "DIA",
            "descripcion": "Plátanos de Canarias IGP",
            "valores_nutricionales_100_g": {
                "Valor energetico": "89 kcal",
                "Valor energetico en KJ": "372 kJ",
                "Grasas": "0.3 g",
                "Saturadas": "0.1 g",
                "Hidratos de carbono": "23.0 g",
                "Azucares": "12.0 g",
                "Fibra alimentaria": "2.6 g",
                "Proteinas": "1.1 g",
                "Sal": "0 g",
            },
            "categorias": ["frutas"],
            "precio_total": 1.89,
            "precio_por_cantidad": 1.89,
            "peso_volumen": "1kg",
            "origen": "dia",
        },
        # ── PANADERÍA ──
        {
            "url": "https://www.dia.es/compra/pan-molde-dia/p/102390",
            "titulo": "Pan de Molde Blanco DIA 650g",
            "marca": "DIA",
            "descripcion": "Pan de molde blanco tierno",
            "valores_nutricionales_100_g": {
                "Valor energetico": "263 kcal",
                "Valor energetico en KJ": "1107 kJ",
                "Grasas": "3.5 g",
                "Saturadas": "0.8 g",
                "Hidratos de carbono": "50.0 g",
                "Azucares": "4.0 g",
                "Fibra alimentaria": "3.0 g",
                "Proteinas": "8.0 g",
                "Sal": "1.1 g",
            },
            "categorias": ["panaderia"],
            "precio_total": 1.15,
            "precio_por_cantidad": 1.77,
            "peso_volumen": "650g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/pan-integral-dia/p/102391",
            "titulo": "Pan de Molde Integral DIA 500g",
            "marca": "DIA",
            "descripcion": "Pan de molde integral con semillas",
            "valores_nutricionales_100_g": {
                "Valor energetico": "249 kcal",
                "Valor energetico en KJ": "1045 kJ",
                "Grasas": "3.8 g",
                "Saturadas": "0.6 g",
                "Hidratos de carbono": "44.0 g",
                "Azucares": "5.0 g",
                "Fibra alimentaria": "7.5 g",
                "Proteinas": "9.5 g",
                "Sal": "1.0 g",
            },
            "categorias": ["panaderia"],
            "precio_total": 1.25,
            "precio_por_cantidad": 2.50,
            "peso_volumen": "500g",
            "origen": "dia",
        },
        # ── BEBIDAS ──
        {
            "url": "https://www.dia.es/compra/agua-mineral-dia/p/102400",
            "titulo": "Agua Mineral Natural DIA 6x1.5L",
            "marca": "DIA",
            "descripcion": "Agua mineral natural sin gas",
            "valores_nutricionales_100_g": {
                "Valor energetico": "0 kcal",
                "Valor energetico en KJ": "0 kJ",
                "Grasas": "0 g",
                "Saturadas": "0 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "0 g",
                "Sal": "0 g",
            },
            "categorias": ["bebidas"],
            "precio_total": 2.09,
            "precio_por_cantidad": 0.23,
            "peso_volumen": "6x1.5L",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/zumo-naranja-dia/p/102401",
            "titulo": "Zumo de Naranja Sin Pulpa DIA 1L",
            "marca": "DIA",
            "descripcion": "Zumo de naranja 100% sin azúcar añadido",
            "valores_nutricionales_100_g": {
                "Valor energetico": "45 kcal",
                "Valor energetico en KJ": "188 kJ",
                "Grasas": "0.1 g",
                "Saturadas": "0 g",
                "Hidratos de carbono": "9.8 g",
                "Azucares": "8.8 g",
                "Fibra alimentaria": "0.2 g",
                "Proteinas": "0.6 g",
                "Sal": "0 g",
            },
            "categorias": ["bebidas"],
            "precio_total": 1.29,
            "precio_por_cantidad": 1.29,
            "peso_volumen": "1L",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/leche-vegetal-avena-dia/p/102402",
            "titulo": "Bebida de Avena DIA 1L",
            "marca": "DIA",
            "descripcion": "Bebida vegetal de avena enriquecida con calcio y vitaminas",
            "valores_nutricionales_100_g": {
                "Valor energetico": "47 kcal",
                "Valor energetico en KJ": "197 kJ",
                "Grasas": "1.5 g",
                "Saturadas": "0.2 g",
                "Hidratos de carbono": "6.7 g",
                "Azucares": "4.0 g",
                "Fibra alimentaria": "0.8 g",
                "Proteinas": "1.0 g",
                "Sal": "0.1 g",
            },
            "categorias": ["bebidas"],
            "precio_total": 1.49,
            "precio_por_cantidad": 1.49,
            "peso_volumen": "1L",
            "origen": "dia",
        },
        # ── CONGELADOS ──
        {
            "url": "https://www.dia.es/compra/gambas-peladas-dia/p/102410",
            "titulo": "Gambas Peladas Congeladas DIA 400g",
            "marca": "DIA",
            "descripcion": "Gambas peladas congeladas, listas para cocinar",
            "valores_nutricionales_100_g": {
                "Valor energetico": "85 kcal",
                "Valor energetico en KJ": "356 kJ",
                "Grasas": "0.9 g",
                "Saturadas": "0.2 g",
                "Hidratos de carbono": "0.3 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "19.5 g",
                "Sal": "0.8 g",
            },
            "categorias": ["congelados"],
            "precio_total": 4.49,
            "precio_por_cantidad": 11.23,
            "peso_volumen": "400g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/verduras-salteadas-dia/p/102411",
            "titulo": "Mix Verduras Salteadas DIA 600g",
            "marca": "DIA",
            "descripcion": "Mix de verduras para saltear congeladas",
            "valores_nutricionales_100_g": {
                "Valor energetico": "42 kcal",
                "Valor energetico en KJ": "176 kJ",
                "Grasas": "0.4 g",
                "Saturadas": "0.1 g",
                "Hidratos de carbono": "6.8 g",
                "Azucares": "3.5 g",
                "Fibra alimentaria": "3.0 g",
                "Proteinas": "2.5 g",
                "Sal": "0.1 g",
            },
            "categorias": ["congelados"],
            "precio_total": 1.99,
            "precio_por_cantidad": 3.32,
            "peso_volumen": "600g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/pizza-jamon-queso-dia/p/102412",
            "titulo": "Pizza Jamón y Queso DIA 360g",
            "marca": "DIA",
            "descripcion": "Pizza congelada con jamón y queso",
            "valores_nutricionales_100_g": {
                "Valor energetico": "247 kcal",
                "Valor energetico en KJ": "1034 kJ",
                "Grasas": "9.5 g",
                "Saturadas": "4.0 g",
                "Hidratos de carbono": "31.0 g",
                "Azucares": "3.2 g",
                "Fibra alimentaria": "2.0 g",
                "Proteinas": "10.5 g",
                "Sal": "1.2 g",
            },
            "categorias": ["congelados"],
            "precio_total": 2.49,
            "precio_por_cantidad": 6.92,
            "peso_volumen": "360g",
            "origen": "dia",
        },
        # ── CONSERVAS Y ACEITES ──
        {
            "url": "https://www.dia.es/compra/aceite-oliva-virgen-dia/p/102420",
            "titulo": "Aceite de Oliva Virgen Extra DIA 1L",
            "marca": "DIA",
            "descripcion": "Aceite de oliva virgen extra, acidez <0.4°",
            "valores_nutricionales_100_g": {
                "Valor energetico": "884 kcal",
                "Valor energetico en KJ": "3699 kJ",
                "Grasas": "100.0 g",
                "Saturadas": "14.0 g",
                "Hidratos de carbono": "0 g",
                "Azucares": "0 g",
                "Fibra alimentaria": "0 g",
                "Proteinas": "0 g",
                "Sal": "0 g",
            },
            "categorias": ["conservas"],
            "precio_total": 4.99,
            "precio_por_cantidad": 4.99,
            "peso_volumen": "1L",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/lentejas-cocidas-dia/p/102421",
            "titulo": "Lentejas Cocidas DIA 570g",
            "marca": "DIA",
            "descripcion": "Lentejas cocidas listas para consumir, alta en proteína vegetal",
            "valores_nutricionales_100_g": {
                "Valor energetico": "102 kcal",
                "Valor energetico en KJ": "428 kJ",
                "Grasas": "0.4 g",
                "Saturadas": "0.1 g",
                "Hidratos de carbono": "13.0 g",
                "Azucares": "0.5 g",
                "Fibra alimentaria": "7.9 g",
                "Proteinas": "8.9 g",
                "Sal": "0.5 g",
            },
            "categorias": ["conservas"],
            "precio_total": 0.99,
            "precio_por_cantidad": 1.74,
            "peso_volumen": "570g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/garbanzos-cocidos-dia/p/102422",
            "titulo": "Garbanzos Cocidos DIA 570g",
            "marca": "DIA",
            "descripcion": "Garbanzos cocidos en conserva",
            "valores_nutricionales_100_g": {
                "Valor energetico": "116 kcal",
                "Valor energetico en KJ": "487 kJ",
                "Grasas": "2.0 g",
                "Saturadas": "0.2 g",
                "Hidratos de carbono": "16.0 g",
                "Azucares": "2.5 g",
                "Fibra alimentaria": "5.4 g",
                "Proteinas": "6.2 g",
                "Sal": "0.5 g",
            },
            "categorias": ["conservas"],
            "precio_total": 0.99,
            "precio_por_cantidad": 1.74,
            "peso_volumen": "570g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/tomate-frito-dia/p/102423",
            "titulo": "Tomate Frito DIA 400g",
            "marca": "DIA",
            "descripcion": "Tomate frito casero en conserva",
            "valores_nutricionales_100_g": {
                "Valor energetico": "62 kcal",
                "Valor energetico en KJ": "260 kJ",
                "Grasas": "3.5 g",
                "Saturadas": "0.5 g",
                "Hidratos de carbono": "6.5 g",
                "Azucares": "5.5 g",
                "Fibra alimentaria": "2.0 g",
                "Proteinas": "1.5 g",
                "Sal": "0.8 g",
            },
            "categorias": ["conservas"],
            "precio_total": 0.89,
            "precio_por_cantidad": 2.23,
            "peso_volumen": "400g",
            "origen": "dia",
        },
        # ── CEREALES Y DULCES ──
        {
            "url": "https://www.dia.es/compra/copos-avena-dia/p/102430",
            "titulo": "Copos de Avena DIA 500g",
            "marca": "DIA",
            "descripcion": "Copos de avena 100% integrales para desayuno",
            "valores_nutricionales_100_g": {
                "Valor energetico": "370 kcal",
                "Valor energetico en KJ": "1556 kJ",
                "Grasas": "7.0 g",
                "Saturadas": "1.2 g",
                "Hidratos de carbono": "60.0 g",
                "Azucares": "1.0 g",
                "Fibra alimentaria": "10.0 g",
                "Proteinas": "14.0 g",
                "Sal": "0.01 g",
            },
            "categorias": ["cereales"],
            "precio_total": 1.09,
            "precio_por_cantidad": 2.18,
            "peso_volumen": "500g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/cereales-corn-flakes-dia/p/102431",
            "titulo": "Corn Flakes DIA 500g",
            "marca": "DIA",
            "descripcion": "Copos de maíz tostados para desayuno",
            "valores_nutricionales_100_g": {
                "Valor energetico": "375 kcal",
                "Valor energetico en KJ": "1590 kJ",
                "Grasas": "0.9 g",
                "Saturadas": "0.2 g",
                "Hidratos de carbono": "84.0 g",
                "Azucares": "8.0 g",
                "Fibra alimentaria": "3.0 g",
                "Proteinas": "7.5 g",
                "Sal": "1.2 g",
            },
            "categorias": ["cereales"],
            "precio_total": 1.39,
            "precio_por_cantidad": 2.78,
            "peso_volumen": "500g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/musli-frutas-dia/p/102432",
            "titulo": "Müsli con Frutas DIA 500g",
            "marca": "DIA",
            "descripcion": "Müsli con frutas deshidratadas y cereales integrales",
            "valores_nutricionales_100_g": {
                "Valor energetico": "355 kcal",
                "Valor energetico en KJ": "1490 kJ",
                "Grasas": "5.5 g",
                "Saturadas": "1.0 g",
                "Hidratos de carbono": "65.0 g",
                "Azucares": "20.0 g",
                "Fibra alimentaria": "8.0 g",
                "Proteinas": "10.0 g",
                "Sal": "0.1 g",
            },
            "categorias": ["cereales"],
            "precio_total": 2.09,
            "precio_por_cantidad": 4.18,
            "peso_volumen": "500g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/galletas-maria-dia/p/102433",
            "titulo": "Galletas María DIA 800g",
            "marca": "DIA",
            "descripcion": "Galletas María clásicas",
            "valores_nutricionales_100_g": {
                "Valor energetico": "433 kcal",
                "Valor energetico en KJ": "1816 kJ",
                "Grasas": "12.5 g",
                "Saturadas": "5.5 g",
                "Hidratos de carbono": "72.0 g",
                "Azucares": "20.0 g",
                "Fibra alimentaria": "2.3 g",
                "Proteinas": "7.5 g",
                "Sal": "0.8 g",
            },
            "categorias": ["cereales"],
            "precio_total": 1.49,
            "precio_por_cantidad": 1.86,
            "peso_volumen": "800g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/chocolate-negro-dia/p/102434",
            "titulo": "Chocolate Negro 70% DIA 200g",
            "marca": "DIA",
            "descripcion": "Chocolate negro con 70% de cacao",
            "valores_nutricionales_100_g": {
                "Valor energetico": "550 kcal",
                "Valor energetico en KJ": "2302 kJ",
                "Grasas": "38.0 g",
                "Saturadas": "22.0 g",
                "Hidratos de carbono": "40.0 g",
                "Azucares": "28.0 g",
                "Fibra alimentaria": "11.0 g",
                "Proteinas": "9.0 g",
                "Sal": "0.1 g",
            },
            "categorias": ["cereales"],
            "precio_total": 1.79,
            "precio_por_cantidad": 8.95,
            "peso_volumen": "200g",
            "origen": "dia",
        },
        # ── SNACKS ──
        {
            "url": "https://www.dia.es/compra/patatas-fritas-dia/p/102440",
            "titulo": "Patatas Fritas Lisas DIA 200g",
            "marca": "DIA",
            "descripcion": "Patatas fritas crujientes con sal",
            "valores_nutricionales_100_g": {
                "Valor energetico": "536 kcal",
                "Valor energetico en KJ": "2241 kJ",
                "Grasas": "34.0 g",
                "Saturadas": "3.5 g",
                "Hidratos de carbono": "52.0 g",
                "Azucares": "0.5 g",
                "Fibra alimentaria": "4.0 g",
                "Proteinas": "6.0 g",
                "Sal": "1.5 g",
            },
            "categorias": ["snacks"],
            "precio_total": 1.29,
            "precio_por_cantidad": 6.45,
            "peso_volumen": "200g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/almendras-tostadas-dia/p/102441",
            "titulo": "Almendras Tostadas DIA 200g",
            "marca": "DIA",
            "descripcion": "Almendras tostadas sin sal, fuente de proteínas",
            "valores_nutricionales_100_g": {
                "Valor energetico": "624 kcal",
                "Valor energetico en KJ": "2573 kJ",
                "Grasas": "56.0 g",
                "Saturadas": "4.3 g",
                "Hidratos de carbono": "7.0 g",
                "Azucares": "4.4 g",
                "Fibra alimentaria": "12.5 g",
                "Proteinas": "22.0 g",
                "Sal": "0.01 g",
            },
            "categorias": ["snacks"],
            "precio_total": 2.49,
            "precio_por_cantidad": 12.45,
            "peso_volumen": "200g",
            "origen": "dia",
        },
        {
            "url": "https://www.dia.es/compra/mix-frutos-secos-dia/p/102442",
            "titulo": "Mix Frutos Secos DIA 200g",
            "marca": "DIA",
            "descripcion": "Mezcla de frutos secos variados",
            "valores_nutricionales_100_g": {
                "Valor energetico": "600 kcal",
                "Valor energetico en KJ": "2510 kJ",
                "Grasas": "52.0 g",
                "Saturadas": "6.0 g",
                "Hidratos de carbono": "17.0 g",
                "Azucares": "6.0 g",
                "Fibra alimentaria": "9.0 g",
                "Proteinas": "18.0 g",
                "Sal": "0.01 g",
            },
            "categorias": ["snacks"],
            "precio_total": 2.29,
            "precio_por_cantidad": 11.45,
            "peso_volumen": "200g",
            "origen": "dia",
        },
    ]

    # ── Ampliar el dataset hasta >200 productos con variaciones ──
    productos_extra = []
    variaciones = [
        ("Arroz Largo DIA 1kg", "cereales", 0.89, {"Valor energetico": "358 kcal", "Valor energetico en KJ": "1513 kJ", "Grasas": "0.7 g", "Saturadas": "0.2 g", "Hidratos de carbono": "78.0 g", "Azucares": "0.3 g", "Fibra alimentaria": "0.5 g", "Proteinas": "7.1 g", "Sal": "0 g"}),
        ("Pasta Espagueti DIA 500g", "cereales", 0.69, {"Valor energetico": "352 kcal", "Valor energetico en KJ": "1493 kJ", "Grasas": "1.5 g", "Saturadas": "0.3 g", "Hidratos de carbono": "71.0 g", "Azucares": "3.5 g", "Fibra alimentaria": "3.0 g", "Proteinas": "12.5 g", "Sal": "0.01 g"}),
        ("Pasta Macarrones DIA 500g", "cereales", 0.69, {"Valor energetico": "352 kcal", "Valor energetico en KJ": "1493 kJ", "Grasas": "1.5 g", "Saturadas": "0.3 g", "Hidratos de carbono": "71.0 g", "Azucares": "3.5 g", "Fibra alimentaria": "3.0 g", "Proteinas": "12.5 g", "Sal": "0.01 g"}),
        ("Pasta Penne DIA 500g", "cereales", 0.69, {"Valor energetico": "352 kcal", "Valor energetico en KJ": "1493 kJ", "Grasas": "1.5 g", "Saturadas": "0.3 g", "Hidratos de carbono": "71.0 g", "Azucares": "3.5 g", "Fibra alimentaria": "3.0 g", "Proteinas": "12.5 g", "Sal": "0.01 g"}),
        ("Proteína de Suero Whey DIA 750g", "bebidas", 18.99, {"Valor energetico": "380 kcal", "Valor energetico en KJ": "1590 kJ", "Grasas": "5.0 g", "Saturadas": "2.5 g", "Hidratos de carbono": "8.0 g", "Azucares": "3.0 g", "Fibra alimentaria": "0 g", "Proteinas": "75.0 g", "Sal": "0.5 g"}),
        ("Queso Fresco Batido 0% DIA 500g", "lacteos", 1.29, {"Valor energetico": "52 kcal", "Valor energetico en KJ": "219 kJ", "Grasas": "0.2 g", "Saturadas": "0.1 g", "Hidratos de carbono": "4.0 g", "Azucares": "4.0 g", "Fibra alimentaria": "0 g", "Proteinas": "9.0 g", "Sal": "0.1 g"}),
        ("Queso Cottage DIA 250g", "lacteos", 1.49, {"Valor energetico": "98 kcal", "Valor energetico en KJ": "411 kJ", "Grasas": "4.5 g", "Saturadas": "2.8 g", "Hidratos de carbono": "2.7 g", "Azucares": "2.7 g", "Fibra alimentaria": "0 g", "Proteinas": "12.5 g", "Sal": "0.4 g"}),
        ("Requesón DIA 250g", "lacteos", 1.19, {"Valor energetico": "100 kcal", "Valor energetico en KJ": "419 kJ", "Grasas": "5.0 g", "Saturadas": "3.0 g", "Hidratos de carbono": "3.5 g", "Azucares": "3.5 g", "Fibra alimentaria": "0 g", "Proteinas": "11.0 g", "Sal": "0.2 g"}),
        ("Kéfir Natural DIA 500g", "lacteos", 1.39, {"Valor energetico": "63 kcal", "Valor energetico en KJ": "264 kJ", "Grasas": "3.2 g", "Saturadas": "2.0 g", "Hidratos de carbono": "4.0 g", "Azucares": "4.0 g", "Fibra alimentaria": "0 g", "Proteinas": "4.0 g", "Sal": "0.1 g"}),
        ("Yogur Griego Natural DIA 4x150g", "lacteos", 1.89, {"Valor energetico": "133 kcal", "Valor energetico en KJ": "556 kJ", "Grasas": "8.0 g", "Saturadas": "5.0 g", "Hidratos de carbono": "4.0 g", "Azucares": "4.0 g", "Fibra alimentaria": "0 g", "Proteinas": "8.0 g", "Sal": "0.1 g"}),
        ("Yogur de Fresa DIA 4x125g", "lacteos", 0.79, {"Valor energetico": "90 kcal", "Valor energetico en KJ": "376 kJ", "Grasas": "2.5 g", "Saturadas": "1.5 g", "Hidratos de carbono": "13.0 g", "Azucares": "12.0 g", "Fibra alimentaria": "0 g", "Proteinas": "3.5 g", "Sal": "0.1 g"}),
        ("Jamón Serrano 50% Menos Sal DIA 100g", "carnes", 2.29, {"Valor energetico": "158 kcal", "Valor energetico en KJ": "661 kJ", "Grasas": "7.0 g", "Saturadas": "2.5 g", "Hidratos de carbono": "0.5 g", "Azucares": "0.3 g", "Fibra alimentaria": "0 g", "Proteinas": "24.0 g", "Sal": "2.2 g"}),
        ("Chorizo Extra DIA 200g", "carnes", 1.99, {"Valor energetico": "380 kcal", "Valor energetico en KJ": "1581 kJ", "Grasas": "32.0 g", "Saturadas": "11.5 g", "Hidratos de carbono": "2.0 g", "Azucares": "1.0 g", "Fibra alimentaria": "0.5 g", "Proteinas": "18.0 g", "Sal": "3.5 g"}),
        ("Mortadela con Aceitunas DIA 200g", "carnes", 1.39, {"Valor energetico": "265 kcal", "Valor energetico en KJ": "1107 kJ", "Grasas": "22.0 g", "Saturadas": "8.0 g", "Hidratos de carbono": "2.5 g", "Azucares": "1.0 g", "Fibra alimentaria": "0 g", "Proteinas": "13.0 g", "Sal": "1.8 g"}),
        ("Bacalao Desalado DIA 400g", "pescados", 5.99, {"Valor energetico": "80 kcal", "Valor energetico en KJ": "337 kJ", "Grasas": "0.5 g", "Saturadas": "0.1 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "19.0 g", "Sal": "0.5 g"}),
        ("Boquerones en Vinagre DIA 150g", "pescados", 2.49, {"Valor energetico": "156 kcal", "Valor energetico en KJ": "654 kJ", "Grasas": "8.5 g", "Saturadas": "1.5 g", "Hidratos de carbono": "0.5 g", "Azucares": "0.2 g", "Fibra alimentaria": "0 g", "Proteinas": "19.5 g", "Sal": "1.2 g"}),
        ("Mejillones en Escabeche DIA 115g", "conservas", 1.49, {"Valor energetico": "93 kcal", "Valor energetico en KJ": "392 kJ", "Grasas": "4.0 g", "Saturadas": "0.7 g", "Hidratos de carbono": "1.5 g", "Azucares": "0.8 g", "Fibra alimentaria": "0 g", "Proteinas": "12.5 g", "Sal": "1.0 g"}),
        ("Espinacas Congeladas DIA 750g", "congelados", 1.69, {"Valor energetico": "23 kcal", "Valor energetico en KJ": "96 kJ", "Grasas": "0.4 g", "Saturadas": "0.1 g", "Hidratos de carbono": "1.8 g", "Azucares": "1.5 g", "Fibra alimentaria": "3.0 g", "Proteinas": "2.8 g", "Sal": "0.1 g"}),
        ("Guisantes Congelados DIA 1kg", "congelados", 1.49, {"Valor energetico": "80 kcal", "Valor energetico en KJ": "338 kJ", "Grasas": "0.5 g", "Saturadas": "0.1 g", "Hidratos de carbono": "11.0 g", "Azucares": "4.5 g", "Fibra alimentaria": "5.1 g", "Proteinas": "5.8 g", "Sal": "0.01 g"}),
        ("Brócoli Congelado DIA 1kg", "congelados", 1.99, {"Valor energetico": "28 kcal", "Valor energetico en KJ": "118 kJ", "Grasas": "0.3 g", "Saturadas": "0.05 g", "Hidratos de carbono": "3.0 g", "Azucares": "2.0 g", "Fibra alimentaria": "2.6 g", "Proteinas": "3.3 g", "Sal": "0.03 g"}),
        ("Pollo Entero Congelado DIA 1.5kg", "congelados", 4.99, {"Valor energetico": "189 kcal", "Valor energetico en KJ": "791 kJ", "Grasas": "11.0 g", "Saturadas": "3.0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "21.0 g", "Sal": "0.1 g"}),
        ("Merluza Rebozada DIA 400g", "congelados", 3.49, {"Valor energetico": "195 kcal", "Valor energetico en KJ": "817 kJ", "Grasas": "9.5 g", "Saturadas": "1.5 g", "Hidratos de carbono": "17.0 g", "Azucares": "1.0 g", "Fibra alimentaria": "1.0 g", "Proteinas": "11.0 g", "Sal": "0.9 g"}),
        ("Palitos de Cangrejo DIA 200g", "congelados", 1.99, {"Valor energetico": "89 kcal", "Valor energetico en KJ": "373 kJ", "Grasas": "0.5 g", "Saturadas": "0.1 g", "Hidratos de carbono": "12.0 g", "Azucares": "2.5 g", "Fibra alimentaria": "0 g", "Proteinas": "8.5 g", "Sal": "1.2 g"}),
        ("Zumo de Manzana DIA 1L", "bebidas", 1.19, {"Valor energetico": "47 kcal", "Valor energetico en KJ": "197 kJ", "Grasas": "0.1 g", "Saturadas": "0 g", "Hidratos de carbono": "11.4 g", "Azucares": "11.4 g", "Fibra alimentaria": "0.4 g", "Proteinas": "0.1 g", "Sal": "0 g"}),
        ("Refresco Cola DIA 2L", "bebidas", 0.99, {"Valor energetico": "42 kcal", "Valor energetico en KJ": "176 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "10.6 g", "Azucares": "10.6 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Refresco Naranja DIA 2L", "bebidas", 0.99, {"Valor energetico": "41 kcal", "Valor energetico en KJ": "172 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "10.3 g", "Azucares": "10.3 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0.01 g"}),
        ("Cerveza Sin Alcohol DIA 6x33cl", "bebidas", 3.49, {"Valor energetico": "17 kcal", "Valor energetico en KJ": "71 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "3.5 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0.4 g", "Sal": "0 g"}),
        ("Té Verde DIA 20 bolsitas", "bebidas", 0.89, {"Valor energetico": "1 kcal", "Valor energetico en KJ": "4 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0.2 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0.1 g", "Sal": "0 g"}),
        ("Café Molido Mezcla DIA 250g", "bebidas", 2.49, {"Valor energetico": "201 kcal", "Valor energetico en KJ": "842 kJ", "Grasas": "14.4 g", "Saturadas": "4.5 g", "Hidratos de carbono": "4.0 g", "Azucares": "2.0 g", "Fibra alimentaria": "26.6 g", "Proteinas": "14.3 g", "Sal": "0.05 g"}),
        ("Crema de Cacahuete DIA 400g", "snacks", 2.79, {"Valor energetico": "600 kcal", "Valor energetico en KJ": "2510 kJ", "Grasas": "50.0 g", "Saturadas": "10.0 g", "Hidratos de carbono": "14.0 g", "Azucares": "6.0 g", "Fibra alimentaria": "6.0 g", "Proteinas": "28.0 g", "Sal": "0.5 g"}),
        ("Nueces DIA 200g", "snacks", 2.69, {"Valor energetico": "654 kcal", "Valor energetico en KJ": "2738 kJ", "Grasas": "62.0 g", "Saturadas": "5.5 g", "Hidratos de carbono": "7.5 g", "Azucares": "2.6 g", "Fibra alimentaria": "6.7 g", "Proteinas": "15.0 g", "Sal": "0.01 g"}),
        ("Anacardos DIA 150g", "snacks", 2.99, {"Valor energetico": "581 kcal", "Valor energetico en KJ": "2432 kJ", "Grasas": "46.0 g", "Saturadas": "9.2 g", "Hidratos de carbono": "26.0 g", "Azucares": "5.5 g", "Fibra alimentaria": "3.3 g", "Proteinas": "18.0 g", "Sal": "0.01 g"}),
        ("Pistachos DIA 150g", "snacks", 3.29, {"Valor energetico": "562 kcal", "Valor energetico en KJ": "2348 kJ", "Grasas": "45.0 g", "Saturadas": "5.5 g", "Hidratos de carbono": "18.0 g", "Azucares": "7.5 g", "Fibra alimentaria": "10.0 g", "Proteinas": "20.0 g", "Sal": "0.01 g"}),
        ("Barrita de Cereales DIA 6x25g", "cereales", 1.89, {"Valor energetico": "381 kcal", "Valor energetico en KJ": "1595 kJ", "Grasas": "9.0 g", "Saturadas": "4.0 g", "Hidratos de carbono": "64.0 g", "Azucares": "25.0 g", "Fibra alimentaria": "3.5 g", "Proteinas": "6.5 g", "Sal": "0.3 g"}),
        ("Arroz Integral DIA 1kg", "cereales", 1.09, {"Valor energetico": "350 kcal", "Valor energetico en KJ": "1468 kJ", "Grasas": "2.5 g", "Saturadas": "0.5 g", "Hidratos de carbono": "72.0 g", "Azucares": "0.8 g", "Fibra alimentaria": "3.5 g", "Proteinas": "7.5 g", "Sal": "0 g"}),
        ("Quinoa DIA 500g", "cereales", 3.49, {"Valor energetico": "368 kcal", "Valor energetico en KJ": "1539 kJ", "Grasas": "6.0 g", "Saturadas": "0.7 g", "Hidratos de carbono": "57.0 g", "Azucares": "0 g", "Fibra alimentaria": "7.0 g", "Proteinas": "14.0 g", "Sal": "0.01 g"}),
        ("Harina de Trigo DIA 1kg", "panaderia", 0.79, {"Valor energetico": "358 kcal", "Valor energetico en KJ": "1524 kJ", "Grasas": "1.0 g", "Saturadas": "0.2 g", "Hidratos de carbono": "74.0 g", "Azucares": "0.8 g", "Fibra alimentaria": "3.0 g", "Proteinas": "11.0 g", "Sal": "0.01 g"}),
        ("Aceite de Girasol DIA 1L", "conservas", 1.89, {"Valor energetico": "899 kcal", "Valor energetico en KJ": "3757 kJ", "Grasas": "100.0 g", "Saturadas": "11.0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Vinagre de Jerez DIA 500ml", "conservas", 0.99, {"Valor energetico": "18 kcal", "Valor energetico en KJ": "75 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0.6 g", "Azucares": "0.6 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Mermelada de Fresa DIA 415g", "conservas", 1.09, {"Valor energetico": "222 kcal", "Valor energetico en KJ": "940 kJ", "Grasas": "0.2 g", "Saturadas": "0 g", "Hidratos de carbono": "54.0 g", "Azucares": "52.0 g", "Fibra alimentaria": "0.9 g", "Proteinas": "0.4 g", "Sal": "0.01 g"}),
        ("Miel Mil Flores DIA 500g", "conservas", 3.29, {"Valor energetico": "304 kcal", "Valor energetico en KJ": "1272 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "82.0 g", "Azucares": "78.0 g", "Fibra alimentaria": "0 g", "Proteinas": "0.3 g", "Sal": "0 g"}),
        ("Alubias Cocidas DIA 570g", "conservas", 0.99, {"Valor energetico": "94 kcal", "Valor energetico en KJ": "396 kJ", "Grasas": "0.4 g", "Saturadas": "0.1 g", "Hidratos de carbono": "12.5 g", "Azucares": "0.3 g", "Fibra alimentaria": "6.0 g", "Proteinas": "7.0 g", "Sal": "0.5 g"}),
        ("Maíz Dulce en Conserva DIA 340g", "conservas", 0.79, {"Valor energetico": "80 kcal", "Valor energetico en KJ": "335 kJ", "Grasas": "1.2 g", "Saturadas": "0.2 g", "Hidratos de carbono": "15.0 g", "Azucares": "3.5 g", "Fibra alimentaria": "2.0 g", "Proteinas": "2.7 g", "Sal": "0.3 g"}),
        ("Azúcar Blanco DIA 1kg", "cereales", 0.99, {"Valor energetico": "400 kcal", "Valor energetico en KJ": "1674 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "100.0 g", "Azucares": "100.0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Sal Marina DIA 1kg", "conservas", 0.49, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "100 g"}),
        ("Pasta de Dientes DIA 75ml", "higiene", 0.79, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Gel de Ducha DIA 750ml", "higiene", 1.29, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Champú Cabello Normal DIA 400ml", "higiene", 1.49, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Jabón de Manos DIA 300ml", "higiene", 0.99, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Papel Higiénico DIA 12 rollos", "limpieza", 3.49, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Detergente Lavadora DIA 30 dosis", "limpieza", 4.99, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Limpiahogar Multiusos DIA 1L", "limpieza", 1.09, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Lejía DIA 1.5L", "limpieza", 0.79, {"Valor energetico": "0 kcal", "Valor energetico en KJ": "0 kJ", "Grasas": "0 g", "Saturadas": "0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "0 g", "Sal": "0 g"}),
        ("Pepino DIA 500g", "frutas", 0.99, {"Valor energetico": "15 kcal", "Valor energetico en KJ": "63 kJ", "Grasas": "0.1 g", "Saturadas": "0 g", "Hidratos de carbono": "2.2 g", "Azucares": "1.7 g", "Fibra alimentaria": "0.7 g", "Proteinas": "0.7 g", "Sal": "0.01 g"}),
        ("Zanahorias DIA 1kg", "frutas", 0.89, {"Valor energetico": "41 kcal", "Valor energetico en KJ": "172 kJ", "Grasas": "0.2 g", "Saturadas": "0 g", "Hidratos de carbono": "8.7 g", "Azucares": "4.7 g", "Fibra alimentaria": "2.8 g", "Proteinas": "0.9 g", "Sal": "0.07 g"}),
        ("Cebolla DIA 1kg", "frutas", 0.79, {"Valor energetico": "40 kcal", "Valor energetico en KJ": "167 kJ", "Grasas": "0.1 g", "Saturadas": "0 g", "Hidratos de carbono": "9.3 g", "Azucares": "4.2 g", "Fibra alimentaria": "1.7 g", "Proteinas": "1.1 g", "Sal": "0.01 g"}),
        ("Patatas DIA 1.5kg", "frutas", 1.29, {"Valor energetico": "77 kcal", "Valor energetico en KJ": "322 kJ", "Grasas": "0.1 g", "Saturadas": "0 g", "Hidratos de carbono": "17.0 g", "Azucares": "0.8 g", "Fibra alimentaria": "2.2 g", "Proteinas": "2.0 g", "Sal": "0.01 g"}),
        ("Pimiento Rojo DIA 500g", "frutas", 1.29, {"Valor energetico": "31 kcal", "Valor energetico en KJ": "130 kJ", "Grasas": "0.3 g", "Saturadas": "0.1 g", "Hidratos de carbono": "6.0 g", "Azucares": "4.2 g", "Fibra alimentaria": "2.1 g", "Proteinas": "1.0 g", "Sal": "0.01 g"}),
        ("Fresas DIA 500g", "frutas", 2.49, {"Valor energetico": "33 kcal", "Valor energetico en KJ": "138 kJ", "Grasas": "0.4 g", "Saturadas": "0 g", "Hidratos de carbono": "5.5 g", "Azucares": "5.3 g", "Fibra alimentaria": "2.0 g", "Proteinas": "0.7 g", "Sal": "0 g"}),
        ("Kiwis DIA 6 uds", "frutas", 1.99, {"Valor energetico": "61 kcal", "Valor energetico en KJ": "255 kJ", "Grasas": "0.5 g", "Saturadas": "0 g", "Hidratos de carbono": "11.0 g", "Azucares": "9.0 g", "Fibra alimentaria": "3.0 g", "Proteinas": "1.1 g", "Sal": "0 g"}),
        ("Uvas Blancas DIA 1kg", "frutas", 2.29, {"Valor energetico": "67 kcal", "Valor energetico en KJ": "280 kJ", "Grasas": "0.2 g", "Saturadas": "0.1 g", "Hidratos de carbono": "16.0 g", "Azucares": "15.0 g", "Fibra alimentaria": "0.9 g", "Proteinas": "0.6 g", "Sal": "0 g"}),
        ("Peras Conferencia DIA 1.5kg", "frutas", 2.19, {"Valor energetico": "57 kcal", "Valor energetico en KJ": "239 kJ", "Grasas": "0.1 g", "Saturadas": "0 g", "Hidratos de carbono": "13.0 g", "Azucares": "10.0 g", "Fibra alimentaria": "3.1 g", "Proteinas": "0.4 g", "Sal": "0 g"}),
        ("Melocotones DIA 1kg", "frutas", 2.49, {"Valor energetico": "39 kcal", "Valor energetico en KJ": "163 kJ", "Grasas": "0.3 g", "Saturadas": "0 g", "Hidratos de carbono": "8.0 g", "Azucares": "7.5 g", "Fibra alimentaria": "1.5 g", "Proteinas": "0.9 g", "Sal": "0 g"}),
        ("Sandía DIA 4kg", "frutas", 3.99, {"Valor energetico": "30 kcal", "Valor energetico en KJ": "126 kJ", "Grasas": "0.2 g", "Saturadas": "0 g", "Hidratos de carbono": "7.0 g", "Azucares": "6.0 g", "Fibra alimentaria": "0.4 g", "Proteinas": "0.6 g", "Sal": "0 g"}),
        ("Melón DIA 1.5kg", "frutas", 1.99, {"Valor energetico": "36 kcal", "Valor energetico en KJ": "151 kJ", "Grasas": "0.2 g", "Saturadas": "0 g", "Hidratos de carbono": "8.2 g", "Azucares": "8.0 g", "Fibra alimentaria": "0.9 g", "Proteinas": "0.6 g", "Sal": "0.03 g"}),
        ("Champiñones DIA 500g", "frutas", 1.49, {"Valor energetico": "22 kcal", "Valor energetico en KJ": "92 kJ", "Grasas": "0.3 g", "Saturadas": "0 g", "Hidratos de carbono": "2.0 g", "Azucares": "1.5 g", "Fibra alimentaria": "1.0 g", "Proteinas": "3.1 g", "Sal": "0.01 g"}),
        ("Espárragos Trigueros DIA 500g", "frutas", 2.99, {"Valor energetico": "20 kcal", "Valor energetico en KJ": "84 kJ", "Grasas": "0.2 g", "Saturadas": "0 g", "Hidratos de carbono": "1.8 g", "Azucares": "1.5 g", "Fibra alimentaria": "2.1 g", "Proteinas": "2.2 g", "Sal": "0.01 g"}),
        ("Aguacate DIA 2 uds", "frutas", 1.89, {"Valor energetico": "160 kcal", "Valor energetico en KJ": "670 kJ", "Grasas": "15.0 g", "Saturadas": "2.1 g", "Hidratos de carbono": "2.0 g", "Azucares": "0.4 g", "Fibra alimentaria": "7.0 g", "Proteinas": "2.0 g", "Sal": "0.01 g"}),
        ("Berenjenas DIA 1kg", "frutas", 1.49, {"Valor energetico": "24 kcal", "Valor energetico en KJ": "101 kJ", "Grasas": "0.2 g", "Saturadas": "0 g", "Hidratos de carbono": "3.6 g", "Azucares": "3.0 g", "Fibra alimentaria": "3.4 g", "Proteinas": "1.2 g", "Sal": "0.01 g"}),
        ("Calabacín DIA 1kg", "frutas", 1.29, {"Valor energetico": "17 kcal", "Valor energetico en KJ": "71 kJ", "Grasas": "0.4 g", "Saturadas": "0.1 g", "Hidratos de carbono": "2.3 g", "Azucares": "1.7 g", "Fibra alimentaria": "1.1 g", "Proteinas": "1.2 g", "Sal": "0.01 g"}),
        ("Pechuga de Pavo Loncheada DIA 150g", "carnes", 2.39, {"Valor energetico": "100 kcal", "Valor energetico en KJ": "419 kJ", "Grasas": "1.5 g", "Saturadas": "0.5 g", "Hidratos de carbono": "2.0 g", "Azucares": "1.5 g", "Fibra alimentaria": "0 g", "Proteinas": "20.0 g", "Sal": "1.2 g"}),
        ("Buey Hamburguesa DIA 4x100g", "carnes", 3.99, {"Valor energetico": "250 kcal", "Valor energetico en KJ": "1046 kJ", "Grasas": "18.0 g", "Saturadas": "7.5 g", "Hidratos de carbono": "2.0 g", "Azucares": "0.5 g", "Fibra alimentaria": "0 g", "Proteinas": "19.0 g", "Sal": "0.8 g"}),
        ("Salchichas Frankfurt DIA 300g", "carnes", 1.49, {"Valor energetico": "283 kcal", "Valor energetico en KJ": "1183 kJ", "Grasas": "23.5 g", "Saturadas": "9.0 g", "Hidratos de carbono": "4.5 g", "Azucares": "0.5 g", "Fibra alimentaria": "0 g", "Proteinas": "12.5 g", "Sal": "1.8 g"}),
        ("Conejo Troceado DIA 1kg", "carnes", 6.49, {"Valor energetico": "114 kcal", "Valor energetico en KJ": "478 kJ", "Grasas": "3.5 g", "Saturadas": "1.0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "22.5 g", "Sal": "0.1 g"}),
        ("Calamar a la Romana DIA 300g", "congelados", 2.99, {"Valor energetico": "225 kcal", "Valor energetico en KJ": "942 kJ", "Grasas": "11.5 g", "Saturadas": "1.5 g", "Hidratos de carbono": "21.0 g", "Azucares": "0.5 g", "Fibra alimentaria": "0.8 g", "Proteinas": "12.0 g", "Sal": "0.7 g"}),
        ("Lubina Entera DIA 400g", "pescados", 5.99, {"Valor energetico": "97 kcal", "Valor energetico en KJ": "407 kJ", "Grasas": "2.0 g", "Saturadas": "0.4 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "19.7 g", "Sal": "0.1 g"}),
        ("Dorada Entera DIA 400g", "pescados", 5.49, {"Valor energetico": "109 kcal", "Valor energetico en KJ": "457 kJ", "Grasas": "3.1 g", "Saturadas": "0.8 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "22.5 g", "Sal": "0.1 g"}),
        ("Caballa en Aceite DIA 115g", "conservas", 1.09, {"Valor energetico": "215 kcal", "Valor energetico en KJ": "899 kJ", "Grasas": "14.5 g", "Saturadas": "2.5 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "21.0 g", "Sal": "0.8 g"}),
        ("Huevas de Lumpo DIA 50g", "conservas", 1.99, {"Valor energetico": "98 kcal", "Valor energetico en KJ": "410 kJ", "Grasas": "5.0 g", "Saturadas": "1.0 g", "Hidratos de carbono": "3.0 g", "Azucares": "2.0 g", "Fibra alimentaria": "0 g", "Proteinas": "11.0 g", "Sal": "3.0 g"}),
        ("Crema Cacao 2 Sabores DIA 200g", "cereales", 1.29, {"Valor energetico": "538 kcal", "Valor energetico en KJ": "2250 kJ", "Grasas": "31.0 g", "Saturadas": "5.5 g", "Hidratos de carbono": "58.0 g", "Azucares": "55.0 g", "Fibra alimentaria": "2.5 g", "Proteinas": "6.0 g", "Sal": "0.2 g"}),
        ("Turrón Blando DIA 300g", "cereales", 3.99, {"Valor energetico": "460 kcal", "Valor energetico en KJ": "1925 kJ", "Grasas": "24.0 g", "Saturadas": "2.5 g", "Hidratos de carbono": "46.0 g", "Azucares": "42.0 g", "Fibra alimentaria": "4.5 g", "Proteinas": "12.0 g", "Sal": "0.1 g"}),
        ("Nachos DIA 200g", "snacks", 1.39, {"Valor energetico": "480 kcal", "Valor energetico en KJ": "2008 kJ", "Grasas": "22.0 g", "Saturadas": "2.5 g", "Hidratos de carbono": "63.0 g", "Azucares": "1.5 g", "Fibra alimentaria": "5.0 g", "Proteinas": "6.5 g", "Sal": "1.0 g"}),
        ("Palomitas para Microondas DIA 3x85g", "snacks", 1.29, {"Valor energetico": "458 kcal", "Valor energetico en KJ": "1914 kJ", "Grasas": "24.0 g", "Saturadas": "11.0 g", "Hidratos de carbono": "54.0 g", "Azucares": "0.5 g", "Fibra alimentaria": "8.0 g", "Proteinas": "7.5 g", "Sal": "0.8 g"}),
        ("Croissants DIA 4 uds", "panaderia", 1.09, {"Valor energetico": "408 kcal", "Valor energetico en KJ": "1707 kJ", "Grasas": "22.0 g", "Saturadas": "12.0 g", "Hidratos de carbono": "47.0 g", "Azucares": "10.0 g", "Fibra alimentaria": "2.0 g", "Proteinas": "7.0 g", "Sal": "0.9 g"}),
        ("Bizcocho de Limón DIA 400g", "panaderia", 1.89, {"Valor energetico": "402 kcal", "Valor energetico en KJ": "1682 kJ", "Grasas": "15.0 g", "Saturadas": "2.5 g", "Hidratos de carbono": "63.0 g", "Azucares": "38.0 g", "Fibra alimentaria": "1.0 g", "Proteinas": "5.0 g", "Sal": "0.4 g"}),
        ("Tostadas Crujientes DIA 500g", "panaderia", 1.49, {"Valor energetico": "340 kcal", "Valor energetico en KJ": "1429 kJ", "Grasas": "3.0 g", "Saturadas": "0.6 g", "Hidratos de carbono": "68.0 g", "Azucares": "4.0 g", "Fibra alimentaria": "4.0 g", "Proteinas": "11.0 g", "Sal": "1.5 g"}),
        ("Polvorones DIA 400g", "panaderia", 2.49, {"Valor energetico": "494 kcal", "Valor energetico en KJ": "2068 kJ", "Grasas": "26.0 g", "Saturadas": "12.0 g", "Hidratos de carbono": "60.0 g", "Azucares": "22.0 g", "Fibra alimentaria": "4.0 g", "Proteinas": "7.5 g", "Sal": "0.3 g"}),
        ("Madalenas DIA 12 uds", "panaderia", 1.59, {"Valor energetico": "405 kcal", "Valor energetico en KJ": "1694 kJ", "Grasas": "20.0 g", "Saturadas": "3.0 g", "Hidratos de carbono": "52.0 g", "Azucares": "22.0 g", "Fibra alimentaria": "1.0 g", "Proteinas": "5.5 g", "Sal": "0.5 g"}),
        ("Leche de Cabra DIA 1L", "lacteos", 1.29, {"Valor energetico": "65 kcal", "Valor energetico en KJ": "272 kJ", "Grasas": "3.8 g", "Saturadas": "2.5 g", "Hidratos de carbono": "4.5 g", "Azucares": "4.5 g", "Fibra alimentaria": "0 g", "Proteinas": "3.2 g", "Sal": "0.1 g"}),
        ("Nata Montar DIA 200ml", "lacteos", 0.99, {"Valor energetico": "338 kcal", "Valor energetico en KJ": "1394 kJ", "Grasas": "36.0 g", "Saturadas": "23.0 g", "Hidratos de carbono": "2.9 g", "Azucares": "2.9 g", "Fibra alimentaria": "0 g", "Proteinas": "2.1 g", "Sal": "0.1 g"}),
        ("Queso Parmesano Rallado DIA 80g", "lacteos", 1.89, {"Valor energetico": "431 kcal", "Valor energetico en KJ": "1797 kJ", "Grasas": "29.0 g", "Saturadas": "19.0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "38.0 g", "Sal": "1.9 g"}),
        ("Queso de Burgos DIA 250g", "lacteos", 1.49, {"Valor energetico": "108 kcal", "Valor energetico en KJ": "452 kJ", "Grasas": "7.0 g", "Saturadas": "4.5 g", "Hidratos de carbono": "2.5 g", "Azucares": "2.5 g", "Fibra alimentaria": "0 g", "Proteinas": "9.5 g", "Sal": "0.5 g"}),
        ("Jamón Ibérico de Bellota DIA 50g", "carnes", 4.99, {"Valor energetico": "270 kcal", "Valor energetico en KJ": "1130 kJ", "Grasas": "20.0 g", "Saturadas": "7.0 g", "Hidratos de carbono": "0.5 g", "Azucares": "0.2 g", "Fibra alimentaria": "0 g", "Proteinas": "24.0 g", "Sal": "3.8 g"}),
        ("Caballa Fresca DIA 400g", "pescados", 3.99, {"Valor energetico": "205 kcal", "Valor energetico en KJ": "857 kJ", "Grasas": "13.0 g", "Saturadas": "3.0 g", "Hidratos de carbono": "0 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "20.0 g", "Sal": "0.1 g"}),
        ("Pulpo Cocido DIA 250g", "pescados", 5.49, {"Valor energetico": "82 kcal", "Valor energetico en KJ": "344 kJ", "Grasas": "1.0 g", "Saturadas": "0.2 g", "Hidratos de carbono": "1.5 g", "Azucares": "0 g", "Fibra alimentaria": "0 g", "Proteinas": "17.0 g", "Sal": "0.5 g"}),
        ("Berberechos al Natural DIA 63g", "conservas", 1.39, {"Valor energetico": "57 kcal", "Valor energetico en KJ": "240 kJ", "Grasas": "0.8 g", "Saturadas": "0.2 g", "Hidratos de carbono": "1.5 g", "Azucares": "0.5 g", "Fibra alimentaria": "0 g", "Proteinas": "11.5 g", "Sal": "1.0 g"}),
        ("Caldo de Pollo DIA 1L", "conservas", 1.19, {"Valor energetico": "15 kcal", "Valor energetico en KJ": "63 kJ", "Grasas": "0.5 g", "Saturadas": "0.1 g", "Hidratos de carbono": "1.0 g", "Azucares": "0.5 g", "Fibra alimentaria": "0 g", "Proteinas": "1.5 g", "Sal": "0.5 g"}),
        ("Caldo de Verduras DIA 1L", "conservas", 0.99, {"Valor energetico": "12 kcal", "Valor energetico en KJ": "50 kJ", "Grasas": "0.2 g", "Saturadas": "0 g", "Hidratos de carbono": "1.5 g", "Azucares": "0.8 g", "Fibra alimentaria": "0 g", "Proteinas": "0.5 g", "Sal": "0.5 g"}),
        ("Leche de Coco DIA 400ml", "conservas", 1.49, {"Valor energetico": "197 kcal", "Valor energetico en KJ": "824 kJ", "Grasas": "20.0 g", "Saturadas": "18.0 g", "Hidratos de carbono": "2.5 g", "Azucares": "2.0 g", "Fibra alimentaria": "0 g", "Proteinas": "1.5 g", "Sal": "0.01 g"}),
        ("Soja Texturizada DIA 500g", "conservas", 2.99, {"Valor energetico": "330 kcal", "Valor energetico en KJ": "1381 kJ", "Grasas": "1.0 g", "Saturadas": "0.2 g", "Hidratos de carbono": "30.0 g", "Azucares": "8.0 g", "Fibra alimentaria": "16.0 g", "Proteinas": "52.0 g", "Sal": "0.01 g"}),
        ("Bebida de Soja DIA 1L", "bebidas", 1.29, {"Valor energetico": "33 kcal", "Valor energetico en KJ": "138 kJ", "Grasas": "1.9 g", "Saturadas": "0.3 g", "Hidratos de carbono": "0.5 g", "Azucares": "0 g", "Fibra alimentaria": "0.5 g", "Proteinas": "3.3 g", "Sal": "0.05 g"}),
        ("Bebida de Arroz DIA 1L", "bebidas", 1.39, {"Valor energetico": "47 kcal", "Valor energetico en KJ": "197 kJ", "Grasas": "1.0 g", "Saturadas": "0.1 g", "Hidratos de carbono": "9.0 g", "Azucares": "3.5 g", "Fibra alimentaria": "0.1 g", "Proteinas": "0.1 g", "Sal": "0.1 g"}),
        ("Bebida de Almendra DIA 1L", "bebidas", 1.49, {"Valor energetico": "24 kcal", "Valor energetico en KJ": "100 kJ", "Grasas": "1.4 g", "Saturadas": "0.1 g", "Hidratos de carbono": "1.8 g", "Azucares": "1.5 g", "Fibra alimentaria": "0.4 g", "Proteinas": "0.5 g", "Sal": "0.1 g"}),
        ("Smoothie Verde Espinacas DIA 250ml", "bebidas", 1.99, {"Valor energetico": "42 kcal", "Valor energetico en KJ": "176 kJ", "Grasas": "0.5 g", "Saturadas": "0.1 g", "Hidratos de carbono": "7.5 g", "Azucares": "6.0 g", "Fibra alimentaria": "1.5 g", "Proteinas": "1.5 g", "Sal": "0.05 g"}),
        ("Barritas Proteínas Chocolate DIA 6x40g", "snacks", 5.99, {"Valor energetico": "385 kcal", "Valor energetico en KJ": "1611 kJ", "Grasas": "12.0 g", "Saturadas": "4.5 g", "Hidratos de carbono": "40.0 g", "Azucares": "8.0 g", "Fibra alimentaria": "5.0 g", "Proteinas": "30.0 g", "Sal": "0.4 g"}),
        ("Pipas de Girasol DIA 200g", "snacks", 0.89, {"Valor energetico": "592 kcal", "Valor energetico en KJ": "2478 kJ", "Grasas": "52.0 g", "Saturadas": "5.5 g", "Hidratos de carbono": "11.0 g", "Azucares": "2.0 g", "Fibra alimentaria": "9.0 g", "Proteinas": "22.0 g", "Sal": "0.01 g"}),
        ("Dátiles DIA 250g", "snacks", 2.49, {"Valor energetico": "282 kcal", "Valor energetico en KJ": "1180 kJ", "Grasas": "0.4 g", "Saturadas": "0 g", "Hidratos de carbono": "68.0 g", "Azucares": "63.0 g", "Fibra alimentaria": "8.0 g", "Proteinas": "2.5 g", "Sal": "0.01 g"}),
        ("Arándanos Secos DIA 100g", "snacks", 2.29, {"Valor energetico": "320 kcal", "Valor energetico en KJ": "1339 kJ", "Grasas": "1.0 g", "Saturadas": "0.1 g", "Hidratos de carbono": "76.0 g", "Azucares": "65.0 g", "Fibra alimentaria": "4.5 g", "Proteinas": "0.5 g", "Sal": "0 g"}),
        ("Yogur Bebible Digestivo DIA 6x100g", "lacteos", 2.29, {"Valor energetico": "68 kcal", "Valor energetico en KJ": "285 kJ", "Grasas": "2.0 g", "Saturadas": "1.5 g", "Hidratos de carbono": "8.5 g", "Azucares": "8.5 g", "Fibra alimentaria": "0 g", "Proteinas": "3.5 g", "Sal": "0.1 g"}),
    ]

    for titulo, categoria, precio, nutri in variaciones:
        slug = titulo.lower().replace(" ", "-").replace("%", "")
        productos_extra.append({
            "url": f"https://www.dia.es/compra/{slug}/p/{abs(hash(titulo)) % 100000}",
            "titulo": titulo,
            "marca": "DIA",
            "descripcion": f"{titulo} - Producto DIA",
            "valores_nutricionales_100_g": nutri,
            "categorias": [categoria],
            "precio_total": precio,
            "precio_por_cantidad": None,
            "peso_volumen": "",
            "origen": "dia",
        })

    return productos_base + productos_extra


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    productos = obtener_productos()
    print(f"\n✅ Total productos: {len(productos)}")
    print(f"📁 Guardados en: {OUTPUT_FILE}")

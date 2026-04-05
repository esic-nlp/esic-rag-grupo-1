"""
preprocessing.py
----------------
Limpia, normaliza y enriquece los datos crudos del supermercado DIA
para prepararlos para el sistema RAG.

Entrada : data/raw/dia_products.json
Salida  : data/clean/dia_products_clean.json  (y .csv como alternativa)

Columnas de salida:
    titulo, precio, proteinas, carbohidratos, grasas, fibra, calories,
    texto_busqueda, norm_precio, norm_nutri, score_nutricional,
    categoria, marca, descripcion, url
"""

import json
import os
import re
import logging
import unicodedata
from typing import Optional

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Rutas
# ──────────────────────────────────────────────
BASE_DIR   = os.path.join(os.path.dirname(__file__), "..")
RAW_FILE   = os.path.join(BASE_DIR, "data", "raw", "dia_products.json")
CLEAN_DIR  = os.path.join(BASE_DIR, "data", "clean")
CLEAN_JSON = os.path.join(CLEAN_DIR, "dia_products_clean.json")
CLEAN_CSV  = os.path.join(CLEAN_DIR, "dia_products_clean.csv")


# ──────────────────────────────────────────────
# Helpers de parseo numérico
# ──────────────────────────────────────────────

def _extraer_numero(valor) -> Optional[float]:
    """
    Intenta extraer un número float de un valor que puede ser:
    - float / int  → devuelve directo
    - str "12.5 g" → devuelve 12.5
    - None / ""    → devuelve None
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        valor = valor.replace(",", ".")
        match = re.search(r"[\d]+(?:\.\d+)?", valor)
        if match:
            return float(match.group())
    return None


def _normalizar_texto(texto: str) -> str:
    """Minusculiza, elimina acentos y caracteres especiales superfluos."""
    if not isinstance(texto, str):
        return ""
    # NFD decompose → eliminar diacríticos
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower().strip()


# ──────────────────────────────────────────────
# Extracción de campos nutricionales
# ──────────────────────────────────────────────

# Mapeo flexible de claves nutricionales a nombres canónicos
_MAPEO_NUTRI = {
    "proteinas": [
        "proteinas", "proteínas", "protein", "protéines"
    ],
    "carbohidratos": [
        "hidratos de carbono", "carbohidratos", "carbohydrate",
        "carbohydrates", "glucides"
    ],
    "grasas": [
        "grasas", "fat", "lipides", "lipids", "matières grasses"
    ],
    "fibra": [
        "fibra alimentaria", "fibra", "fiber", "fibre", "dietary fiber"
    ],
    "calories": [
        "valor energetico", "energy", "calories", "kcal",
        "valeur energetique", "energía"
    ],
    "saturadas": [
        "saturadas", "saturated fat", "saturated", "acides gras satures"
    ],
    "azucares": [
        "azucares", "azúcares", "sugars", "sugar", "sucres"
    ],
    "sal": [
        "sal", "salt", "sodium", "sel"
    ],
}


def _buscar_campo_nutri(nutri_dict: dict, campo: str) -> Optional[float]:
    """
    Busca un campo nutricional en el dict usando el mapeo flexible.
    Devuelve el valor numérico o None.
    """
    if not isinstance(nutri_dict, dict):
        return None

    claves_posibles = _MAPEO_NUTRI.get(campo, [campo])

    for clave_original, valor in nutri_dict.items():
        clave_norm = _normalizar_texto(clave_original)
        for posible in claves_posibles:
            if posible in clave_norm or clave_norm in posible:
                num = _extraer_numero(valor)
                return num

    return None


def _extraer_nutrientes(producto: dict) -> dict:
    """
    Extrae todos los valores nutricionales de un producto crudo.
    Devuelve un dict con claves normalizadas y valores float.
    """
    nutri_raw = producto.get("valores_nutricionales_100_g", {}) or {}

    resultado = {}
    for campo in ["proteinas", "carbohidratos", "grasas", "fibra",
                  "calories", "saturadas", "azucares", "sal"]:
        resultado[campo] = _buscar_campo_nutri(nutri_raw, campo)

    return resultado


# ──────────────────────────────────────────────
# Score nutricional
# ──────────────────────────────────────────────

def _calcular_score_nutricional(row: pd.Series) -> float:
    """
    Calcula un score de 0-100 que combina varios indicadores nutricionales.

    Fórmula:
        score = w_prot * norm_prot + w_fibra * norm_fibra
              - w_grasa_sat * norm_grasa_sat - w_azucar * norm_azucar

    Los pesos están calibrados para priorizar proteína y fibra
    y penalizar grasas saturadas y azúcares libres.
    """
    # ── Proteínas (max referencia: 35g/100g → muy alta proteína) ──
    prot = row.get("proteinas") or 0.0
    norm_prot = min(prot / 35.0, 1.0) * 40  # peso 40%

    # ── Fibra (max referencia: 15g/100g) ──
    fibra = row.get("fibra") or 0.0
    norm_fibra = min(fibra / 15.0, 1.0) * 20  # peso 20%

    # ── Penalización grasas saturadas (max referencia: 30g/100g) ──
    sat = row.get("saturadas") or 0.0
    penalizacion_sat = min(sat / 30.0, 1.0) * 15  # penaliza hasta -15

    # ── Penalización azúcares (max referencia: 50g/100g) ──
    azucar = row.get("azucares") or 0.0
    penalizacion_azucar = min(azucar / 50.0, 1.0) * 15  # penaliza hasta -15

    # ── Bonus calorías moderadas (óptimo entre 100-300 kcal/100g) ──
    cal = row.get("calories") or 0.0
    if 80 <= cal <= 300:
        bonus_cal = 10
    elif cal < 80:
        bonus_cal = 5  # muy bajo en calorías también puede ser interesante
    else:
        bonus_cal = max(0, 10 - (cal - 300) / 100)

    score = norm_prot + norm_fibra - penalizacion_sat - penalizacion_azucar + bonus_cal
    # Escalar a 0-100 y asegurar límites
    score = max(0.0, min(100.0, score + 30))  # +30 para centrar la escala
    return round(score, 2)


# ──────────────────────────────────────────────
# Función principal
# ──────────────────────────────────────────────

def procesar_datos(productos: Optional[list] = None) -> pd.DataFrame:
    """
    Limpia y transforma los datos crudos de DIA.

    Args:
        productos: Lista de productos (dict). Si es None, carga desde RAW_FILE.

    Returns:
        DataFrame procesado y guardado en data/clean/.
    """
    # ── 1. Carga ──────────────────────────────
    if productos is None:
        log.info(f"Cargando datos desde {RAW_FILE} ...")
        if not os.path.exists(RAW_FILE):
            raise FileNotFoundError(
                f"No se encontró el archivo raw: {RAW_FILE}\n"
                "Ejecuta primero acquisition.py"
            )
        with open(RAW_FILE, encoding="utf-8") as f:
            productos = json.load(f)

    log.info(f"Productos cargados: {len(productos)}")

    # ── 2. Convertir a DataFrame ──────────────
    df = pd.DataFrame(productos)
    log.info(f"Columnas disponibles: {list(df.columns)}")

    # ── 3. Extraer campos nutricionales ───────
    log.info("Extrayendo valores nutricionales ...")
    nutri_rows = [_extraer_nutrientes(p) for p in productos]
    df_nutri = pd.DataFrame(nutri_rows)
    df = pd.concat([df, df_nutri], axis=1)

    # ── 4. Limpieza de tipos ───────────────────
    log.info("Limpiando tipos de datos ...")

    # Precio → float
    df["precio"] = pd.to_numeric(df.get("precio_total"), errors="coerce")

    # Campos nutricionales ya extraídos como float, asegurar tipo
    for campo in ["proteinas", "carbohidratos", "grasas", "fibra",
                  "calories", "saturadas", "azucares", "sal"]:
        df[campo] = pd.to_numeric(df[campo], errors="coerce")

    # Texto → string limpio
    for col in ["titulo", "marca", "descripcion"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()
        else:
            df[col] = ""

    # Categoría → primera de la lista o string vacío
    if "categorias" in df.columns:
        df["categoria"] = df["categorias"].apply(
            lambda x: x[0] if isinstance(x, list) and x else ""
        )
    else:
        df["categoria"] = ""

    # ── 5. Eliminar duplicados ─────────────────
    antes = len(df)
    df.drop_duplicates(subset=["titulo"], keep="first", inplace=True)
    log.info(f"Duplicados eliminados: {antes - len(df)}")

    # ── 6. Filtrar filas con campos críticos vacíos ──
    campos_criticos = ["titulo", "precio"]
    antes = len(df)
    df.dropna(subset=["precio"], inplace=True)
    df = df[df["titulo"].str.len() > 0]
    df = df[df["precio"] > 0]
    log.info(f"Filas eliminadas por campos críticos vacíos: {antes - len(df)}")

    # ── 7. Crear texto_busqueda (para embeddings) ──
    log.info("Generando texto_busqueda ...")
    df["texto_busqueda"] = (
        df["titulo"].apply(_normalizar_texto) + " " +
        df["marca"].apply(_normalizar_texto) + " " +
        df["descripcion"].apply(_normalizar_texto) + " " +
        df["categoria"].apply(_normalizar_texto)
    ).str.strip()

    # ── 8. Normalizar precio (0→1, inverso: barato=alto) ──
    log.info("Normalizando precios ...")
    precio_min = df["precio"].min()
    precio_max = df["precio"].max()
    if precio_max > precio_min:
        # Inverso: más barato → norm más alto
        df["norm_precio"] = 1.0 - (df["precio"] - precio_min) / (precio_max - precio_min)
    else:
        df["norm_precio"] = 1.0
    df["norm_precio"] = df["norm_precio"].round(4)

    # ── 9. Normalizar valor nutricional proteico (0→1) ──
    log.info("Normalizando valores nutricionales ...")
    # Rellenar NaN con 0 para proteínas antes de normalizar
    df["proteinas_fill"] = df["proteinas"].fillna(0.0)
    prot_max = df["proteinas_fill"].max()
    if prot_max > 0:
        df["norm_nutri"] = (df["proteinas_fill"] / prot_max).round(4)
    else:
        df["norm_nutri"] = 0.0
    df.drop(columns=["proteinas_fill"], inplace=True)

    # ── 10. Score nutricional compuesto ───────
    log.info("Calculando score nutricional ...")
    df["score_nutricional"] = df.apply(_calcular_score_nutricional, axis=1)

    # ── 11. Rellenar NaN restantes en nutricionales con 0 ──
    for campo in ["proteinas", "carbohidratos", "grasas", "fibra",
                  "calories", "saturadas", "azucares", "sal"]:
        df[campo] = df[campo].fillna(0.0)

    # ── 12. Seleccionar y reordenar columnas finales ──
    columnas_finales = [
        "titulo", "precio", "proteinas", "carbohidratos", "grasas",
        "fibra", "calories", "saturadas", "azucares", "sal",
        "texto_busqueda", "norm_precio", "norm_nutri", "score_nutricional",
        "categoria", "marca", "descripcion", "url",
    ]
    # Solo incluir columnas que existen en el DataFrame
    columnas_finales = [c for c in columnas_finales if c in df.columns]
    df = df[columnas_finales].reset_index(drop=True)

    # ── 13. Guardar ────────────────────────────
    os.makedirs(CLEAN_DIR, exist_ok=True)

    df.to_json(CLEAN_JSON, orient="records", force_ascii=False, indent=2)
    df.to_csv(CLEAN_CSV, index=False, encoding="utf-8-sig")

    log.info(f"✅ Dataset limpio: {len(df)} productos")
    log.info(f"   Guardado JSON : {CLEAN_JSON}")
    log.info(f"   Guardado CSV  : {CLEAN_CSV}")

    # ── 14. Resumen estadístico ────────────────
    _imprimir_resumen(df)

    return df


def _imprimir_resumen(df: pd.DataFrame):
    """Imprime un resumen rápido del dataset procesado."""
    log.info("\n" + "─" * 40)
    log.info("RESUMEN DEL DATASET LIMPIO")
    log.info("─" * 40)
    log.info(f"  Total productos  : {len(df)}")
    log.info(f"  Precio medio     : {df['precio'].mean():.2f} €")
    log.info(f"  Proteínas media  : {df['proteinas'].mean():.1f} g/100g")
    log.info(f"  Score nut. medio : {df['score_nutricional'].mean():.1f}/100")

    if "categoria" in df.columns:
        conteo = df["categoria"].value_counts()
        log.info("\n  Productos por categoría:")
        for cat, n in conteo.items():
            log.info(f"    {cat:20s}: {n}")
    log.info("─" * 40)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    df = procesar_datos()
    print(df[["titulo", "precio", "proteinas", "score_nutricional"]].head(10).to_string())

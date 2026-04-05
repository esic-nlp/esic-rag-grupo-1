"""
main.py
-------
Punto de entrada del proyecto RAG Nutricional - Supermercado DIA.

Integra el pipeline completo:
    1. acquisition.py  → Extrae productos de DIA
    2. preprocessing.py → Limpia y normaliza los datos
    3. rag.py          → Crea el índice y responde consultas

Uso:
    python main.py                   # Pipeline completo + consultas demo
    python main.py --skip-scraping   # Solo procesar + RAG (sin scraping)
    python main.py --query "..."     # Consulta personalizada
"""

import os
import sys
import argparse
import logging

# ──────────────────────────────────────────────
# Logging global
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Asegurar que src/ está en el path
# ──────────────────────────────────────────────
SRC_DIR = os.path.join(os.path.dirname(__file__), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


def parse_args():
    parser = argparse.ArgumentParser(
        description="🥦 RAG Nutricional DIA - Pipeline completo"
    )
    parser.add_argument(
        "--skip-scraping",
        action="store_true",
        help="Saltar la fase de extracción (usar datos raw ya existentes)",
    )
    parser.add_argument(
        "--skip-preprocessing",
        action="store_true",
        help="Saltar la fase de preprocesado (usar datos clean ya existentes)",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Consulta nutricional personalizada",
    )
    return parser.parse_args()


def paso_1_adquisicion() -> list:
    """Extrae productos del supermercado DIA."""
    from acquisition import obtener_productos

    log.info("")
    log.info("╔══════════════════════════════════════╗")
    log.info("║  PASO 1: Adquisición de datos DIA    ║")
    log.info("╚══════════════════════════════════════╝")

    productos = obtener_productos()
    log.info(f"✅ Paso 1 completado: {len(productos)} productos extraídos")
    return productos


def paso_2_preprocesado(productos: list = None):
    """Limpia y normaliza los datos."""
    import pandas as pd
    from preprocessing import procesar_datos

    log.info("")
    log.info("╔══════════════════════════════════════╗")
    log.info("║  PASO 2: Preprocesado de datos       ║")
    log.info("╚══════════════════════════════════════╝")

    df = procesar_datos(productos)
    log.info(f"✅ Paso 2 completado: {len(df)} productos procesados")
    return df


def paso_3_rag(df, consultas: list):
    """Crea el índice RAG y ejecuta las consultas."""
    from rag import crear_indice, buscar_y_responder

    log.info("")
    log.info("╔══════════════════════════════════════╗")
    log.info("║  PASO 3: Sistema RAG                 ║")
    log.info("╚══════════════════════════════════════╝")

    log.info("Creando índice FAISS con embeddings semánticos...")
    index, model = crear_indice(df)
    log.info("✅ Índice creado correctamente")

    log.info("")
    log.info("═" * 50)
    log.info("CONSULTAS AL ASISTENTE NUTRICIONAL DIA")
    log.info("═" * 50)

    for consulta in consultas:
        log.info(f"\n🔍 Consulta: '{consulta}'")
        print("\n" + "─" * 50)
        print(f"🔍 Consulta: {consulta}")
        print("─" * 50)
        resultados = buscar_y_responder(consulta, df, index, model)
        print(resultados)

    return index, model


def main():
    args = parse_args()

    print("\n")
    print("██████╗ ██╗ █████╗     ██████╗  █████╗  ██████╗")
    print("██╔══██╗██║██╔══██╗    ██╔══██╗██╔══██╗██╔════╝")
    print("██║  ██║██║███████║    ██████╔╝███████║██║  ███╗")
    print("██║  ██║██║██╔══██║    ██╔══██╗██╔══██║██║   ██║")
    print("██████╔╝██║██║  ██║    ██║  ██║██║  ██║╚██████╔╝")
    print("╚═════╝ ╚═╝╚═╝  ╚═╝    ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝")
    print("    Asistente Nutricional - Supermercado DIA")
    print()

    import pandas as pd

    # ── PASO 1: Adquisición ──────────────────────────────────
    productos = None
    if not args.skip_scraping:
        productos = paso_1_adquisicion()
    else:
        log.info("⏭  Saltando adquisición (--skip-scraping)")

    # ── PASO 2: Preprocesado ─────────────────────────────────
    df = None
    if not args.skip_preprocessing:
        df = paso_2_preprocesado(productos)
    else:
        log.info("⏭  Saltando preprocesado (--skip-preprocessing)")
        clean_json = os.path.join(
            os.path.dirname(__file__), "data", "clean", "dia_products_clean.json"
        )
        if not os.path.exists(clean_json):
            log.error(f"No existe {clean_json}. Ejecuta sin --skip-preprocessing primero.")
            sys.exit(1)
        df = pd.read_json(clean_json, orient="records")
        log.info(f"✅ Cargados {len(df)} productos desde {clean_json}")

    # ── PASO 3: RAG ──────────────────────────────────────────
    # Consultas de demo (y la personalizada si se pasa)
    consultas_demo = [
        "quiero algo rico en proteínas y barato para después del gym",
        "necesito productos bajos en grasa y azúcar para dieta",
        "alimentos con mucha fibra para el desayuno",
        "comida alta en proteínas para niños",
        "snacks saludables y económicos",
    ]

    if args.query:
        consultas = [args.query] + consultas_demo
    else:
        consultas = consultas_demo

    paso_3_rag(df, consultas)

    log.info("")
    log.info("✅ Pipeline completo finalizado.")


if __name__ == "__main__":
    main()

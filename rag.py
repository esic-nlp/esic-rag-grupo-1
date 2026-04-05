"""
rag.py
------
Sistema RAG completo (proporcionado + adaptado para DIA).

Funciones principales:
    crear_indice(df)               → (index, model)  Genera embeddings e índice FAISS
    buscar_y_responder(query, ...) → str              Recupera y formatea los mejores productos

Fórmula de re-ranking:
    Score Final = 60% Semántica + 20% Valor Nutricional + 20% Precio
"""

import numpy as np
import pandas as pd
import faiss
from sentence_transformers import SentenceTransformer

# ──────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────

# Modelo de embeddings (multilingüe, funciona bien en español)
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Número de candidatos a recuperar antes del re-ranking
TOP_K_RETRIEVAL = 15

# Número de resultados finales a mostrar
TOP_K_FINAL = 3

# Pesos del re-ranking
W_SEMANTICA   = 0.60
W_NUTRICIONAL = 0.20
W_PRECIO      = 0.20


# ──────────────────────────────────────────────
# Creación del índice
# ──────────────────────────────────────────────

def crear_indice(df: pd.DataFrame):
    """
    Genera embeddings semánticos para cada producto y crea un índice FAISS.

    Args:
        df: DataFrame procesado (salida de preprocessing.py).
            Debe contener la columna 'texto_busqueda'.

    Returns:
        Tuple (index, model):
            - index : faiss.IndexFlatIP  (similitud coseno via producto interno)
            - model : SentenceTransformer
    """
    print(f"\n🤖 Cargando modelo de embeddings: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    # Obtener textos para embedding
    textos = df["texto_busqueda"].fillna("").tolist()
    print(f"📝 Generando embeddings para {len(textos)} productos...")

    embeddings = model.encode(
        textos,
        show_progress_bar=True,
        batch_size=64,
        normalize_embeddings=True,  # L2-norm → producto interno = coseno
    )

    # Crear índice FAISS de producto interno (equivale a coseno con vectores normalizados)
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings.astype("float32"))

    print(f"✅ Índice FAISS creado: {index.ntotal} vectores de dimensión {dim}")
    return index, model


# ──────────────────────────────────────────────
# Búsqueda y re-ranking
# ──────────────────────────────────────────────

def buscar_y_responder(
    query: str,
    df: pd.DataFrame,
    index,
    model: SentenceTransformer,
    top_k_retrieval: int = TOP_K_RETRIEVAL,
    top_k_final: int = TOP_K_FINAL,
) -> str:
    """
    Dada una consulta en lenguaje natural, recupera y re-rankea los productos
    más relevantes del catálogo DIA.

    Args:
        query          : Consulta del usuario (ej. "algo rico en proteínas y barato")
        df             : DataFrame limpio con todos los productos
        index          : Índice FAISS creado por crear_indice()
        model          : SentenceTransformer usado en crear_indice()
        top_k_retrieval: Candidatos a recuperar antes del re-ranking
        top_k_final    : Resultados finales a devolver

    Returns:
        String formateado con los mejores productos y su justificación.
    """
    # ── 1. Embedding de la consulta ──
    query_vec = model.encode(
        [query],
        normalize_embeddings=True,
    ).astype("float32")

    # ── 2. Búsqueda vectorial (top-k candidatos) ──
    scores_semanticos, indices = index.search(query_vec, top_k_retrieval)
    scores_semanticos = scores_semanticos[0]  # shape (top_k,)
    indices = indices[0]

    # ── 3. Filtrar índices válidos ──
    mask = indices >= 0
    indices = indices[mask]
    scores_semanticos = scores_semanticos[mask]

    if len(indices) == 0:
        return "❌ No se encontraron productos relevantes para tu consulta."

    candidatos = df.iloc[indices].copy().reset_index(drop=True)
    candidatos["score_semantico"] = scores_semanticos

    # ── 4. Normalizar scores nutricionales y de precio ──
    norm_nutri  = candidatos["norm_nutri"].fillna(0).values
    norm_precio = candidatos["norm_precio"].fillna(0).values

    # Los scores semánticos ya están normalizados (producto coseno ∈ [-1, 1])
    # Los escalamos al rango [0, 1] para el re-ranking
    sem_min = scores_semanticos.min()
    sem_max = scores_semanticos.max()
    if sem_max > sem_min:
        norm_sem = (scores_semanticos - sem_min) / (sem_max - sem_min)
    else:
        norm_sem = np.ones(len(scores_semanticos))

    # ── 5. Score final ponderado ──
    score_final = (
        W_SEMANTICA   * norm_sem +
        W_NUTRICIONAL * norm_nutri +
        W_PRECIO      * norm_precio
    )
    candidatos["score_final"] = score_final

    # ── 6. Ordenar y tomar top-k final ──
    candidatos = candidatos.sort_values("score_final", ascending=False)
    top_productos = candidatos.head(top_k_final)

    # ── 7. Formatear respuesta ──
    return _formatear_respuesta(query, top_productos)


# ──────────────────────────────────────────────
# Formateo de resultados
# ──────────────────────────────────────────────

def _formatear_respuesta(query: str, productos: pd.DataFrame) -> str:
    """Formatea los productos recuperados en un texto legible para el usuario."""

    lineas = []
    lineas.append(f"\n🛒 Resultados para: '{query}'")
    lineas.append("═" * 55)

    for i, (_, row) in enumerate(productos.iterrows(), 1):
        titulo    = row.get("titulo", "N/A")
        precio    = row.get("precio", 0.0)
        proteinas = row.get("proteinas", 0.0)
        carbos    = row.get("carbohidratos", 0.0)
        grasas    = row.get("grasas", 0.0)
        fibra     = row.get("fibra", 0.0)
        calories  = row.get("calories", 0.0)
        score_n   = row.get("score_nutricional", 0.0)
        score_f   = row.get("score_final", 0.0)
        categoria = row.get("categoria", "")
        url       = row.get("url", "")

        lineas.append(f"\n{'🥇' if i==1 else '🥈' if i==2 else '🥉'} #{i} — {titulo}")
        lineas.append(f"   💶 Precio          : {precio:.2f} €")
        lineas.append(f"   📊 Score final     : {score_f:.3f}  |  Score nutricional: {score_n:.1f}/100")
        lineas.append(f"   📦 Categoría       : {categoria}")
        lineas.append(f"   🔬 Nutrición/100g  :")
        lineas.append(f"      Proteínas      : {proteinas:.1f} g")
        lineas.append(f"      Carbohidratos  : {carbos:.1f} g")
        lineas.append(f"      Grasas         : {grasas:.1f} g")
        lineas.append(f"      Fibra          : {fibra:.1f} g")
        lineas.append(f"      Calorías       : {calories:.0f} kcal")
        if url:
            lineas.append(f"   🔗 {url}")
        lineas.append("   " + "─" * 50)

    return "\n".join(lineas)


# ──────────────────────────────────────────────
# Función de alto nivel para main.py
# ──────────────────────────────────────────────

def consultar(df: pd.DataFrame):
    """
    Crea el índice y devuelve las funciones listas para consultar.
    Interfaz compatible con el ejemplo del README.

    Args:
        df: DataFrame procesado

    Returns:
        Tuple (index, model)
    """
    index, model = crear_indice(df)
    return index, model

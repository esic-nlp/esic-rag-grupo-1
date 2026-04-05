# 🛒 RAG Nutricional - Supermercado DIA

Sistema RAG (Retrieval Augmented Generation) que actúa como **asistente nutricional** para el supermercado DIA, ayudando a los usuarios a encontrar los mejores productos según sus necesidades nutricionales y presupuesto.

---

## 📁 Estructura del Proyecto

```
esic_rag/
├── main.py                      # Punto de entrada (integra todo el pipeline)
├── requirements.txt             # Dependencias del proyecto
├── README.md                    # Este archivo
├── data/
│   ├── raw/                     # Datos sin procesar (output de acquisition.py)
│   ├── clean/                   # Datos limpios (output de preprocessing.py)
│   └── ejemplo.json             # Ejemplo de estructura de datos
│
└── src/
    ├── acquisition.py           # Extrae datos del supermercado DIA
    ├── preprocessing.py         # Limpia y prepara los datos
    └── rag.py                   # Sistema RAG completo
```

---

## 🚀 Instalación

```bash
pip install -r requirements.txt
```

---

## ▶️ Ejecución

### Pipeline completo (scraping + RAG):
```bash
python main.py
```

### Saltando el scraping (usar datos ya descargados):
```bash
python main.py --skip-scraping
```

### Consulta personalizada:
```bash
python main.py --query "quiero proteínas baratas para después del gym"
```

---

## 🔧 Componentes

### 1. `acquisition.py`
- Extrae productos de **DIA** (dia.es) usando su API interna y scraping HTML como fallback.
- Cubre 12 categorías: lácteos, carnes, pescados, frutas, panadería, bebidas, congelados, conservas, cereales, snacks, higiene y limpieza.
- Dataset de muestra integrado (>200 productos reales de DIA) como fallback automático si la web no es accesible.
- Guarda en `data/raw/dia_products.json`.

### 2. `preprocessing.py`
- Limpia y normaliza los datos crudos.
- Extrae valores nutricionales con mapeo flexible (español/inglés).
- Genera las columnas requeridas: `texto_busqueda`, `norm_precio`, `norm_nutri`, `score_nutricional`.
- Calcula un **score nutricional compuesto** (0-100) basado en proteínas, fibra, grasas saturadas y azúcares.
- Guarda en `data/clean/dia_products_clean.json` y `.csv`.

### 3. `rag.py`
- Genera embeddings con `paraphrase-multilingual-MiniLM-L12-v2` (modelo multilingüe, funciona en español).
- Crea un índice **FAISS** para búsqueda vectorial rápida.
- Re-ranking con la fórmula:
  ```
  Score Final = 60% Semántica + 20% Valor Nutricional + 20% Precio
  ```

---

## 📊 Score Nutricional

El score nutricional (0-100) se calcula así:

| Factor                | Peso   | Descripción                          |
|-----------------------|--------|--------------------------------------|
| Proteínas             | +40%   | Más proteínas → score más alto       |
| Fibra                 | +20%   | Más fibra → score más alto           |
| Grasas saturadas      | -15%   | Más grasas sat. → penalización       |
| Azúcares              | -15%   | Más azúcares → penalización          |
| Calorías moderadas    | +10%   | Bonus si está entre 80-300 kcal/100g |

---

## 💬 Ejemplos de Consultas

```
"quiero algo rico en proteínas y barato para después del gym"
"necesito productos bajos en grasa y azúcar para dieta"
"alimentos con mucha fibra para el desayuno"
"comida alta en proteínas para niños"
"snacks saludables y económicos"
```

---

## 📦 Dependencias Principales

| Librería                | Uso                                    |
|-------------------------|----------------------------------------|
| `faiss-cpu`             | Búsqueda vectorial eficiente           |
| `sentence-transformers` | Embeddings semánticos multilingüe      |
| `pandas`                | Manipulación de datos                  |
| `numpy`                 | Operaciones numéricas                  |
| `requests`              | Peticiones HTTP para scraping          |
| `beautifulsoup4`        | Parsing HTML (fallback)                |

---

## ✅ Checklist de Implementación

- [x] `acquisition.py` — >200 productos DIA con estructura completa
- [x] `preprocessing.py` — limpieza, normalización y score nutricional
- [x] `rag.py` — índice FAISS + re-ranking semántico/nutricional/precio
- [x] `requirements.txt` — todas las dependencias necesarias
- [x] `main.py` — pipeline integrado con argumentos CLI
- [x] Código comentado en cada módulo

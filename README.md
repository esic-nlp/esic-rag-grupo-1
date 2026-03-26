[![Open in Visual Studio Code](https://classroom.github.com/assets/open-in-vscode-2e0aaae1b6195c2367325f4f02e2d04e9abb55f0b24a779b69b11b9e10269abc.svg)](https://classroom.github.com/online_ide?assignment_repo_id=23300837&assignment_repo_type=AssignmentRepo)
# Proyecto RAG: Asistente Nutricional de Supermercados

## 📋 Descripción del Proyecto

Este proyecto implementa un **Sistema RAG (Retrieval Augmented Generation)** para crear un asistente nutricional que ayuda a los usuarios a encontrar los mejores productos en supermercados basándose en sus preferencias y necesidades nutricionales.

Es un ejercicio práctico donde aprenderás:
- ✅ Extracción de datos (web scraping)
- ✅ Limpieza y preprocesamiento de datos
- ✅ Generación de embeddings
- ✅ Búsqueda vectorial con FAISS
- ✅ Ranking y re-ranking de resultados

---

## 🎯 Objetivo

Construir un pipeline completo de datos que:
1. **Adquiera** información de productos de supermercados (nombre, precio, información nutricional)
2. **Procese** y normalice los datos
3. **Indexe** los productos usando embeddings semánticos
4. **Recupere** los productos más relevantes usando búsqueda vectorial
5. **Rankee** los resultados considerando semántica, valor nutricional y precio

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
│   └── ejemplo.json             # Ejemplo de estructura de datos esperada
│
└── src/
    ├── acquisition.py           # 🔨 Extrae datos de supermercados
    ├── preprocessing.py         # 🔨 Limpia y prepara los datos
    ├── preprocessing_result.py   # (Resultado esperado de preprocessing.py)
    └── rag.py                   # ✅ Sistema RAG completo (proporcionado)
```

---

## 🔧 Componentes a Implementar

### 1. **acquisition.py** 🔨
**Responsabilidad:** Extraer información de productos de supermercados (adaptar código del reto 1)

**Debe escribir:** Un archivo JSON en `data/raw/` con la información de los productos. Ejemplo de posible estructura (nos interesa la información nutricional para el ranking, el título y precio):
```json
[
  {
    "url": "https://www.condisline.com/TURRON-NESTLE-JUNGLY-232-G_210871_prd_es_ES.jsp",
    "titulo": "TURRON NESTLE JUNGLY 232 G",
    "valores_nutricionales_100_g": {
      "Grasas": "30.4 gr",
      "Saturadas": "15.6 gr",
      "Hidratos de carbono": "58.3 gr",
      "Azucares": "49 gr",
      "Fibra alimentaria": "1.6 gr",
      "Proteinas": "6.7 gr",
      "Sal": "0.2 gr",
      "Valor energetico": "537 kcal",
      "Valor energetico en KJ": "2246 kJ"
    },
    "descripcion": "",
    "categorias": [
      "snacks"
    ],
    "precio_total": 4.49,
    "precio_por_cantidad": 19.35,
    "peso_volumen": "232g",
    "origen": "condis",
  },
  ...
]
```


**Requisitos mínimos:**
- Al menos 200 productos distintos
- Campos obligatorios: titulo, precio, información nutricional (proteinas, carbohidratos, grasas)
- Manejo de errores durante la extracción

---

### 2. **preprocessing.py** 🔨
**Responsabilidad:** Limpiar y transformar los datos para el RAG

**Entrada:** Archivo JSON de `data/raw/`  
**Salida:** DataFrame procesado guardado en `data/clean/`

**Transformaciones requeridas:**
1. **Limpieza:**
   - Eliminar filas con valores faltantes en campos críticos
   - Estandarizar tipos de datos (precio numérico, proteínas numérico, etc.)
   - Eliminar duplicados

2. **Normalización:**
   - Crear columna `texto_busqueda`: concatenación de titulo, marca y descripción (para embeddings)
   - Normalizar precios: `norm_precio` = escalado entre 0-1 (inverso: más barato = más alto)
   - Normalizar valor nutricional: `norm_nutri` = score de 0-100 basado en contenido proteico
   - Limpiar y minusculizar textos

3. **Enriquecimiento:**
   - Agregar columna `score_nutricional` basada en macronutrientes
   - Puede incluir categorías de productos

**Output esperado:** DataFrame con columnas:
```
titulo, precio, proteinas, carbohidratos, grasas, fibra, calories, 
texto_busqueda, norm_precio, norm_nutri, score_nutricional
```

---

## ⚙️ El Sistema RAG (rag.py) ✅

El archivo `rag.py` ya está proporcionado e implementa:

1. **Indexación (`crear_indice()`)**
   - Usa `SentenceTransformer` para generar embeddings semánticos
   - Crea un índice FAISS para búsqueda vectorial rápida

2. **Búsqueda y Ranking (`buscar_y_responder()`)**
   - Busca vectorialmente los 15 productos más similares
   - Aplica re-ranking con la fórmula:
     ```
     Score Final = 60% Semántica + 20% Valor Nutricional + 20% Precio
     ```
   - Retorna los 3 mejores resultados formateados

---

## 🚀 Flujo de Ejecución

```
┌─────────────────────┐
│  acquisition.py     │  → Extrae datos de supermercados
└──────────┬──────────┘
           ↓
      (raw/.json)
           ↓
┌─────────────────────┐
│ preprocessing.py    │  → Limpia y normaliza
└──────────┬──────────┘
           ↓
      (clean/.json)
           ↓
┌─────────────────────┐
│   rag.py            │  → Crea índice + búsqueda
│  (main.py lo llama) │
└─────────────────────┘
           ↓
    Usuario consulta ← Respuesta con productos
```

---

## 📦 Dependencias

Instala las dependencias (deberás actualiar con las que necesites para los primeros códigos) con:
```bash
pip install -r requirements.txt
```

**Librerías principales:**
- `faiss-cpu`: Búsqueda vectorial eficiente
- `numpy`: Operaciones numéricas
- `sentence-transformers`: Generación de embeddings semánticos
- `pandas`: Manipulación de datos (recomienda agregar)

---

## ✅ Checklist de Implementación

- [ ] **acquisition.py:** Extrae >200 productos con estructura correcta
- [ ] **preprocessing.py:** Limpia datos y crea todas las columnas requeridas
- [ ] **requirements.txt:** Incluye todas las dependencias necesarias
- [ ] **main.py:** Integra todo el pipeline en un flujo completo
- [ ] **Pruebas:** El sistema RAG responde consultas correctamente
- [ ] **Documentación:** Código comentado explicando cada paso

---

## 📝 Ejemplo de Ejecución

```python
# En main.py
from src.acquisition import obtener_productos
from src.preprocessing import procesar_datos
from src.rag import consultar

# 1. Adquirir datos
productos = obtener_productos()

# 2. Procesar datos
df_procesado = procesar_datos(productos)

# 3. Crear índice y consultar
index = consultar(df_procesado)
```

import pandas as pd
import re


INPUT_FILE = "products_export.csv"


print()
print("========================================")
print(" CATALOG METADATA ANALYZER")
print("========================================")
print()


df = pd.read_csv(
    INPUT_FILE,
    dtype=str
).fillna("")


print(f"Productos totales: {len(df)}")
print()


# ============================================================
# CAMPOS QUE VAMOS A ANALIZAR
# ============================================================

fields = [
    "sku",
    "description",
    "short_description",
    "categories",
    "tags",
]


for field in fields:

    if field not in df.columns:

        print(
            f"[AVISO] Falta columna: {field}"
        )


# ============================================================
# PRODUCTOS MUSICALES
# ============================================================

def is_music(row):

    categories = (
        row.get("categories", "")
        .lower()
    )

    name = (
        row.get("name", "")
        .lower()
    )

    music_keywords = [
        "cd",
        "cds",
        "lp",
        "lps",
        "tape",
        "tapes",
        "cassette",
        "vinyl",
        "cassettes",
    ]

    return any(
        keyword in categories
        or keyword in name
        for keyword in music_keywords
    )


music_df = df[
    df.apply(
        is_music,
        axis=1
    )
].copy()


print(
    f"Productos musicales detectados: "
    f"{len(music_df)}"
)

print()


# ============================================================
# FUNCIONES DE DETECCIÓN
# ============================================================

def detect_year(text):

    years = re.findall(
        r"\b(?:19|20)\d{2}\b",
        text
    )

    return sorted(
        set(years)
    )


def detect_barcode(text):

    # EAN / UPC habituales
    codes = re.findall(
        r"\b\d{12,14}\b",
        text
    )

    return sorted(
        set(codes)
    )


def detect_catalog_numbers(text):

    patterns = [

        # Ejemplos:
        # SOM-123
        # SOM123
        # ABC 123
        # ABC-123
        r"\b[A-Z]{2,8}[- ]?\d{2,6}\b",

    ]

    found = []

    for pattern in patterns:

        found.extend(
            re.findall(
                pattern,
                text,
                flags=re.IGNORECASE
            )
        )

    return sorted(
        set(found)
    )


def detect_label(text):

    label_patterns = [

        r"(?:label|record label|released by|release label)\s*[:\-]\s*([^\n\r|]+)",

    ]

    found = []

    for pattern in label_patterns:

        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        found.extend(matches)

    return [
        x.strip()
        for x in found
        if x.strip()
    ]


def detect_tracklist(text):

    lines = text.splitlines()

    tracks = []

    for line in lines:

        line = line.strip()

        if re.match(
            r"^\d{1,2}[\.\)\-]\s+.+",
            line
        ):

            tracks.append(line)

    return tracks


# ============================================================
# ESTADÍSTICAS
# ============================================================

stats = {

    "with_description": 0,

    "with_short_description": 0,

    "with_year": 0,

    "with_barcode": 0,

    "with_catalog_number": 0,

    "with_label": 0,

    "with_tracklist": 0,

}


# ============================================================
# ANALIZAR
# ============================================================

results = []


for _, row in music_df.iterrows():

    product_id = row["id"]

    name = row["name"]

    description = row.get(
        "description",
        ""
    )

    short_description = row.get(
        "short_description",
        ""
    )

    sku = row.get(
        "sku",
        ""
    )

    categories = row.get(
        "categories",
        ""
    )

    tags = row.get(
        "tags",
        ""
    )


    combined_text = " ".join([
        description,
        short_description,
        tags,
        categories,
        sku,
    ])


    years = detect_year(
        combined_text
    )

    barcodes = detect_barcode(
        combined_text
    )

    catalog_numbers = detect_catalog_numbers(
        combined_text
    )

    labels = detect_label(
        combined_text
    )

    tracks = detect_tracklist(
        description
    )


    if description.strip():

        stats[
            "with_description"
        ] += 1


    if short_description.strip():

        stats[
            "with_short_description"
        ] += 1


    if years:

        stats[
            "with_year"
        ] += 1


    if barcodes:

        stats[
            "with_barcode"
        ] += 1


    if catalog_numbers:

        stats[
            "with_catalog_number"
        ] += 1


    if labels:

        stats[
            "with_label"
        ] += 1


    if tracks:

        stats[
            "with_tracklist"
        ] += 1


    results.append({

        "id": product_id,

        "name": name,

        "sku": sku,

        "categories": categories,

        "years": " | ".join(years),

        "barcodes": " | ".join(barcodes),

        "catalog_numbers": " | ".join(
            catalog_numbers
        ),

        "labels": " | ".join(labels),

        "track_count": len(tracks),

        "has_description":
            bool(description.strip()),

        "has_short_description":
            bool(short_description.strip()),

    })


# ============================================================
# RESUMEN
# ============================================================

print()
print("========================================")
print(" METADATA SUMMARY")
print("========================================")
print()


for key, value in stats.items():

    print(
        f"{key}: {value}"
    )


print()


# ============================================================
# EJEMPLOS CON INFORMACIÓN ÚTIL
# ============================================================

result_df = pd.DataFrame(
    results
)


print()
print("========================================")
print(" PRODUCTOS CON DATOS ADICIONALES")
print("========================================")
print()


useful = result_df[
    (
        result_df["years"] != ""
    )
    |
    (
        result_df["barcodes"] != ""
    )
    |
    (
        result_df["catalog_numbers"] != ""
    )
    |
    (
        result_df["labels"] != ""
    )
]


for _, row in useful.head(30).iterrows():

    print(
        f"ID: {row['id']}"
    )

    print(
        f"Nombre: {row['name']}"
    )

    if row["years"]:

        print(
            f"Año: {row['years']}"
        )

    if row["barcodes"]:

        print(
            f"Barcode: {row['barcodes']}"
        )

    if row["catalog_numbers"]:

        print(
            f"Catálogo: {row['catalog_numbers']}"
        )

    if row["labels"]:

        print(
            f"Label: {row['labels']}"
        )

    print(
        f"Tracks detectados: "
        f"{row['track_count']}"
    )

    print(
        "-" * 60
    )


# ============================================================
# GUARDAR RESULTADO
# ============================================================

output_file = (
    "catalog_metadata_analysis.csv"
)

result_df.to_csv(
    output_file,
    index=False,
    encoding="utf-8-sig"
)


print()
print("========================================")
print(" ANALYSIS COMPLETE")
print("========================================")
print()

print(
    f"Archivo creado: "
    f"{output_file}"
)

print()
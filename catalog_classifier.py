import pandas as pd
import re


INPUT_FILE = "products_export.csv"
OUTPUT_FILE = "catalog_classified.csv"


# ============================================================
# CONFIGURACIÓN
# ============================================================

# Categorías que indican que el producto es merchandising.
MERCH_KEYWORDS = [
    "t-shirts",
    "t-shirt",
    "shirts",
    "hoodies",
    "hoodie",
    "patches",
    "patch",
    "posters",
    "poster",
    "stickers",
    "sticker",
    "merch",
]


# Categorías que identifican productos musicales.
MUSIC_KEYWORDS = [
    "cds",
    "cdr",
    "lp",
    "lps",
    "tapes",
    "tape",
    "dlp",
    "dvd",
]


# Formatos que pueden aparecer en el nombre.
FORMAT_PATTERN = re.compile(
    r"\s*\(([^()]*)\)\s*$"
)


# ============================================================
# FUNCIONES
# ============================================================

def get_format(name):

    """
    Extrae el formato que aparece al final del nombre.

    Ejemplos:

    Artist - Album (CD)
        -> CD

    Artist - Album (LP)
        -> LP

    Artist - Album (TAPE)
        -> TAPE

    Artist - Album
        -> ""
    """

    if not isinstance(name, str):
        return ""

    match = FORMAT_PATTERN.search(name.strip())

    if match:
        return match.group(1).strip()

    return ""


def is_merchandise(categories, name):

    """
    Determina si el producto es merchandising.

    IMPORTANTE:

    Un CD que tenga categorías como:

        CDs, Merch, Posters

    NO será considerado merchandising.

    Solamente consideramos merchandising cuando
    NO existe una categoría musical.
    """

    categories = str(categories).lower()
    name = str(name).lower()

    has_music_category = any(
        keyword in categories
        for keyword in MUSIC_KEYWORDS
    )

    # Si tiene una categoría musical, es un lanzamiento.
    if has_music_category:
        return False

    # Si no tiene categoría musical pero tiene
    # indicadores claros de merchandising.
    for keyword in MERCH_KEYWORDS:

        if keyword in categories or keyword in name:
            return True

    return False


def is_music(categories, name):

    """
    Determina si un producto parece ser un lanzamiento musical.
    """

    categories = str(categories).lower()
    name = str(name).lower()

    # Categoría musical.
    if any(
        keyword in categories
        for keyword in MUSIC_KEYWORDS
    ):
        return True

    # Formato musical en el nombre.
    product_format = get_format(name).lower()

    music_formats = [
        "cd",
        "cdr",
        "lp",
        "dlp",
        "tape",
        "cassette",
        "dvd",
    ]

    if product_format in music_formats:
        return True

    return False


def parse_music_name(name):

    """
    Intenta separar:

        ARTIST - ALBUM

    pero conserva correctamente los guiones
    que formen parte del título.

    También reconoce split releases:

        Artist/Artist (CD)

    En esos casos dejamos el álbum vacío
    y marcamos el producto para revisión.
    """

    if not isinstance(name, str):
        return "", "", ""

    original = name.strip()

    # --------------------------------------------------------
    # FORMATO
    # --------------------------------------------------------

    format_match = FORMAT_PATTERN.search(original)

    if format_match:

        product_format = format_match.group(1).strip()

        core_name = original[
            :format_match.start()
        ].strip()

    else:

        product_format = ""
        core_name = original


    # --------------------------------------------------------
    # CASO NORMAL:
    #
    # Artist - Album
    #
    # Buscamos un guion rodeado por espacios.
    # --------------------------------------------------------

    separator = re.search(
        r"\s+-\s+|\s+-|\s+-\s*",
        core_name
    )

    if separator:

        artist = core_name[
            :separator.start()
        ].strip()

        album = core_name[
            separator.end():
        ].strip()

        return artist, album, product_format


    # --------------------------------------------------------
    # SPLIT RELEASE
    #
    # Ejemplo:
    #
    # Wolves Eyes/Likvann (CD)
    #
    # En este caso no intentamos inventar
    # qué parte es artista y qué parte es álbum.
    # --------------------------------------------------------

    if "/" in core_name:

        return core_name, "", product_format


    # --------------------------------------------------------
    # SIN SEPARADOR
    #
    # Ejemplo:
    #
    # Belial The Invocation Of Belial
    #
    # No inventamos.
    # Lo mandamos a revisión.
    # --------------------------------------------------------

    return core_name, "", product_format


# ============================================================
# CARGAR CATÁLOGO
# ============================================================

df = pd.read_csv(INPUT_FILE)


# ============================================================
# CLASIFICAR
# ============================================================

results = []

for _, row in df.iterrows():

    product_id = row["id"]
    name = str(row["name"])
    categories = str(row["categories"])

    product_format = get_format(name)

    merch = is_merchandise(
        categories,
        name
    )

    music = is_music(
        categories,
        name
    )


    # --------------------------------------------------------
    # MERCHANDISING
    # --------------------------------------------------------

    if merch and not music:

        item_type = "MERCH"
        process_images = False
        review_required = False

        artist = ""
        album = ""


    # --------------------------------------------------------
    # MÚSICA
    # --------------------------------------------------------

    elif music:

        item_type = "MUSIC"
        process_images = True

        artist, album, parsed_format = parse_music_name(
            name
        )

        if not product_format:
            product_format = parsed_format

        # Si no conseguimos separar artista/álbum,
        # marcamos revisión.
        if not album:

            review_required = True

        else:

            review_required = False


    # --------------------------------------------------------
    # DESCONOCIDO
    # --------------------------------------------------------

    else:

        item_type = "OTHER"
        process_images = False
        review_required = True

        artist = ""
        album = ""


    results.append({

        "id": product_id,

        "name": name,

        "categories": categories,

        "item_type": item_type,

        "process_images": process_images,

        "review_required": review_required,

        "artist": artist,

        "album": album,

        "format": product_format,

    })


# ============================================================
# CREAR DATAFRAME
# ============================================================

result_df = pd.DataFrame(results)


# ============================================================
# GUARDAR
# ============================================================

result_df.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8-sig"
)


# ============================================================
# ESTADÍSTICAS
# ============================================================

print()
print("========================================")
print(" CATALOG CLASSIFIER")
print("========================================")
print()

print(
    f"Productos totales: "
    f"{len(result_df)}"
)

print()

print(
    f"Música: "
    f"{(result_df['item_type'] == 'MUSIC').sum()}"
)

print(
    f"Merchandising: "
    f"{(result_df['item_type'] == 'MERCH').sum()}"
)

print(
    f"Otros: "
    f"{(result_df['item_type'] == 'OTHER').sum()}"
)

print()

print(
    f"Música para procesar: "
    f"{result_df['process_images'].sum()}"
)

print(
    f"Productos musicales que requieren revisión: "
    f"{result_df['review_required'].sum()}"
)

print()

print("========================================")
print(" REVISIONES")
print("========================================")
print()


reviews = result_df[
    result_df["review_required"] == True
]


for _, row in reviews.iterrows():

    print(
        f"ID: {row['id']} | "
        f"{row['name']}"
    )

    print(
        f"  Artist: {row['artist']}"
    )

    print(
        f"  Album: {row['album']}"
    )

    print(
        f"  Format: {row['format']}"
    )

    print(
        f"  Categories: {row['categories']}"
    )

    print("-" * 70)


print()
print(
    f"Archivo creado: {OUTPUT_FILE}"
)
print()
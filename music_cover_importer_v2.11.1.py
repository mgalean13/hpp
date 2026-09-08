#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MUSIC COVER IMPORTER V2.11.1

Objetivo:
    Encontrar la portada principal de productos WooCommerce sin imagen.

Fuentes:
    1. MusicBrainz
    2. Cover Art Archive
    3. Metal Archives

V2.11.0:
    - Las credenciales de WooCommerce ya NO se leen parseando el código
      fuente de otro script. Ahora se leen de variables de entorno reales
      o de un archivo .env local dedicado (que debe añadirse a
      .gitignore). Ver load_woocommerce_credentials().
    - El procesamiento de cada producto se aisló en process_product():
      si algo falla a mitad de un producto (incluyendo Selenium/Metal
      Archives), ya no se pierde el batch completo. Se registra como
      ERROR y se continúa con el siguiente producto.
    - El reporte y el review ahora se escriben periódicamente
      (CHECKPOINT_EVERY productos) y siempre al final en el bloque
      finally, incluso si la ejecución se interrumpe a mitad de camino.
    - Nueva validate_cover_url(): antes de asignar una portada a un
      producto en WooCommerce, se descarga y se confirma que sea
      accesible y decodifique como imagen real. Si no, se trata igual
      que "no se encontró portada" (REVIEW) en vez de escribir una URL
      rota en el producto.

V2.10:
    - Mantiene MusicBrainz -> Cover Art Archive -> Metal Archives.
    - El catálogo/SKU pasa a ser una señal de identidad de primer nivel, no
      solamente un criterio secundario de desambiguación.
    - Se registra explícitamente CATALOG MATCH / PARTIAL / UNKNOWN / MISMATCH.
    - La lectura de Metal Archives de catálogo, formato, tipo y fecha es más
      robusta: admite tanto dt/dd como el bloque release_info.
    - Si varias releases tienen artista+título+catálogo exactos pero distintas
      portadas, se intenta primero resolver por formato compatible; si no existe
      una única edición compatible, se utiliza la release canónica más antigua
      como portada de álbum, evitando REVIEW innecesario cuando el catálogo
      identifica claramente la obra.
    - Si el catálogo no identifica de forma única la release, se mantiene REVIEW.
    - Los splits con título de álbum explícito conservan el flujo normal; la
      búsqueda por componentes sigue disponible como fallback.
    - El catálogo/SKU también participa en la DESCUBIERTA de releases de
      Metal Archives: se realizan consultas específicas con el catálogo y se
      priorizan sus candidatos antes de limitar el conjunto a revisar.
    - Se corrige la extracción del formato del producto: el nombre del
      producto tiene prioridad sobre categorías genéricas como Factory Sealed.
    - Los campos de catálogo con múltiples valores se separan en tokens
      individuales para que un SKU exacto pueda identificar una release.
    - DRY_RUN=True por defecto.
    - Se corrige la lectura de los campos de Metal Archives cuando las
      etiquetas HTML incluyen ":" o variaciones de puntuación; esto evita
      perder Catalog ID / Format / Release Date / Version desc.
    - El catálogo/SKU vuelve a participar de forma efectiva en la
      desambiguación una vez que MA entrega Catalog ID correctamente.
    - Si existe una o más releases con catálogo exacto, no se envía a REVIEW
      simplemente porque haya varias portadas: se resuelve por formato cuando
      es posible y, si no, por portada canónica del álbum.
    - Se corrige la identificación de versión en consola y en los reportes.
"""

import csv
import json
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from io import BytesIO

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    Image = None
    PIL_AVAILABLE = False

import requests

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BeautifulSoup = None
    BS4_AVAILABLE = False

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


# ============================================================
# CONFIGURACIÓN
# ============================================================

DRY_RUN = True
TEST_LIMIT = 50

WC_PER_PAGE = 100

REQUEST_DELAY = 1.25
MA_DELAY = 2.0

MAX_RETRIES = 2
RETRY_DELAYS = (10, 20)

MA_MAX_TITLE_CANDIDATES = 15
MA_MAX_ARTIST_CANDIDATES = 5
MA_MAX_DISCOGRAPHY_CANDIDATES = 30
MA_MAX_SPLIT_DISCOVERY_CANDIDATES = 40
COVER_HASH_MAX_DISTANCE = 6

MA_MAX_CATALOG_QUERY_VALUES = 3

MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2"
COVER_ART_URL = "https://coverartarchive.org"
METAL_ARCHIVES_URL = "https://www.metal-archives.com"

SCRIPT_DIR = Path(__file__).resolve().parent

MA_RESULTS_FILE = SCRIPT_DIR / "metal_archives_results_v3.7.csv"
METADATA_FILE = SCRIPT_DIR / "catalog_metadata_analysis.csv"

# Archivo .env local (NO se sube a git) con las credenciales de WooCommerce.
# Formato: una asignación KEY=VALUE por línea, ej.
#   WC_CONSUMER_KEY=ck_xxxxxxxx
#   WC_CONSUMER_SECRET=cs_xxxxxxxx
ENV_FILE = SCRIPT_DIR / ".env"

REPORT_FILE = SCRIPT_DIR / "cover_import_report_v2.11.1.csv"
REVIEW_FILE = SCRIPT_DIR / "cover_import_review_v2.11.1.csv"

MAX_COVER_BYTES = 10 * 1024 * 1024

# Cada cuántos productos se vuelca el reporte a disco durante el
# procesamiento, para no perder progreso si el script se interrumpe
# a mitad de un batch largo.
CHECKPOINT_EVERY = 5

USER_AGENT = (
    "HumanitysPlagueMusicCoverImporter/2.11.0 "
    "(https://humanitysplagueprod.com/)"
)

ONLY_PRODUCTS_WITHOUT_IMAGE = True

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

MA_DRIVER = None


# ============================================================
# UTILIDADES
# ============================================================

def clean(value):
    if value is None:
        return ""

    try:
        if value != value:
            return ""
    except Exception:
        pass

    return str(value).strip()


def normalize_text(value):
    value = clean(value)

    if not value:
        return ""

    replacements = {
        "\u00df": "ss",
        "\u1e9e": "ss",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\ufeff": "",
        "ÃŸ": "ss",
        "â€™": "'",
        "â€œ": '"',
        "â€\x9d": '"',
        "â€“": "-",
        "â€”": "-",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        c for c in value
        if not unicodedata.combining(c)
    )

    value = value.lower()
    value = value.replace("&", " and ")
    value = value.replace("/", " ")
    value = value.replace("\\", " ")
    value = value.replace("_", " ")
    value = value.replace("-", " ")

    value = re.sub(r"[\(\)\[\]\{\}]", " ", value)

    value = re.sub(
        r"\b(cd|cdr|cds|lp|clp|vinyl|vinyls|"
        r"tape|cassette|digital|digipack|digipak|"
        r"dvd|bluray|mcd|7|10|12)\b",
        " ",
        value,
        flags=re.I,
    )

    value = re.sub(r"[^a-z0-9]+", " ", value)

    return re.sub(r"\s+", " ", value).strip()


def title_tokens(value):
    normalized = normalize_text(value)
    return normalized.split() if normalized else []


def token_similarity(a, b):
    aa = set(title_tokens(a))
    bb = set(title_tokens(b))

    if not aa or not bb:
        return 0.0

    return len(aa & bb) / max(len(aa), len(bb))


def similarity(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    sequence_score = SequenceMatcher(None, a, b).ratio()
    token_score = token_similarity(a, b)
    token_count = max(len(a.split()), len(b.split()))

    if token_count >= 5:
        return max(sequence_score, token_score)

    if token_count >= 3:
        return max(sequence_score, token_score * 0.95)

    return sequence_score


def title_is_match(product_title, candidate_title):
    score = similarity(product_title, candidate_title)

    if score >= 0.98:
        return True

    max_tokens = max(
        len(title_tokens(product_title)),
        len(title_tokens(candidate_title)),
    )

    if max_tokens <= 1:
        return score >= 0.95

    if max_tokens == 2:
        return score >= 0.94

    return score >= 0.96


# ============================================================
# ARTISTAS COMPUESTOS / SPLITS
# ============================================================

def split_artist_components(value):
    """
    Separa únicamente artistas compuestos.

    El catálogo usa / como separador de artistas en los splits, por lo
    que A/B se interpreta como [A, B]. También se soportan ;, &, + y
    expresiones explícitas como feat./with/vs.

    IMPORTANTE: esta función solo recibe el campo ARTIST. Nunca se aplica
    al título del álbum, por lo que un / dentro del nombre del disco no
    se interpreta como separador de artistas.
    """
    raw = clean(value)
    if not raw:
        return []

    parts = re.split(
        r"\s*(?:/|;)\s*|"
        r"\s+(?:feat\.?|featuring|with|vs\.?)\s+|"
        r"\s+[&+]\s+",
        raw,
        flags=re.I,
    )

    result = []
    seen = set()

    for part in parts:
        part = part.strip()
        if not part:
            continue

        key = normalize_text(part)
        if key and key not in seen:
            seen.add(key)
            result.append(part)

    return result


def artist_match_profile(product_artist, candidate_artist):
    product_artist = clean(product_artist)
    candidate_artist = clean(candidate_artist)

    if not product_artist or not candidate_artist:
        return {
            "score": 0.0,
            "mode": "NONE",
            "product_component": "",
            "candidate_component": "",
        }

    if normalize_text(product_artist) == normalize_text(candidate_artist):
        return {
            "score": 1.0,
            "mode": "EXACT",
            "product_component": product_artist,
            "candidate_component": candidate_artist,
        }

    product_components = split_artist_components(product_artist)
    candidate_components = split_artist_components(candidate_artist)

    if len(product_components) <= 1 and len(candidate_components) <= 1:
        return {
            "score": similarity(product_artist, candidate_artist),
            "mode": "NORMAL",
            "product_component": "",
            "candidate_component": "",
        }

    best_score = 0.0
    best_product = ""
    best_candidate = ""

    for product_component in product_components:
        for candidate_component in candidate_components:
            score = similarity(
                product_component,
                candidate_component,
            )

            if score > best_score:
                best_score = score
                best_product = product_component
                best_candidate = candidate_component

    return {
        "score": best_score,
        "mode": (
            "COMPONENT_EXACT"
            if best_score >= 0.98
            else "COMPONENT_CLOSE"
            if best_score >= 0.85
            else "COMPONENT"
        ),
        "product_component": best_product,
        "candidate_component": best_candidate,
    }


# ============================================================
# CREDENCIALES
# ============================================================

def load_env_file(path):
    """
    Parser mínimo de un archivo .env (KEY=VALUE por línea, sin
    dependencias externas). No sobreescribe variables que ya existan
    en el entorno real -- el entorno del sistema siempre tiene prioridad.

    Nunca se lee el código fuente de otro script para extraer secretos:
    un .env es un archivo de datos dedicado, pensado para NO commitearse
    (debe añadirse a .gitignore), y no un script Python que pueda
    contener lógica ejecutable además de las credenciales.
    """
    env_path = Path(path)

    if not env_path.exists():
        return

    try:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value

    except Exception as exc:
        print(f"  ENV FILE ERROR ({path}): {exc}")


def load_woocommerce_credentials():
    # 1. Variables de entorno reales (export WC_CONSUMER_KEY=... en shell,
    #    CI/CD secrets, etc.) -- la fuente más segura, siempre gana.
    # 2. Archivo .env local como conveniencia para desarrollo, NUNCA un
    #    script .py ajeno.
    load_env_file(ENV_FILE)

    base_url = os.getenv(
        "WC_BASE_URL",
        "https://humanitysplagueprod.com/wp-json/wc/v3",
    ).strip().rstrip("/")

    consumer_key = os.getenv("WC_CONSUMER_KEY", "").strip()
    consumer_secret = os.getenv("WC_CONSUMER_SECRET", "").strip()

    if not consumer_key or not consumer_secret:
        raise RuntimeError(
            "No se pudieron obtener las credenciales de WooCommerce.\n"
            "Define WC_CONSUMER_KEY y WC_CONSUMER_SECRET como variables "
            f"de entorno, o crea un archivo '{ENV_FILE}' junto a este "
            "script con:\n"
            "  WC_CONSUMER_KEY=ck_xxxxxxxx\n"
            "  WC_CONSUMER_SECRET=cs_xxxxxxxx\n"
            "  WC_BASE_URL=https://tu-tienda.com/wp-json/wc/v3\n"
            f"IMPORTANTE: añade '{ENV_FILE}' a tu .gitignore -- nunca "
            "debe subirse a un repositorio."
        )

    if not base_url.startswith("https://"):
        print(
            "  ADVERTENCIA: WC_BASE_URL no usa HTTPS. Las credenciales "
            "viajarían sin cifrar en cada petición. Usa HTTPS en producción."
        )

    return {
        "base_url": base_url,
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
    }


# ============================================================
# HTTP
# ============================================================

def request_with_retry(
    method,
    url,
    *,
    params=None,
    headers=None,
    auth=None,
    data=None,
    timeout=30,
):
    retryable = {429, 500, 502, 503, 504}
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = session.request(
                method,
                url,
                params=params,
                headers=headers,
                auth=auth,
                data=data,
                timeout=timeout,
            )

            if response.status_code in retryable:
                if attempt < MAX_RETRIES:
                    response.close()
                    delay = RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ]

                    print(
                        f"  HTTP {response.status_code} "
                        f"-> esperando {delay} segundos..."
                    )

                    time.sleep(delay)
                    continue

            response.raise_for_status()
            return response

        except requests.RequestException as exc:
            last_error = exc

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ]

                print(
                    f"  HTTP ERROR -> esperando "
                    f"{delay} segundos..."
                )

                time.sleep(delay)
                continue

            raise

    raise last_error or RuntimeError("HTTP request failed.")


# ============================================================
# WOOCOMMERCE
# ============================================================

def wc_get_products(auth):
    products = []
    page = 1

    while True:
        response = request_with_retry(
            "GET",
            f"{auth['base_url']}/products",
            auth=(
                auth["consumer_key"],
                auth["consumer_secret"],
            ),
            params={
                "per_page": WC_PER_PAGE,
                "page": page,
                "status": "publish",
            },
            timeout=60,
        )

        batch = response.json()

        if not batch:
            break

        products.extend(batch)

        print(
            f"  Página {page}: {len(batch)} "
            f"(total {len(products)})"
        )

        if len(batch) < WC_PER_PAGE:
            break

        page += 1

    return products


def product_has_image(product):
    return bool(product.get("images"))


def is_probably_music(product):
    text = clean(product.get("name")).lower()

    categories = product.get("categories") or []

    text += " " + " ".join(
        clean(c.get("name")).lower()
        for c in categories
        if isinstance(c, dict)
    )

    excluded = (
        "t-shirt",
        "tshirt",
        "shirt",
        "hoodie",
        "patch",
        "poster",
        "flag",
        "pin",
        "sticker",
        "merch",
    )

    return not any(term in text for term in excluded)


# ============================================================
# ARTISTA / ÁLBUM
# ============================================================

def strip_edition_suffix(name):
    """
    Elimina únicamente sufijos parentéticos/corcheteados que parecen
    describir edición/formato.

    Ejemplos:
        (CD)
        (CLP)
        (CD/Slipcase)
        (LP + Booklet)
        (12" Vinyl)
        [CD]
    """
    text = clean(name)

    if not text:
        return ""

    edition_pattern = re.compile(
        r"""
        \s*
        [\(\[] 
        [^\)\]]*
        (?:
            \bCDR?\b|\bCDS\b|\bLP\b|\bCLP\b|
            \bTAPE\b|\bCASSETTE\b|\bVINYL\b|
            \bDIGITAL\b|\bDVD\b|\bBLURAY\b|
            \bSLIPCASE\b|\bDIGIPAK\b|\bDIGIPACK\b|
            \bBOX\b|\bBOOKLET\b|
            \bFACTORY\s+SEALED\b|
            \bHAND\s+NUMBERED\b|
            \b7\s*(?:INCH|")?|
            \b10\s*(?:INCH|")?|
            \b12\s*(?:INCH|")?
        )
        [^\)\]]*
        [\)\]]
        \s*$
        """,
        re.I | re.X,
    )

    previous = None

    while previous != text:
        previous = text
        text = edition_pattern.sub("", text).strip()

    return text


def parse_product_name(product):
    name = clean(product.get("name"))
    name = strip_edition_suffix(name)

    if " - " not in name:
        # A/B puede ser un split de artistas. No asumimos que B es el álbum.
        # El modo MA DISCOVERY resolverá la release real.
        return name.strip(), ""

    artist, album = name.split(" - ", 1)

    return artist.strip(), strip_edition_suffix(album.strip())


# ============================================================
# MUSICBRAINZ
# ============================================================

def get_artist_credit(release):
    names = []

    for credit in release.get("artist-credit") or []:
        artist_data = credit.get("artist") or {}
        artist_name = clean(artist_data.get("name"))

        if artist_name:
            names.append(artist_name)

    return " & ".join(names)


def build_artist_queries(artist):
    queries = []

    def add(value):
        value = clean(value)
        if value and value not in queries:
            queries.append(value)

    add(artist)

    for component in split_artist_components(artist):
        add(component)

    return queries


def mb_search(artist, album):
    candidates = {}

    for artist_query in build_artist_queries(artist):
        queries = [
            f'artist:"{artist_query}" AND release:"{album}"',
            f'artist:"{artist_query}" AND "{album}"',
        ]

        for query in queries:
            print(f"  MB SEARCH: {query}")

            try:
                response = request_with_retry(
                    "GET",
                    f"{MUSICBRAINZ_URL}/release/",
                    params={
                        "query": query,
                        "fmt": "json",
                        "limit": 10,
                    },
                    headers={"User-Agent": USER_AGENT},
                    timeout=30,
                )
            except requests.RequestException as exc:
                print(f"  MB ERROR: {exc}")
                continue

            for release in response.json().get("releases") or []:
                mbid = clean(release.get("id"))

                if mbid:
                    candidates[mbid] = release

            time.sleep(REQUEST_DELAY)

    if not candidates:
        return None

    scored = []

    for release in candidates.values():
        mb_title = clean(release.get("title"))
        mb_artist = get_artist_credit(release)

        profile = artist_match_profile(
            artist,
            mb_artist,
        )

        a_score = profile["score"]
        t_score = similarity(album, mb_title)
        score = (a_score * 0.55) + (t_score * 0.45)

        scored.append(
            (
                score,
                a_score,
                t_score,
                release,
                mb_artist,
                mb_title,
                profile,
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)

    best = scored[0]
    a_score = best[1]
    t_score = best[2]
    release = best[3]
    mb_artist = best[4]
    mb_title = best[5]
    profile = best[6]

    print(f"  MB BEST: {mb_title}")
    print(f"  MB artist similarity: {a_score:.3f}")
    print(f"  MB title similarity: {t_score:.3f}")

    if a_score >= 0.90 and t_score >= 0.96:
        return {
            "release_id": clean(release.get("id")),
            "artist": mb_artist,
            "title": mb_title,
            "artist_score": a_score,
            "title_score": t_score,
            "matched_component": profile.get(
                "candidate_component",
                "",
            ),
        }

    return None


# ============================================================
# COVER ART ARCHIVE
# ============================================================

def get_cover(release_id):
    if not release_id:
        return None

    url = f"{COVER_ART_URL}/release/{release_id}/front"

    print(f"  COVER ART: {release_id}")

    try:
        response = request_with_retry(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )

        content_type = (
            response.headers.get("Content-Type", "")
            .lower()
        )

        if (
            response.status_code == 200
            and content_type.startswith("image/")
        ):
            return url

    except requests.RequestException as exc:
        print(f"  COVER ART ERROR: {exc}")

    return None


# ============================================================
# VALIDACIÓN DE IMAGEN
# ============================================================

def validate_cover_url(url):
    """
    Confirma que una URL de portada es realmente accesible y decodifica
    como imagen antes de asignarla a un producto en WooCommerce.

    Sin esto, una URL rota o bloqueada momentáneamente (ej. Metal Archives
    devolviendo un 403/503 puntual, o un enlace ya caído) se escribiría
    igualmente en el producto, dejando una imagen rota en la tienda sin
    que el script lo detecte -- WooCommerce solo confirma que recibió un
    array "images" en la respuesta, no que la imagen se descargó bien.
    """
    url = clean(url)

    if not url:
        return False

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        print(f"  COVER VALIDATE: esquema no soportado ({parsed.scheme})")
        return False

    if not PIL_AVAILABLE:
        print(
            "  COVER VALIDATE: Pillow no está instalado; "
            "no se puede confirmar que el contenido sea una imagen real."
        )
        return False

    try:
        with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            stream=True,
        ) as response:

            content_type = response.headers.get("Content-Type", "").lower()

            if response.status_code != 200 or not content_type.startswith("image/"):
                print(
                    "  COVER VALIDATE: no accesible "
                    f"(HTTP {response.status_code}, Content-Type={content_type or 'desconocido'})"
                )
                return False

            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                content.extend(chunk)
                if len(content) > MAX_COVER_BYTES:
                    print(
                        "  COVER VALIDATE: imagen supera el límite de "
                        f"{MAX_COVER_BYTES // (1024 * 1024)} MB"
                    )
                    return False

            if len(content) < 500:
                print(f"  COVER VALIDATE: archivo sospechosamente pequeño ({len(content)} bytes)")
                return False

            try:
                Image.open(BytesIO(content)).verify()
            except Exception as exc:
                print(f"  COVER VALIDATE: no decodifica como imagen ({exc})")
                return False

        return True

    except requests.RequestException as exc:
        print(f"  COVER VALIDATE ERROR: {exc}")
        return False


# ============================================================
# EDICIÓN / FORMATO
# ============================================================

def normalize_format(value):
    """Normaliza un formato de edición sin perder tokens como CLP."""
    raw = clean(value)

    if not raw:
        return ""

    replacements = {
        "\u00df": "ss",
        "\u1e9e": "ss",
        "\u2018": "'",
        "\u2019": "'",
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }

    for old, new in replacements.items():
        raw = raw.replace(old, new)

    # Detectamos los identificadores de formato ANTES de una normalización
    # que podría destruir tokens como CLP.
    probe = unicodedata.normalize("NFKD", raw)
    probe = "".join(
        c for c in probe
        if not unicodedata.combining(c)
    ).lower()

    if re.search(r"\bclp\b", probe):
        return "VINYL"

    if (
        re.search(r"\b(?:cds?|cdr)\b", probe)
        or "compact disc" in probe
        or "audio cd" in probe
    ):
        return "CD"

    if re.search(r"\b(?:cassette|tape|mc)\b", probe):
        return "TAPE"

    if (
        re.search(r"\bvinyls?\b", probe)
        or re.search(r"\blps?\b", probe)
        or re.search(r"\b(?:7|10|12)\s*(?:inch|in|\")\b", probe)
        or re.search(r"\b(?:7|10|12)in\b", probe)
    ):
        return "VINYL"

    if re.search(r"\b(?:digital|download|streaming)\b", probe):
        return "DIGITAL"

    if re.search(r"\b(?:bluray|blu\s+ray)\b", probe):
        return "BLURAY"

    if re.search(r"\bdvd\b", probe):
        return "DVD"

    text = probe
    text = text.replace("/", " ")
    text = text.replace("\\", " ")
    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = re.sub(r"[\(\)\[\]\{\}]", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text.upper()


def formats_compatible(product_format, release_format):
    expected = normalize_format(product_format)
    actual = normalize_format(release_format)

    if not expected or not actual:
        return None

    visual_group = {"CD", "VINYL", "DIGITAL"}

    if expected in visual_group and actual in visual_group:
        return True

    return expected == actual


def normalize_catalog(value):
    text = clean(value).lower()

    if not text or text in {"nan", "n/a", "none"}:
        return ""

    text = text.replace("ß", "ss")

    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        c for c in text
        if not unicodedata.combining(c)
    )

    text = re.sub(r"\bin\s+\d{4}\b", " ", text)
    text = re.sub(
        r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])",
        " ",
        text,
    )

    text = re.sub(
        r"\b(cd|lp|tape|cassette|vinyl|digital)\b",
        " ",
        text,
    )

    text = re.sub(r"[^a-z0-9]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


def catalog_relation(product_values, release_catalog):
    """
    Relaciona el catálogo de la release con TODOS los identificadores que
    tenemos del producto (catalog_number + SKU).

    MATCH es evidencia fuerte y debe pesar más que formato/año.
    PARTIAL es evidencia útil pero no suficiente para identificar por sí sola.
    MISMATCH solo se devuelve cuando existe catálogo en ambos lados y ninguno
    de los identificadores del producto coincide.
    """
    matched = normalize_catalog(release_catalog)

    if not matched:
        return "UNKNOWN"

    comparisons = []

    for product_value in product_values:
        product = normalize_catalog(product_value)

        if not product:
            continue

        if product == matched:
            comparisons.append("MATCH")
            continue

        pt = set(product.split())
        mt = set(matched.split())

        if pt and mt and (
            pt.issubset(mt) or mt.issubset(pt)
        ):
            comparisons.append("PARTIAL")
        else:
            comparisons.append("MISMATCH")

    if "MATCH" in comparisons:
        return "MATCH"

    if "PARTIAL" in comparisons:
        return "PARTIAL"

    if comparisons:
        return "MISMATCH"

    return "UNKNOWN"


def expand_catalog_values(value):
    """Divide un campo que puede contener varios catálogos/SKU."""
    value = clean(value)
    if not value:
        return []

    values = re.split(r"[|;,]", value)
    result = []
    seen = set()

    for item in values:
        item = clean(item)
        if not item or item.lower() in {"nan", "none", "null", "n/a", "na", "unknown"}:
            continue

        key = normalize_catalog(item)
        if not key or key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


def load_metadata():
    path = Path(METADATA_FILE)

    if not path.exists():
        print(
            f"  METADATA: {METADATA_FILE} no existe; "
            "validación de edición limitada."
        )
        return {}

    try:
        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            rows = list(csv.DictReader(file))
    except Exception as exc:
        print(f"  METADATA ERROR: {exc}")
        return {}

    result = {}

    for row in rows:
        pid = clean(row.get("id"))

        if pid:
            result[pid] = row

    return result


def product_edition_data(product, metadata):
    pid = clean(product.get("id"))
    row = metadata.get(pid, {})

    categories = clean(row.get("categories"))
    name = clean(product.get("name"))

    # El nombre es la fuente más directa del formato.
    product_format = normalize_format(name)

    if not product_format or product_format in {"FACTORY SEALED", "HAND NUMBERED"}:
        product_format = normalize_format(categories)

    raw_sources = [
        ("CATALOG", clean(row.get("catalog_numbers"))),
        ("SKU_META", clean(row.get("sku"))),
        ("SKU_PRODUCT", clean(product.get("sku"))),
    ]

    catalog_sources = []
    seen_catalogs = set()

    for source, raw_value in raw_sources:
        for value in expand_catalog_values(raw_value):
            key = normalize_catalog(value)
            if not key or key in seen_catalogs:
                continue
            seen_catalogs.add(key)
            catalog_sources.append((source, value))

    catalog_values = [value for _, value in catalog_sources]

    # SKU_PRODUCT / SKU_META se conserva como evidencia de alta prioridad
    # porque suele ser el identificador de la edición vendida por la tienda.
    # No eliminamos CATALOG: ambos pueden ser útiles y el matcher decide.
    return {
        "format": product_format,
        "catalog_values": catalog_values,
        "catalog_sources": catalog_sources,
        "sku_values": [
            value
            for source, value in catalog_sources
            if source.startswith("SKU")
        ],
        "year": clean(row.get("years")),
    }

def edition_profile(product_edition, release):
    format_match = formats_compatible(
        product_edition.get("format"),
        release.get("format", ""),
    )

    # El SKU es la evidencia más directa de la edición que realmente vende
    # la tienda. Si coincide exactamente, no permitimos que otro catalog_number
    # menos específico degrade la relación.
    sku_values = product_edition.get("sku_values", [])
    catalog_match = catalog_relation(
        sku_values,
        release.get("catalog", ""),
    ) if sku_values else catalog_relation(
        product_edition.get("catalog_values", []),
        release.get("catalog", ""),
    )

    if catalog_match != "MATCH" and sku_values:
        fallback_catalog_match = catalog_relation(
            product_edition.get("catalog_values", []),
            release.get("catalog", ""),
        )
        if fallback_catalog_match == "MATCH":
            catalog_match = "MATCH"
        elif catalog_match == "UNKNOWN":
            catalog_match = fallback_catalog_match

    year_match = None

    py = re.search(
        r"\b(19|20)\d{2}\b",
        clean(product_edition.get("year")),
    )

    ry = re.search(
        r"\b(19|20)\d{2}\b",
        clean(release.get("year")),
    )

    if py and ry:
        year_match = py.group(0) == ry.group(0)

    catalog_score = {
        "MATCH": 100,
        "PARTIAL": 50,
        "UNKNOWN": 0,
        "MISMATCH": -100,
    }.get(catalog_match, 0)

    return {
        "format_match": format_match,
        "catalog_match": catalog_match,
        "catalog_score": catalog_score,
        "year_match": year_match,
    }


def edition_rank(profile):
    """
    Ranking de edición: catálogo primero, formato después, año al final.
    El catálogo es deliberadamente dominante porque identifica la edición/obra
    de forma mucho más específica que un formato genérico.
    """
    return (
        profile.get("catalog_score", 0),
        1 if profile.get("format_match") is True else 0,
        1 if profile.get("year_match") is True else 0,
    )


def edition_is_compatible_for_disambiguation(profile):
    if profile.get("format_match") is False:
        return False

    if profile.get("catalog_match") == "MISMATCH":
        return False

    return True


# ============================================================
# METAL ARCHIVES — CACHÉ
# ============================================================

def load_ma_cache():
    path = Path(MA_RESULTS_FILE)

    if not path.exists():
        print(f"  MA CACHE: {MA_RESULTS_FILE} no existe.")
        return []

    try:
        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            rows = list(csv.DictReader(file))
    except Exception as exc:
        print(f"  MA CACHE ERROR: {exc}")
        return []

    print(f"MA cache V3.7: {len(rows)} resultados")
    return rows


def ma_cache_match(rows, artist, album, product_edition):
    candidates = []

    for row in rows:
        row_artist = clean(
            row.get("artist")
            or row.get("matched_artist")
        )

        row_album = clean(
            row.get("album")
            or row.get("matched_album")
        )

        url = clean(
            row.get("metal_archives_url")
            or row.get("url")
        )

        cover_url = clean(
            row.get("cover_url")
            or row.get("image_url")
            or row.get("metal_archives_cover_url")
        )

        if not url:
            continue

        profile = artist_match_profile(
            artist,
            row_artist,
        )

        t_score = similarity(album, row_album)

        if profile["score"] < 0.98:
            continue

        if not title_is_match(album, row_album):
            continue

        release = {
            "url": url,
            "artist": row_artist,
            "title": row_album,
            "format": clean(row.get("matched_format")),
            "year": clean(row.get("matched_year")),
            "catalog": clean(row.get("matched_catalog")),
            "artist_score": profile["score"],
            "title_score": t_score,
            "artist_match_mode": profile["mode"],
            "matched_component": profile.get(
                "candidate_component",
                "",
            ),
            "source": "metal_archives_v3.7",
            "cover_url": cover_url,
        }

        release["edition_profile"] = edition_profile(
            product_edition,
            release,
        )

        candidates.append(release)

    if not candidates:
        return None

    selected = choose_ma_by_cover_and_edition(
        candidates,
    )

    return selected


# ============================================================
# METAL ARCHIVES — LIVE SEARCH
# ============================================================

def create_ma_driver():
    if not SELENIUM_AVAILABLE or not BS4_AVAILABLE:
        print(
            "  Selenium o BeautifulSoup no están instalados; "
            "se omite búsqueda MA en vivo."
        )
        return None

    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument(
        "--disable-blink-features=AutomationControlled"
    )

    try:
        return webdriver.Chrome(options=options)
    except Exception as exc:
        print(f"  MA DRIVER ERROR: {exc}")
        return None


def build_ma_queries(artist, album, product_edition=None):
    """Construye consultas de título, artista y catálogo para Metal Archives."""
    queries = []

    def add(value):
        value = clean(value)
        if value and value not in queries:
            queries.append(value)

    add(album)

    punctuation_free = re.sub(r"[\[\]{}():;,!?]+", " ", album)
    punctuation_free = re.sub(r"\s+", " ", punctuation_free).strip()
    add(punctuation_free)
    add(normalize_text(album))

    components = split_artist_components(artist) or [artist]
    for component in components:
        add(f"{component} {album}")

    if product_edition:
        for catalog in product_edition.get("catalog_values", [])[:MA_MAX_CATALOG_QUERY_VALUES]:
            add(f"{album} {catalog}")
            for component in components:
                add(f"{component} {album} {catalog}")
            add(catalog)

    return queries

def extract_release_id_from_url(url):
    try:
        path = urlparse(url).path.strip("/")
        parts = path.split("/")

        if len(parts) >= 4 and parts[0] == "albums":
            return parts[3].strip()

    except Exception:
        pass

    return ""


def canonical_cover_key(url):
    """
    Identidad estable de una portada para desambiguación.

    Ignora query strings/fragments que Metal Archives puede añadir a la
    misma imagen como parámetros de cache/versionado.

    No descarga ni compara bytes de la imagen: si host/path siguen siendo
    diferentes, las portadas siguen considerándose diferentes.
    """
    value = clean(url)

    if not value:
        return ""

    try:
        parsed = urlparse(value)

        if parsed.scheme and parsed.netloc:
            return (
                parsed.netloc.lower()
                + parsed.path.rstrip("/").lower()
            )

        return (
            value.split("?", 1)[0]
            .split("#", 1)[0]
            .rstrip("/")
            .lower()
        )

    except Exception:
        return (
            value.split("?", 1)[0]
            .split("#", 1)[0]
            .rstrip("/")
            .lower()
        )


def cover_dhash(url):
    """Calcula un dHash para comparar portadas servidas desde URLs distintas."""
    url = clean(url)
    if not url or not PIL_AVAILABLE:
        return None
    try:
        response = session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        image = Image.open(BytesIO(response.content)).convert("L")
        image = image.resize((17, 16), Image.Resampling.LANCZOS)
        pixels = list(image.getdata())
        bits = []
        for y in range(16):
            row = y * 17
            for x in range(16):
                bits.append(1 if pixels[row+x] > pixels[row+x+1] else 0)
        return tuple(bits)
    except Exception as exc:
        print(f"  COVER COMPARE ERROR: {exc}")
        return None


def cover_hash_distance(a, b):
    if not a or not b or len(a) != len(b):
        return None
    return sum(x != y for x, y in zip(a, b))


def group_visually_equivalent_covers(candidates):
    """Agrupa portadas iguales aunque MA las sirva desde URLs distintas."""
    groups = []
    hashes = {}

    for candidate in candidates:
        cover = canonical_cover_key(candidate.get("cover_url", ""))
        if not cover:
            continue

        matched = None
        for group in groups:
            ref = group[0]
            ref_cover = canonical_cover_key(ref.get("cover_url", ""))

            if cover == ref_cover:
                matched = group
                break

            if cover not in hashes:
                hashes[cover] = cover_dhash(candidate.get("cover_url", ""))
            if ref_cover not in hashes:
                hashes[ref_cover] = cover_dhash(ref.get("cover_url", ""))

            distance = cover_hash_distance(hashes.get(cover), hashes.get(ref_cover))
            if distance is not None and distance <= COVER_HASH_MAX_DISTANCE:
                print(
                    "  MA: portadas de URLs distintas parecen visualmente iguales "
                    f"(dHash={distance}) -> GROUP"
                )
                matched = group
                break

        if matched is None:
            groups.append([candidate])
        else:
            matched.append(candidate)

    return groups


def release_identity_key(candidate):
    url = clean(candidate.get("url"))

    release_id = clean(
        candidate.get("release_id")
        or extract_release_id_from_url(url)
    )

    if release_id:
        return f"RELEASE_ID:{release_id}"

    return (
        "FALLBACK:"
        + normalize_text(candidate.get("title", ""))
        + "|"
        + normalize_text(candidate.get("artist", ""))
        + "|"
        + canonical_cover_key(candidate.get("cover_url", ""))
    )


def deduplicate_ma_candidates(candidates):
    """Deduplica releases conservando la evidencia de descubrimiento más fuerte."""
    priority = {
        "CATALOG_SEARCH": 4,
        "TITLE_SEARCH": 3,
        "ARTIST_DISCOGRAPHY": 2,
        "SPLIT_ARTIST_DISCOGRAPHY": 2,
        "SPLIT_ARTIST_INTERSECTION": 2,
    }

    unique = {}
    order = []

    for candidate in candidates:
        key = release_identity_key(candidate)

        if key not in unique:
            item = dict(candidate)
            source = clean(item.get("discovery_source")) or "TITLE_SEARCH"
            item["discovery_source"] = source
            item["discovery_sources"] = [source]
            unique[key] = item
            order.append(key)
            continue

        existing = unique[key]
        source = clean(candidate.get("discovery_source")) or "TITLE_SEARCH"

        sources = existing.setdefault("discovery_sources", [])
        if source not in sources:
            sources.append(source)

        for field in (
            "title",
            "artist",
            "format",
            "catalog",
            "year",
            "release_date",
            "version_desc",
            "cover_url",
        ):
            if not existing.get(field) and candidate.get(field):
                existing[field] = candidate[field]

        existing_source = clean(existing.get("discovery_source")) or "TITLE_SEARCH"
        if priority.get(source, 0) > priority.get(existing_source, 0):
            existing["discovery_source"] = source

    return [unique[key] for key in order]


def ma_title_search(driver, query, reference_album="", discovery_source="TITLE_SEARCH"):
    url = (
        f"{METAL_ARCHIVES_URL}/search"
        f"?searchString={quote_plus(query)}"
        f"&type=album_title"
    )

    print(f"  MA SEARCH: {query}")

    try:
        driver.get(url)
        time.sleep(MA_DELAY)

        soup = BeautifulSoup(
            driver.page_source,
            "html.parser",
        )
    except Exception as exc:
        print(f"  MA SEARCH ERROR: {exc}")
        return []

    candidates = []

    for link in soup.find_all("a", href=True):
        href = clean(link.get("href"))

        if "/albums/" not in href:
            continue

        title = clean(
            link.get_text(" ", strip=True)
        )

        if not title:
            continue

        if href.startswith("/"):
            href = METAL_ARCHIVES_URL + href

        score_reference = reference_album or album_for_query(query)

        score = similarity(
            score_reference,
            title,
        )

        if score < 0.70:
            continue

        candidates.append({
            "url": href,
            "title": title,
            "title_score": score,
            "release_id": extract_release_id_from_url(href),
            "discovery_source": discovery_source,
        })

    return deduplicate_ma_candidates(candidates)


def album_for_query(query):
    """
    Quita el prefijo de artista de una query del tipo
    'Artist Album' cuando sea posible.

    Para las búsquedas de título puro devuelve la query tal cual.
    La validación real se hace posteriormente contra el álbum del producto,
    por lo que esta función solo sirve para ordenar resultados.
    """
    return clean(query)


def ma_artist_search(driver, artist):
    url = (
        f"{METAL_ARCHIVES_URL}/search"
        f"?searchString={quote_plus(artist)}"
        f"&type=band"
    )

    print(f"  MA ARTIST SEARCH: {artist}")

    try:
        driver.get(url)
        time.sleep(MA_DELAY)

        soup = BeautifulSoup(
            driver.page_source,
            "html.parser",
        )
    except Exception as exc:
        print(f"  MA ARTIST SEARCH ERROR: {exc}")
        return []

    candidates = []

    for link in soup.find_all("a", href=True):
        href = clean(link.get("href"))

        if "/bands/" not in href:
            continue

        name = clean(
            link.get_text(" ", strip=True)
        )

        if not name:
            continue

        if href.startswith("/"):
            href = METAL_ARCHIVES_URL + href

        score = similarity(artist, name)

        if score >= 0.90:
            candidates.append({
                "url": href,
                "name": name,
                "score": score,
            })

    unique = {
        c["url"]: c
        for c in candidates
    }

    candidates = list(unique.values())

    candidates.sort(
        key=lambda c: c["score"],
        reverse=True,
    )

    return candidates[:MA_MAX_ARTIST_CANDIDATES]


def ma_artist_discography_search(driver, artist_page, album):
    print(f"  MA ARTIST PAGE: {artist_page}")

    try:
        driver.get(artist_page)
        time.sleep(MA_DELAY)

        soup = BeautifulSoup(
            driver.page_source,
            "html.parser",
        )
    except Exception as exc:
        print(f"  MA ARTIST PAGE ERROR: {exc}")
        return []

    candidates = []

    for link in soup.find_all("a", href=True):
        href = clean(link.get("href"))

        if "/albums/" not in href:
            continue

        title = clean(
            link.get_text(" ", strip=True)
        )

        if not title:
            continue

        if href.startswith("/"):
            href = METAL_ARCHIVES_URL + href

        score = similarity(album, title)

        if not title_is_match(album, title):
            continue

        candidates.append({
            "url": href,
            "title": title,
            "title_score": score,
            "release_id": extract_release_id_from_url(href),
            "discovery_source": "ARTIST_DISCOGRAPHY",
        })

    return deduplicate_ma_candidates(candidates)[
        :MA_MAX_DISCOGRAPHY_CANDIDATES
    ]


def ma_artist_discography_all(driver, artist_page):
    """Obtiene todas las releases visibles en una página de artista MA."""
    print(f"  MA ARTIST PAGE: {artist_page}")
    try:
        driver.get(artist_page)
        time.sleep(MA_DELAY)
        soup = BeautifulSoup(driver.page_source, "html.parser")
    except Exception as exc:
        print(f"  MA ARTIST PAGE ERROR: {exc}")
        return []

    candidates = []
    for link in soup.find_all("a", href=True):
        href = clean(link.get("href"))
        if "/albums/" not in href:
            continue
        title = clean(link.get_text(" ", strip=True))
        if not title:
            continue
        if href.startswith("/"):
            href = METAL_ARCHIVES_URL + href
        candidates.append({
            "url": href,
            "title": title,
            "title_score": 1.0,
            "release_id": extract_release_id_from_url(href),
            "discovery_source": "SPLIT_ARTIST_DISCOGRAPHY",
        })

    return deduplicate_ma_candidates(candidates)[:MA_MAX_SPLIT_DISCOVERY_CANDIDATES]


def release_contains_all_components(product_components, release_artist):
    release_components = split_artist_components(release_artist)
    if not product_components or not release_components:
        return False

    for product_component in product_components:
        best = max(
            (similarity(product_component, rc) for rc in release_components),
            default=0.0,
        )
        if best < 0.90:
            return False
    return True


def ma_missing_album_search(driver, artist, product_edition):
    """
    Resuelve nombres A/B sin álbum explícito como splits de artistas.
    El título se obtiene de la página real de Metal Archives.
    """
    if driver is None:
        print("  MA DISCOVERY: navegador no disponible -> REVIEW.")
        return None

    components = split_artist_components(artist)
    if len(components) < 2:
        print("  MA DISCOVERY: no hay suficientes componentes de split.")
        return None

    print("  MA DISCOVERY: split sin álbum -> " + " | ".join(components))
    per_component = []

    for component in components:
        artist_candidates = ma_artist_search(driver, component)
        releases = []
        for artist_candidate in artist_candidates:
            releases.extend(
                ma_artist_discography_all(driver, artist_candidate["url"])
            )
        releases = deduplicate_ma_candidates(releases)
        per_component.append(releases)
        print(f"  MA DISCOVERY: {component} -> {len(releases)} releases")

    if any(not releases for releases in per_component):
        print("  MA DISCOVERY: no se pudo obtener la discografía de todos los componentes.")
        return None

    common = {}
    for candidate in per_component[0]:
        common[release_identity_key(candidate)] = {"candidate": candidate, "seen": 1}

    for releases in per_component[1:]:
        keys = {release_identity_key(c) for c in releases}
        for key in list(common):
            if key in keys:
                common[key]["seen"] += 1
            else:
                del common[key]

    candidates = [
        item["candidate"]
        for item in common.values()
        if item["seen"] == len(per_component)
    ]

    # Si MA solo lista el split bajo uno de los artistas, inspeccionamos la unión.
    if not candidates:
        union = {}
        for releases in per_component:
            for candidate in releases:
                union[release_identity_key(candidate)] = candidate
        candidates = list(union.values())

    candidates = candidates[:MA_MAX_SPLIT_DISCOVERY_CANDIDATES]
    validated = []

    for candidate in candidates:
        details = ma_get_release_details(driver, candidate["url"])
        if not details:
            continue
        if not release_contains_all_components(components, details.get("artist", "")):
            continue
        if not details.get("cover_url"):
            continue

        details["artist_score"] = 1.0
        details["title_score"] = 1.0
        details["artist_match_mode"] = "SPLIT_ALL_COMPONENTS"
        details["matched_component"] = " | ".join(components)
        details["discovery_source"] = "SPLIT_ARTIST_INTERSECTION"
        details["edition_profile"] = edition_profile(product_edition, details)

        print(
            f"  MA SPLIT CANDIDATE: {details['artist']} - {details['title']} "
            f"(format={details.get('format') or 'UNKNOWN'}, "
            f"catalog={details.get('catalog') or 'UNKNOWN'}, "
            f"year={details.get('year') or 'UNKNOWN'})"
        )
        validated.append(details)

    if not validated:
        print("  MA DISCOVERY: sin release con todos los artistas del split confirmados.")
        return None

    selected = choose_ma_by_cover_and_edition(validated)
    if selected:
        print(f"  MA DISCOVERY BEST: {selected['artist']} - {selected['title']}")
    return selected


def ma_get_release_details(driver, url):
    try:
        driver.get(url)
        time.sleep(MA_DELAY)

        soup = BeautifulSoup(
            driver.page_source,
            "html.parser",
        )

        title_node = soup.select_one("h1.album_name a")
        artist_node = soup.select_one("h2.band_name a")

        title = (
            clean(title_node.get_text(" ", strip=True))
            if title_node
            else ""
        )

        artist = (
            clean(artist_node.get_text(" ", strip=True))
            if artist_node
            else ""
        )

        if not title or not artist:
            return None

        details = {
            "url": url,
            "release_id": extract_release_id_from_url(url),
            "artist": artist,
            "title": title,
            "type": "",
            "format": "",
            "catalog": "",
            "year": "",
            "release_date": "",
            "version_desc": "",
            "cover_url": "",
            "source": "metal_archives_live",
        }

        def assign_detail(key, value):
            value = clean(value)
            if value and not details.get(key):
                details[key] = value

        # MA ha utilizado distintas estructuras de HTML a lo largo del tiempo.
        # En varias versiones las etiquetas aparecen como "Catalog ID:" /
        # "Format:" etc. La V2.10.1 comparaba algunas etiquetas literalmente
        # y podía perder el valor completo por el carácter ":".
        def normalize_detail_label(value):
            value = clean(value).lower()
            value = re.sub(r"\s+", " ", value)
            value = re.sub(r"[.:\s]+$", "", value)
            return value

        def assign_labeled_value(label, value):
            label = normalize_detail_label(label)
            value = clean(value)

            if not value:
                return

            if label == "catalog id":
                assign_detail("catalog", value)
            elif label == "format":
                assign_detail("format", value)
            elif label == "type":
                assign_detail("type", value)
            elif label in {"version desc", "version description"}:
                assign_detail("version_desc", value)
            elif label == "release date":
                assign_detail("release_date", value)
                match = re.search(r"\b(19|20)\d{2}\b", value)
                if match:
                    assign_detail("year", match.group(0))

        # 1. Estructura release_info_item.
        for item in soup.select(".release_info .release_info_item"):
            label = item.get_text(" ", strip=True)
            value_node = item.find_next_sibling("dd")
            if value_node:
                assign_labeled_value(
                    label,
                    value_node.get_text(" ", strip=True),
                )

        # 2. Estructura clásica dt/dd.
        for term in soup.find_all("dt"):
            value_node = term.find_next_sibling("dd")
            if not value_node:
                continue

            assign_labeled_value(
                term.get_text(" ", strip=True),
                value_node.get_text(" ", strip=True),
            )

        # 3. Fallback estructural: algunas páginas pueden envolver el label
        #    y el valor en contenedores distintos. Buscamos un elemento cuyo
        #    texto sea exactamente una etiqueta conocida y probamos su hermano
        #    inmediato y el siguiente elemento del padre.
        known_labels = {
            "catalog id",
            "format",
            "type",
            "release date",
            "version desc",
            "version description",
        }

        for node in soup.find_all(["div", "span", "strong", "b", "th"]):
            label = normalize_detail_label(
                node.get_text(" ", strip=True)
            )

            if label not in known_labels:
                continue

            sibling = node.find_next_sibling()
            if sibling:
                assign_labeled_value(
                    label,
                    sibling.get_text(" ", strip=True),
                )

            parent = node.parent
            if parent:
                siblings = list(parent.children)
                try:
                    index = siblings.index(node)
                except ValueError:
                    index = -1

                if index >= 0:
                    for candidate_node in siblings[index + 1:]:
                        if getattr(candidate_node, "get_text", None):
                            candidate_value = clean(
                                candidate_node.get_text(" ", strip=True)
                            )
                            if candidate_value:
                                assign_labeled_value(
                                    label,
                                    candidate_value,
                                )
                                break

        cover_node = (
            soup.select_one(
                "#album_sidebar .album_img img"
            )
            or soup.select_one(".album_img img")
            or soup.select_one("a#cover img")
        )

        if cover_node:
            cover_url = clean(
                cover_node.get("src")
                or cover_node.get("data-src")
            )

            if cover_url.startswith("/"):
                cover_url = METAL_ARCHIVES_URL + cover_url

            if cover_url.startswith("http://"):
                cover_url = "https://" + cover_url[7:]

            details["cover_url"] = cover_url

        return details

    except Exception as exc:
        print(f"  MA DETAIL ERROR: {exc}")
        return None


def validate_ma_candidate(
    product_artist,
    album,
    details,
    product_edition,
    discovery_source,
):
    if not details:
        return None

    profile = artist_match_profile(
        product_artist,
        details.get("artist", ""),
    )

    title_score = similarity(
        album,
        details.get("title", ""),
    )

    if profile["score"] < 0.98:
        return None

    if not title_is_match(
        album,
        details.get("title", ""),
    ):
        return None

    if not details.get("cover_url"):
        return None

    details["artist_score"] = profile["score"]
    details["title_score"] = title_score
    details["artist_match_mode"] = profile["mode"]
    details["matched_component"] = profile.get(
        "product_component",
        "",
    )
    details["discovery_source"] = discovery_source
    details["edition_profile"] = edition_profile(
        product_edition,
        details,
    )

    print(
        f"  MA CANDIDATE: "
        f"{details['artist']} - {details['title']} "
        f"(artist={profile['score']:.3f}, "
        f"title={title_score:.3f}, "
        f"format={details.get('format') or 'UNKNOWN'}, "
        f"catalog={details.get('catalog') or 'UNKNOWN'}, "
        f"year={details.get('year') or 'UNKNOWN'}, "
        f"catalog_match={details.get('edition_profile', {}).get('catalog_match', 'UNKNOWN')}, "
        f"catalog_score={details.get('edition_profile', {}).get('catalog_score', 0)}, "
        f"discovery={discovery_source}, "
        f"mode={profile['mode']})"
    )

    if profile.get("product_component"):
        print(
            f"  MATCHED COMPONENT: "
            f"{profile['product_component']}"
        )

    return details


def canonical_release_sort_key(candidate):
    """
    Ordena releases para obtener una portada canónica del álbum.

    Se usa únicamente como último desempate cuando el catálogo coincide de
    forma exacta y no existe una única edición compatible. La release más
    antigua es la representación canónica más razonable del álbum.
    """
    release_date = clean(candidate.get("release_date"))
    year = clean(candidate.get("year"))

    if re.match(r"^\d{4}-\d{2}-\d{2}$", release_date):
        date_key = release_date
    else:
        match = re.search(r"\b(19|20)\d{2}\b", release_date or year)
        date_key = (match.group(0) + "-99-99") if match else "9999-99-99"

    return (
        date_key,
        normalize_text(candidate.get("version_desc", "")),
        clean(candidate.get("release_id")),
    )


def choose_ma_by_cover_and_edition(
    candidates,
):
    if not candidates:
        return None

    candidates = deduplicate_ma_candidates(candidates)
    groups = group_visually_equivalent_covers(candidates)

    if not groups:
        return None

    # 1. Misma portada: la edición deja de importar. Elegimos la release con
    #    mejor evidencia de catálogo/formato/año.
    if len(groups) == 1:
        selected = max(
            groups[0],
            key=lambda c: edition_rank(c.get("edition_profile", {})),
        )
        print(
            f"  MA: {len(candidates)} releases comparten la misma portada -> ACCEPT"
        )
        return selected

    # 2. Catálogo exacto es la señal principal. Si solo una release coincide
    #    exactamente con el SKU/catálogo del producto, se acepta aunque el
    #    formato de la fuente sea desconocido.
    exact_catalog = [
        c for c in candidates
        if c.get("edition_profile", {}).get("catalog_match") == "MATCH"
    ]

    if len(exact_catalog) == 1:
        selected = exact_catalog[0]
        print(
            "  MA: catálogo/SKU exacto identifica una única release -> ACCEPT"
        )
        print(
            f"  MA CATALOG: {selected.get('catalog') or 'UNKNOWN'} "
            f"| MATCH={selected.get('edition_profile', {}).get('catalog_match', 'UNKNOWN')}"
        )
        return selected

    # 3. Si hay varias coincidencias exactas de catálogo, intentamos resolver
    #    por formato. Esto cubre variantes reales del mismo release.
    exact_compatible = [
        c for c in exact_catalog
        if c.get("edition_profile", {}).get("format_match") is True
    ]

    compatible_groups = group_visually_equivalent_covers(exact_compatible)

    if len(compatible_groups) == 1:
        selected = max(
            compatible_groups[0],
            key=lambda c: edition_rank(c.get("edition_profile", {})),
        )
        print(
            "  MA: catálogo exacto + formato compatible dejan una única portada -> ACCEPT"
        )
        return selected

    if len(exact_catalog) > 1:
        # 4. Último desempate: si el SKU/catálogo identifica claramente la
        #    obra pero MA tiene varias prensadas con el mismo catálogo y arte
        #    distinto, usamos la release más antigua como portada canónica del
        #    álbum. Esto evita REVIEW cuando el objetivo es la portada del
        #    álbum y no la reproducción exacta de la prensada.
        selected = min(
            exact_catalog,
            key=canonical_release_sort_key,
        )

        print(
            f"  MA: {len(exact_catalog)} releases con catálogo exacto; "
            "sin única edición compatible -> CANONICAL ALBUM COVER"
        )
        print(
            "  MA CATALOG: el catálogo/SKU identifica la obra; "
            "se selecciona la release más antigua como portada canónica."
        )
        return selected

    # 5. Sin catálogo exacto, mantenemos el comportamiento conservador.
    compatible = [
        c for c in candidates
        if edition_is_compatible_for_disambiguation(
            c.get("edition_profile", {})
        )
    ]

    compatible_groups = group_visually_equivalent_covers(compatible)

    if len(compatible_groups) == 1:
        selected = max(
            compatible_groups[0],
            key=lambda c: edition_rank(c.get("edition_profile", {})),
        )
        print(
            "  MA: portadas distintas; una sola portada es compatible "
            "con la edición -> ACCEPT"
        )
        return selected

    print(
        f"  MA: {len(candidates)} releases con {len(groups)} portadas distintas -> REVIEW"
    )
    print(
        "  MA REVIEW: identidad confirmada, pero las portadas son diferentes "
        "y no existe un único criterio de catálogo/edición que permita seleccionar una automáticamente."
    )
    return None


def ma_live_search(
    driver,
    artist,
    album,
    product_edition,
):
    if driver is None:
        return None

    components = split_artist_components(artist) or [artist]
    raw_candidates = []

    # 1. Título del disco primero; en splits, después un artista por vez.
    #    Esto es suficiente para encontrar releases donde MA no conserva
    #    el split como nombre de artista compuesto.
    queries = build_ma_queries(artist, album, product_edition)

    catalog_keys = {
        normalize_catalog(value)
        for value in product_edition.get("catalog_values", [])
        if normalize_catalog(value)
    }

    for query in queries:
        normalized_query = normalize_catalog(query)
        is_catalog_query = any(
            catalog_key in normalized_query
            for catalog_key in catalog_keys
        )

        raw_candidates.extend(
            ma_title_search(
                driver,
                query,
                reference_album=album if is_catalog_query else "",
                discovery_source=(
                    "CATALOG_SEARCH" if is_catalog_query else "TITLE_SEARCH"
                ),
            )
        )

    raw_candidates = deduplicate_ma_candidates(raw_candidates)

    # 2. Si el título no produjo candidatos suficientemente claros, recorrer
    #    la discografía de cada artista individual. Esto cubre releases que
    #    no aparecen bien indexadas por búsqueda de título.
    strong = [
        c
        for c in raw_candidates
        if title_is_match(
            album,
            c.get("title", ""),
        )
    ]

    if not strong:
        for component in components:
            artist_candidates = ma_artist_search(
                driver,
                component,
            )

            for artist_candidate in artist_candidates:
                raw_candidates.extend(
                    ma_artist_discography_search(
                        driver,
                        artist_candidate["url"],
                        album,
                    )
                )

        raw_candidates = deduplicate_ma_candidates(raw_candidates)

    raw_candidates.sort(
        key=lambda c: (
            1 if c.get("discovery_source") == "CATALOG_SEARCH" else 0,
            similarity(album, c.get("title", "")),
        ),
        reverse=True,
    )

    if not raw_candidates:
        print("  MA SEARCH: sin candidatos.")
        return None

    validated = []

    for candidate in raw_candidates[:MA_MAX_TITLE_CANDIDATES]:
        details = ma_get_release_details(
            driver,
            candidate["url"],
        )

        result = validate_ma_candidate(
            artist,
            album,
            details,
            product_edition,
            candidate.get(
                "discovery_source",
                "TITLE_SEARCH",
            ),
        )

        if result:
            validated.append(result)

    if not validated:
        print(
            "  MA SEARCH: sin releases "
            "con identidad confirmada."
        )
        return None

    return choose_ma_by_cover_and_edition(
        validated,
    )


def metal_archives_fallback(
    driver,
    cache_rows,
    artist,
    album,
    product_edition,
):
    print("  MA FALLBACK: activado.")

    cached = ma_cache_match(
        cache_rows,
        artist,
        album,
        product_edition,
    )

    if cached and cached.get("cover_url"):
        print(
            f"  MA CACHE MATCH: "
            f"{cached['artist']} - {cached['title']} "
            f"(mode={cached.get('artist_match_mode', 'NORMAL')})"
        )

        return cached

    return ma_live_search(
        driver,
        artist,
        album,
        product_edition,
    )


# ============================================================
# WOOCOMMERCE WRITE
# ============================================================

def assign_cover(
    auth,
    product_id,
    cover_url,
    artist,
    album,
):
    payload = {
        "images": [
            {
                "src": cover_url,
                "name": f"{artist} - {album}",
                "alt": f"{artist} - {album}",
            }
        ]
    }

    response = request_with_retry(
        "PUT",
        f"{auth['base_url']}/products/{product_id}",
        auth=(
            auth["consumer_key"],
            auth["consumer_secret"],
        ),
        headers={
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=60,
    )

    data = response.json()

    if not data.get("images"):
        raise RuntimeError(
            "WooCommerce respondió sin devolver imágenes."
        )

    return data["images"][0]


# ============================================================
# REPORTES
# ============================================================

FIELDS = [
    "product_id",
    "product_name",
    "artist",
    "album",
    "status",
    "action",
    "source",
    "release_id",
    "cover_url",
    "artist_score",
    "title_score",
    "catalog_match",
    "message",
]


def write_report(filename, rows):
    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=FIELDS,
            extrasaction="ignore",
        )

        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# PROCESAMIENTO DE UN PRODUCTO
# ============================================================

def process_product(
    auth,
    product,
    product_id,
    product_name,
    metadata_rows,
    cache_rows,
):
    """
    Resuelve la portada de un único producto y devuelve la fila de
    reporte correspondiente. No actualiza contadores ni listas globales
    -- eso lo hace el loop en main() a partir del "status" devuelto.
    """
    global MA_DRIVER

    artist, album = parse_product_name(product)

    if not artist:
        print("  REVIEW: no se pudo identificar el artista desde el nombre del producto.")
        return {
            "product_id": product_id,
            "product_name": product_name,
            "artist": "",
            "album": "",
            "status": "MISSING_ARTIST_TITLE",
            "action": "NONE",
            "source": "",
            "release_id": "",
            "cover_url": "",
            "artist_score": "",
            "title_score": "",
            "catalog_match": "",
            "message": "MISSING_ARTIST_TITLE",
        }

    components = split_artist_components(artist)
    print(f"  ARTIST: {artist}")
    if len(components) > 1:
        print("  ARTIST COMPONENTS: " + " | ".join(components))
    if album:
        print(f"  ALBUM:  {album}")
    else:
        print("  ALBUM:  UNKNOWN -> MA SPLIT DISCOVERY MODE")

    product_edition = product_edition_data(product, metadata_rows)
    print(
        "  EDITION: "
        f"format={product_edition['format'] or 'UNKNOWN'}, "
        f"catalog={' | '.join(product_edition['catalog_values']) or 'UNKNOWN'}, "
        f"year={product_edition['year'] or 'UNKNOWN'}"
    )
    if product_edition.get("catalog_sources"):
        print(
            "  CATALOG SOURCES: "
            + " | ".join(
                f"{source}={value}"
                for source, value in product_edition["catalog_sources"]
            )
        )

    cover_url = None
    source = ""
    release_id = ""
    artist_score = ""
    title_score = ""
    ma = None
    mb = None

    # A/B sin álbum: resolver el split directamente en MA.
    if not album:
        if MA_DRIVER is None:
            MA_DRIVER = create_ma_driver()
        ma = ma_missing_album_search(
            MA_DRIVER,
            artist,
            product_edition,
        )
        if ma and ma.get("cover_url"):
            album = clean(ma.get("title"))
            cover_url = ma["cover_url"]
            source = ma.get("source", "metal_archives_live")
            release_id = clean(ma.get("release_id"))
            artist_score = "1.000"
            title_score = "1.000"
            print(f"  ALBUM RESOLVED BY MA: {album}")

    # Flujo normal.
    if album and not cover_url:
        try:
            mb = mb_search(artist, album)
        except Exception as exc:
            print(f"  MB ERROR: {exc}")
            mb = None

        if mb:
            cover_url = get_cover(mb["release_id"])
            if cover_url:
                source = "cover_art_archive"
                release_id = mb["release_id"]
                artist_score = f"{mb['artist_score']:.3f}"
                title_score = f"{mb['title_score']:.3f}"

        if not cover_url:
            if MA_DRIVER is None:
                MA_DRIVER = create_ma_driver()
            ma = metal_archives_fallback(
                MA_DRIVER,
                cache_rows,
                artist,
                album,
                product_edition,
            )
            if ma and ma.get("cover_url"):
                cover_url = ma["cover_url"]
                source = ma.get("source", "metal_archives")
                release_id = clean(ma.get("release_id"))
                artist_score = (
                    f"{ma.get('artist_score', 0):.3f}"
                    if ma.get("artist_score") is not None
                    else ""
                )
                title_score = (
                    f"{ma.get('title_score', 0):.3f}"
                    if ma.get("title_score") is not None
                    else ""
                )

    # Validamos que la portada encontrada sea realmente accesible y
    # decodifique como imagen ANTES de intentar asignarla en WooCommerce.
    # Una URL rota/bloqueada momentáneamente no debe terminar como imagen
    # rota en un producto -- se trata igual que "no se encontró portada".
    if cover_url and not validate_cover_url(cover_url):
        print("  COVER URL NO VÁLIDA/INACCESIBLE -> se descarta y se manda a REVIEW")
        cover_url = None

    if not cover_url:
        print(
            "  RESULTADO: REVIEW / "
            "SIN PORTADA SEGURA"
        )

        return {
            "product_id": product_id,
            "product_name": product_name,
            "artist": artist,
            "album": album,
            "status": "REVIEW",
            "action": "NONE",
            "source": "none",
            "release_id": (
                mb["release_id"]
                if mb
                else ""
            ),
            "cover_url": "",
            "artist_score": (
                f"{mb['artist_score']:.3f}"
                if mb
                else ""
            ),
            "title_score": (
                f"{mb['title_score']:.3f}"
                if mb
                else ""
            ),
            "catalog_match": (
                ma.get("edition_profile", {}).get("catalog_match", "")
                if ma else ""
            ),
            "message": (
                "No se encontró una portada "
                "automática segura."
            ),
        }

    print("  RESULTADO: PORTADA ENCONTRADA")
    print(f"  SOURCE: {source}")
    print(f"  URL: {cover_url}")

    if ma:
        print(
            "  ARTIST MATCH MODE: "
            f"{ma.get('artist_match_mode', 'NORMAL')}"
        )

        if ma.get("matched_component"):
            print(
                "  MATCHED COMPONENT: "
                f"{ma['matched_component']}"
            )

    action = "READY"

    if DRY_RUN:
        print("  ACTION: READY / DRY-RUN")
    else:
        try:
            image = assign_cover(
                auth,
                product_id,
                cover_url,
                artist,
                album,
            )

            print(
                f"  ACTION: UPDATED / "
                f"IMAGE ID {image.get('id')}"
            )

            action = "UPDATED"

        except Exception as exc:
            print(f"  WRITE ERROR: {exc}")
            action = "BLOCKED"

    return {
        "product_id": product_id,
        "product_name": product_name,
        "artist": artist,
        "album": album,
        "status": (
            "READY"
            if DRY_RUN
            else action
        ),
        "action": action,
        "source": source,
        "release_id": release_id,
        "cover_url": cover_url,
        "artist_score": artist_score,
        "title_score": title_score,
        "catalog_match": (
            ma.get("edition_profile", {}).get("catalog_match", "")
            if ma else ""
        ),
        "message": "",
    }


# ============================================================
# MAIN
# ============================================================

def main():
    global MA_DRIVER

    print("=" * 60)
    print(" MUSIC COVER IMPORTER V2.11.1")
    print("=" * 60)
    print()

    print("OBJETIVO: portada únicamente")

    print(
        "FUENTES: MusicBrainz + Cover Art Archive + "
        "Metal Archives"
    )

    print(
        "MODO:",
        "DRY-RUN / READ ONLY"
        if DRY_RUN
        else "ESCRITURA REAL",
    )

    if DRY_RUN:
        print("NO se modificará WooCommerce.")

    print()

    print(
        "PRIORIDAD: "
        "MusicBrainz/Cover Art Archive -> "
        "Metal Archives"
    )

    print()

    auth = load_woocommerce_credentials()

    print(f"Endpoint: {auth['base_url']}")
    print()

    products = wc_get_products(auth)

    without_image = [
        p
        for p in products
        if (
            not ONLY_PRODUCTS_WITHOUT_IMAGE
            or not product_has_image(p)
        )
    ]

    candidates = [
        p
        for p in without_image
        if is_probably_music(p)
    ]

    print()
    print(f"Productos totales: {len(products)}")
    print(f"Sin imagen: {len(without_image)}")
    print(f"Candidatos musicales: {len(candidates)}")

    if TEST_LIMIT is not None:
        candidates = candidates[:TEST_LIMIT]
        print(f"TEST_LIMIT: {len(candidates)}")

    cache_rows = load_ma_cache()
    metadata_rows = load_metadata()

    report = []
    review = []

    ready = 0
    updated = 0
    reviews = 0
    blocked = 0

    print()
    print("=" * 60)
    print("PROCESAMIENTO")
    print("=" * 60)

    try:
        for number, product in enumerate(
            candidates,
            start=1,
        ):
            product_id = product.get("id")
            product_name = clean(product.get("name"))

            print()
            print("-" * 60)

            print(
                f"[{number}/{len(candidates)}] "
                f"PRODUCTO {product_id}: {product_name}"
            )

            # Cada producto se procesa de forma aislada: si algo revienta
            # aquí dentro (incluido Selenium/Metal Archives, que es la
            # parte menos predecible), no se pierde el batch completo --
            # se registra como ERROR y se sigue con el siguiente producto.
            try:
                row = process_product(
                    auth,
                    product,
                    product_id,
                    product_name,
                    metadata_rows,
                    cache_rows,
                )
            except Exception as exc:
                print(f"  ERROR INESPERADO PROCESANDO PRODUCTO: {exc}")
                row = {
                    "product_id": product_id,
                    "product_name": product_name,
                    "artist": "",
                    "album": "",
                    "status": "ERROR",
                    "action": "NONE",
                    "source": "",
                    "release_id": "",
                    "cover_url": "",
                    "artist_score": "",
                    "title_score": "",
                    "catalog_match": "",
                    "message": f"Excepción no controlada: {exc}",
                }

            report.append(row)

            status = row.get("status")

            if status in ("REVIEW", "ERROR", "MISSING_ARTIST_TITLE"):
                review.append(row)
                reviews += 1
            elif status == "READY":
                ready += 1
            elif status == "UPDATED":
                updated += 1
            elif status == "BLOCKED":
                blocked += 1

            if number % CHECKPOINT_EVERY == 0 or number == len(candidates):
                write_report(REPORT_FILE, report)
                write_report(REVIEW_FILE, review)
                print(
                    f"  [checkpoint] progreso guardado "
                    f"({number}/{len(candidates)})"
                )

            time.sleep(REQUEST_DELAY)

    finally:
        if MA_DRIVER is not None:
            try:
                print(
                    "\nCerrando navegador de Metal Archives..."
                )
                MA_DRIVER.quit()
            except Exception as exc:
                print(f"  MA DRIVER CLOSE ERROR: {exc}")
            finally:
                MA_DRIVER = None

        # Se escribe SIEMPRE, incluso si el loop se interrumpió a mitad
        # de camino (Ctrl+C, excepción no prevista, etc.) -- así nunca se
        # pierde el progreso acumulado hasta ese punto.
        write_report(REPORT_FILE, report)
        write_report(REVIEW_FILE, review)

    print()
    print("=" * 60)
    print("MUSIC COVER IMPORTER V2.11.1 — RESULTADO")
    print("=" * 60)
    print()

    print(f"Procesados: {len(candidates)}")
    print(f"READY:     {ready}")
    print(f"UPDATED:   {updated}")
    print(f"REVIEW:    {reviews}")
    print(f"BLOCKED:   {blocked}")

    print()
    print(f"Reporte:  {REPORT_FILE}")
    print(f"Review:   {REVIEW_FILE}")

    if DRY_RUN:
        print()
        print("NO se modificó WooCommerce.")
        print()
        print(
            "SIGUIENTE PASO: revisar las filas READY "
            "y sus URLs antes de activar escritura."
        )


if __name__ == "__main__":
    main()
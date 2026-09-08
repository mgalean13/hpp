#!/usr/bin/env python3
"""
generar_descripciones_gemini.py  (v2 — replica el Gem "HPP Metal Assistant")
=============================================================================

Este script reproduce, vía la API de Gemini, el comportamiento de tu Gem
"HPP Metal Assistant" (rol, glosario de subgéneros, reglas de SEO on-page,
y contexto del sello Humanity's Plague Productions), porque los Gems de la
app de Gemini no tienen una API pública invocable desde código — así que
en vez de "llamar al Gem", le pasamos las mismas instrucciones como
system_instruction en cada llamada a la API.

Para cada producto de tu tienda WooCommerce, genera:
    - meta_title            (50–60 caracteres)
    - meta_description      (140–155 caracteres)
    - focus_keyword
    - alt_text (de la imagen principal del producto)
    - short_description
    - full_description (HTML): reseña de sonido + ficha técnica + tracklist
      investigado con búsqueda web real (o marcado como no encontrado, nunca
      inventado)

Como con la v1, el proceso tiene dos pasos separados:

    Paso 1 (por defecto):  genera el contenido y lo guarda en
                            gemini_resultados.csv. NO toca tu tienda.

    Paso 2 (--aplicar):    lee gemini_resultados.csv y actualiza WooCommerce
                            (descripciones, SEO de Yoast Y RankMath, y el
                            alt text de la imagen). Pide confirmación.

=====================================================================
CONFIGURACIÓN DE CREDENCIALES (por variables de entorno)
=====================================================================
    setx GROQ_API_KEY "tu_api_key_de_groq_aqui"
    setx WC_URL "https://tu-tienda.com"
    setx WC_CONSUMER_KEY "tu_consumer_key_de_woocommerce"
    setx WC_CONSUMER_SECRET "tu_consumer_secret_de_woocommerce"
(cierra y abre una terminal nueva después de correr esto)

Requisitos:
    pip install groq requests

Uso:
    python3 generar_descripciones_gemini.py                # generar (dry-run)
    python3 generar_descripciones_gemini.py --limite 5      # probar con 5
    python3 generar_descripciones_gemini.py --aplicar       # publicar en WooCommerce
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata

import requests

try:
    from groq import Groq
except ImportError:
    Groq = None


# ----------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
WC_URL = os.environ.get("WC_URL", "https://humanitysplagueprod.com")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "")

MODEL = "groq/compound-mini"  # incluye búsqueda web integrada (1 búsqueda por llamada, más liviano)
RESULTADOS_PATH = "gemini_resultados.csv"
SIN_TRACKLIST_PATH = "gemini_sin_tracklist.csv"
BATCH_SIZE_WC = 50
CSV_FIELDNAMES = [
    "id", "sku", "name", "image_id", "slug",
    "meta_title", "meta_description", "focus_keyword", "alt_text",
    "short_description", "full_description_html",
    "tracklist_encontrado", "fuente",
]
# ----------------------------------------------------------------------


# ======================================================================
# SYSTEM INSTRUCTION — instrucciones + conocimiento de tu Gem "HPP Metal Assistant"
# ======================================================================
SYSTEM_INSTRUCTION = """
# ROL Y PERFIL
Eres HPP Metal Assistant, un especialista de clase mundial en SEO para
e-commerce, optimización técnica on-page e historiador musical con
autoridad absoluta en Metal Extremo, específicamente Black Metal y todos
sus subgéneros (Second Wave, Raw, Atmospheric, DSBM, War Metal, Pagan,
Orthodox, Dungeon Synth).

Tu única misión es auditar, generar y optimizar contenido de alta
conversión y optimizado para buscadores para Humanity's Plague
Productions (HPP) — un sello y mail-order underground con sede en North
Andover, MA, especializado en ediciones físicas (LPs, CDs, Cassettes) de
black metal crudo, abyecto y tradicional, y merchandising.

# CONTEXTO DEL SELLO (Humanity's Plague Productions)
HPP es un sello y operación de mail-order de metal extremo underground,
con sede en North Andover, Massachusetts (198 High St, North Andover, MA
01845, USA). Fundado alrededor de 2007, dedicado exclusivamente a black
metal crudo, abyecto y tradicional, con ediciones físicas limitadas
(CDs, LPs, Cassettes) y merchandising underground. HPP prioriza la
pureza estética cruda, la coleccionabilidad física y la profundidad
atmosférica por sobre la accesibilidad comercial.
Modelo de negocio: sello y mail-order DIY independiente, tiradas
limitadas (típicamente 200–500 copias). Distribuidores: Norteamérica -
NWN! Productions; Europa - Darkness Shall Rise Productions (Alemania),
Darkwoods (España), Iron Bonehead (UK), BlackSeed.

# VOZ EDITORIAL Y TONO DE MARCA
- Tono: culto, atmosférico, sobrio, autoritativo, profundamente respetuoso
  con la herencia del metal extremo.
- Vocabulario: usa terminología underground auténtica ("atmósfera abyecta",
  "ambiente opresivo", "necrosound crudo", "riffing abrasivo", "resonancia
  fría", "lanzamiento de culto", "ejecución monolítica").
- ESTRICTAMENTE PROHIBIDO: clichés de marketing o relleno comercial
  genérico (ej. "súper oferta", "producto fantástico", "no te lo pierdas",
  "compra ya", "mejor precio").

# GLOSARIO DE SUBGÉNEROS (úsalo para identificar y describir el estilo)
- Second Wave Black Metal: riffs fríos trémolo, blast beats percusivos,
  voces desgarradas, producción lo-fi de los pioneros escandinavos de los 90.
- Raw Black Metal / Lo-Fi: producción underground intencionalmente cruda,
  abrasiva, con énfasis en aspereza, ruido de cinta y agresión emocional cruda.
- Atmospheric Black Metal: composiciones extensas, guitarras con reverb,
  interludios ambientales, paisajes sonoros inmersivos.
- DSBM (Depressive Suicidal Black Metal): estructuras lentas a medio tempo,
  voces agonizantes, melodías melancólicas, temas de aislamiento y desesperación.
- War Metal / Bestial Black Metal: fusión caótica de Black y Death Metal,
  baterías aplastantes, muro de ruido, violencia implacable.
- Pagan / Folk Black Metal: pasajes acústicos, instrumentos tradicionales,
  temas heroicos, paganismo ancestral.
- Orthodox / Religious Black Metal: arreglos disonantes complejos, letras
  teológicas oscuras o satánicas, estructuras ritualísticas.
- Dungeon Synth: música ambiental con sintetizador, atmósferas medievales,
  fantasía y oscuridad de castillo, ligada a las demos de black metal crudo.

# TERMINOLOGÍA DE FORMATOS Y COLECCIONISMO
- Gatefold, Splatter/Marble/Swirl Vinyl, Die Hard Edition, Reissue/Repress,
  Necrosound/Analog Lo-Fi — úsalos cuando correspondan al producto.

# REGLAS DE SEO ON-PAGE (WOOCOMMERCE)
- Meta Title: estrictamente 50 a 60 caracteres.
  Fórmula (Música): [Banda] - [Álbum] [Formato] | Humanity's Plague Productions
  Fórmula (Merch): [Ítem/Diseño] - [Banda/Tema] | Humanity's Plague Productions
- Meta Description: estrictamente 140 a 155 caracteres. Debe impulsar
  intención de búsqueda transaccional, resaltar valor coleccionable físico,
  exclusividad de formato y disponibilidad underground.
- Focus Keyword: términos de alta intención transaccional
  (ej. "buy [banda] [álbum] vinyl", "black metal cassette tape",
  "underground black metal shop", "gatefold LP limited edition").
- Alt Text de imagen: descripción natural y rica en keywords del
  artwork/prenda, sin relleno de keywords.
  Ejemplo: "Front cover artwork of Darkthrone Transilvanian Hunger on 12 inch black vinyl LP"

# ESTRUCTURA DE CONTENIDO — LANZAMIENTOS MUSICALES (LP, CD, Cassette)
- Short Description: 2 a 3 frases contundentes dirigidas a coleccionistas
  físicos, resaltando estilo musical, specs de la edición y exclusividad.
- Full Description (arquitectura HTML):
  1. Reseña de sonido y atmósfera (2 párrafos <p>): análisis de
     composición, riffs, entrega vocal, textura de producción
     (lo-fi/necrosound) y resonancia atmosférica.
  2. Ficha técnica del coleccionista, como lista <ul>:
     <li><strong>Band:</strong> [Nombre]</li>
     <li><strong>Album:</strong> [Título]</li>
     <li><strong>Format:</strong> [LP 12" / Gatefold / Digipak / CD / Cassette]</li>
     <li><strong>Label:</strong> [Sello]</li>
     <li><strong>Release / Pressing Year:</strong> [Año]</li>
     <li><strong>Country:</strong> [País de origen]</li>
     <li><strong>Edition Specs:</strong> [Tiraje, color de vinilo, detalles de insert]</li>
  3. Tracklist: lista <ol> (o dividida en Side A / Side B para vinilo/cassette).

# ESTRUCTURA DE CONTENIDO — MERCHANDISING (remeras, hoodies, parches)
- Full Description (arquitectura HTML):
  1. Arte y concepto (1-2 párrafos <p>): explicación de la iconografía
     visual, simbolismo o arte original.
  2. Especificaciones técnicas, como lista <ul>:
     <li><strong>Garment Material:</strong> 100% Heavyweight Cotton</li>
     <li><strong>Print Type:</strong> High-durability Screenprint</li>
     <li><strong>Garment Color:</strong> Black / Dark Red / White</li>
     <li><strong>Fit Type:</strong> Unisex / Regular Fit</li>
     <li><strong>Care Instructions:</strong> Wash inside out at 30°C</li>

# INVESTIGACIÓN Y HONESTIDAD FACTUAL (regla de seguridad, no negociable)
Usa la herramienta de búsqueda web para investigar el lanzamiento real
(banda, álbum, año, país, sello, tracklist) en fuentes como Metal
Archives, Discogs, Bandcamp o el sitio del sello. Si NO encuentras un
dato con una fuente razonablemente confiable (especialmente el
tracklist, el año o el país), NO LO INVENTES: usa "Unknown" para ese
dato, y en el caso puntual del tracklist deja la lista vacía y marca
"tracklist_encontrado": false. Nunca sacrifiques precisión factual por
completar la plantilla.

# FORMATO DE SALIDA
Cuando se te pida para consumo por script de automatización, responde
ÚNICAMENTE con un objeto JSON válido, sin texto conversacional antes ni
después, sin bloques de código markdown, con esta estructura exacta:
{
  "meta_title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "alt_text": "...",
  "short_description": "...",
  "full_description": "...",
  "tracklist_encontrado": true,
  "fuente": "URL o nombre de la fuente usada para el tracklist, o vacío si no se encontró"
}
"""

PROMPT_TEMPLATE = """Genera el contenido para este producto de la tienda:

- Nombre / Banda - Álbum (tal como está cargado): {nombre}
- Categorías / Formato: {tipo}
- Sello (Label): {sello}
- SKU interno (no lo menciones en el texto): {sku}

Determina si es un lanzamiento musical (LP/CD/Cassette) o merchandising
según el nombre y las categorías, y aplica la estructura de contenido
correspondiente.

IMPORTANTE: tu respuesta debe ser ÚNICAMENTE el objeto JSON pedido en las
instrucciones. No agregues explicaciones, comentarios, texto introductorio
ni resumas lo que investigaste. No uses bloques de código markdown (```).
La primera letra de tu respuesta debe ser '{{' y la última '}}'.
"""


def generar_slug(nombre: str) -> str:
    """Genera un slug limpio tipo 'banda-album-formato' a partir del nombre del producto."""
    nfkd = unicodedata.normalize("NFKD", nombre)
    sin_acentos = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = sin_acentos.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def limpiar_json(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(json)?", "", texto).strip()
    texto = re.sub(r"```$", "", texto).strip()
    # Los modelos de Groq a veces agregan texto conversacional antes/después
    # del JSON (ej. explicando qué buscaron). Si el parseo directo falla,
    # extraemos el bloque { ... } más externo y probamos de nuevo.
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio = texto.find("{")
        fin = texto.rfind("}")
        if inicio == -1 or fin == -1 or fin <= inicio:
            raise
        return json.loads(texto[inicio:fin + 1])


def obtener_productos_woocommerce() -> list:
    productos = []
    pagina = 1
    while True:
        resp = requests.get(
            f"{WC_URL.rstrip('/')}/wp-json/wc/v3/products",
            params={"per_page": 100, "page": pagina},
            auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
            timeout=60,
        )
        resp.raise_for_status()
        lote = resp.json()
        if not lote:
            break
        productos.extend(lote)
        pagina += 1
    return productos


def cargar_ya_procesados(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8", newline="") as f:
        return {row["id"] for row in csv.DictReader(f)}


def generar_contenido(cliente, producto: dict) -> dict:
    label = ""
    for attr in producto.get("attributes", []):
        if attr.get("name", "").strip().lower() == "label":
            label = ", ".join(attr.get("options", []))
            break

    prompt = PROMPT_TEMPLATE.format(
        nombre=producto.get("name", ""),
        tipo=", ".join(c.get("name", "") for c in producto.get("categories", [])),
        sello=label,
        sku=producto.get("sku", ""),
    )

    respuesta = cliente.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        compound_custom={"tools": {"enabled_tools": ["web_search"]}},
        max_completion_tokens=6000,
    )

    contenido = respuesta.choices[0].message.content
    if not contenido:
        print(f"  --- Diagnóstico: contenido vacío ---")
        print(f"  finish_reason: {respuesta.choices[0].finish_reason}")
        print(f"  message completo: {respuesta.choices[0].message!r}")
        print(f"  ---")
    try:
        return limpiar_json(contenido)
    except json.JSONDecodeError:
        print(f"  --- Respuesta cruda del modelo (primeros 500 caracteres) ---\n  {contenido[:500]!r}\n  ---")
        raise


def paso_generar(args):
    if Groq is None:
        sys.exit("Falta instalar el SDK de Groq. Corre: pip install groq")
    if not GROQ_API_KEY:
        sys.exit("No encontré GROQ_API_KEY en las variables de entorno.")
    if not WC_CONSUMER_KEY or not WC_CONSUMER_SECRET:
        sys.exit("No encontré WC_CONSUMER_KEY / WC_CONSUMER_SECRET en las variables de entorno.")

    cliente = Groq(api_key=GROQ_API_KEY)

    print("Descargando productos desde WooCommerce...")
    productos = obtener_productos_woocommerce()
    print(f"  Total de productos en la tienda: {len(productos)}")

    ya_procesados = set() if args.reprocesar else cargar_ya_procesados(RESULTADOS_PATH)
    if ya_procesados:
        print(f"  Ya procesados en corridas anteriores (se van a saltar): {len(ya_procesados)}")

    pendientes = [p for p in productos if str(p["id"]) not in ya_procesados]
    if args.limite:
        pendientes = pendientes[:args.limite]
    print(f"  Productos a procesar en esta corrida: {len(pendientes)}")

    escribir_encabezado = not os.path.exists(RESULTADOS_PATH) or args.reprocesar
    modo = "w" if args.reprocesar else "a"
    espera = 60.0 / args.rpm

    with open(RESULTADOS_PATH, modo, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if escribir_encabezado:
            writer.writeheader()

        for i, producto in enumerate(pendientes, 1):
            print(f"[{i}/{len(pendientes)}] {producto.get('name')} (id {producto['id']})...")
            imagenes = producto.get("images", [])
            image_id = imagenes[0]["id"] if imagenes else ""
            slug = generar_slug(producto.get("name", ""))

            intentos = 0
            while True:
                intentos += 1
                try:
                    r = generar_contenido(cliente, producto)
                    writer.writerow({
                        "id": producto["id"],
                        "sku": producto.get("sku", ""),
                        "name": producto.get("name", ""),
                        "image_id": image_id,
                        "slug": slug,
                        "meta_title": r.get("meta_title", ""),
                        "meta_description": r.get("meta_description", ""),
                        "focus_keyword": r.get("focus_keyword", ""),
                        "alt_text": r.get("alt_text", ""),
                        "short_description": r.get("short_description", ""),
                        "full_description_html": r.get("full_description", ""),
                        "tracklist_encontrado": r.get("tracklist_encontrado", False),
                        "fuente": r.get("fuente", ""),
                    })
                    f.flush()
                    break
                except json.JSONDecodeError:
                    print("  Aviso: respuesta no vino en JSON válido, se omite (revisar a mano luego).")
                    break
                except Exception as e:
                    mensaje = str(e)
                    if "RESOURCE_EXHAUSTED" in mensaje or "429" in mensaje:
                        if intentos >= 3:
                            print(f"\nParece que se agotó la cuota diaria gratuita de Groq. Progreso guardado en {RESULTADOS_PATH}. Corre el script de nuevo mañana.")
                            return
                        print("  Límite de tasa alcanzado, esperando 60s y reintentando...")
                        time.sleep(60)
                        continue
                    if "413" in mensaje or "request_too_large" in mensaje:
                        print("  Aviso: la petición fue demasiado grande (mucho contenido de búsqueda web), se omite este producto.")
                        break
                    print(f"  Error inesperado: {mensaje}")
                    break

            time.sleep(espera)

    print(f"\nListo. Resultados guardados en {RESULTADOS_PATH}")
    print("Revísalo y, cuando estés conforme, corre este script con --aplicar para publicarlo en WooCommerce.")


def paso_aplicar(args):
    if not WC_CONSUMER_KEY or not WC_CONSUMER_SECRET:
        sys.exit("No encontré WC_CONSUMER_KEY / WC_CONSUMER_SECRET en las variables de entorno.")
    if not os.path.exists(RESULTADOS_PATH):
        sys.exit(f"No existe {RESULTADOS_PATH} todavía. Corre primero el script sin --aplicar.")

    with open(RESULTADOS_PATH, encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))

    sin_tracklist = [r for r in filas if str(r.get("tracklist_encontrado", "")).lower() not in ("true", "1")]
    if sin_tracklist:
        with open(SIN_TRACKLIST_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "sku", "name"])
            writer.writeheader()
            for r in sin_tracklist:
                writer.writerow({"id": r["id"], "sku": r["sku"], "name": r["name"]})
        print(f"Aviso: {len(sin_tracklist)} productos no tienen tracklist confirmado. Se actualiza el resto del contenido igual, pero revisa {SIN_TRACKLIST_PATH}.")

    print(f"\nSe van a actualizar {len(filas)} productos en {WC_URL}:")
    print("  - Descripción corta y larga")
    print("  - SEO de Yoast y RankMath (meta title, meta description, focus keyword)")
    print("  - Alt text de la imagen principal")
    print("  - Slug/URL del producto (WordPress suele redirigir la URL vieja automáticamente, pero revisa después de aplicar)")
    confirmacion = input("Escribe 'ACTUALIZAR' para confirmar: ")
    if confirmacion.strip() != "ACTUALIZAR":
        print("Cancelado.")
        return

    endpoint = f"{WC_URL.rstrip('/')}/wp-json/wc/v3/products/batch"
    auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)

    for i in range(0, len(filas), BATCH_SIZE_WC):
        lote = filas[i:i + BATCH_SIZE_WC]
        updates = []
        for r in lote:
            item = {
                "id": int(r["id"]),
                "short_description": r["short_description"],
                "description": r["full_description_html"],
                "slug": r.get("slug") or generar_slug(r["name"]),
                "meta_data": [
                    {"key": "_yoast_wpseo_title", "value": r["meta_title"]},
                    {"key": "_yoast_wpseo_metadesc", "value": r["meta_description"]},
                    {"key": "_yoast_wpseo_focuskw", "value": r["focus_keyword"]},
                    {"key": "rank_math_title", "value": r["meta_title"]},
                    {"key": "rank_math_description", "value": r["meta_description"]},
                    {"key": "rank_math_focus_keyword", "value": r["focus_keyword"]},
                ],
            }
            if r.get("image_id") and r.get("alt_text"):
                item["images"] = [{"id": int(r["image_id"]), "alt": r["alt_text"]}]
            updates.append(item)

        payload = {"update": updates}
        print(f"Actualizando lote {i // BATCH_SIZE_WC + 1} ({len(lote)} productos)...")
        resp = requests.post(endpoint, json=payload, auth=auth, timeout=60)
        if resp.status_code not in (200, 201):
            print(f"  ERROR HTTP {resp.status_code}: {resp.text[:500]}")
            continue
        data = resp.json()
        errores = [item for item in data.get("update", []) if "error" in item]
        if errores:
            print(f"  {len(errores)} productos con error en este lote.")
        time.sleep(0.5)

    print("\nListo.")


def main():
    parser = argparse.ArgumentParser(description="Genera contenido SEO/descripciones con el prompt del Gem HPP Metal Assistant y lo publica en WooCommerce.")
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--limite", type=int, default=0)
    parser.add_argument("--rpm", type=float, default=8)
    parser.add_argument("--reprocesar", action="store_true")
    args = parser.parse_args()

    if args.aplicar:
        paso_aplicar(args)
    else:
        paso_generar(args)


if __name__ == "__main__":
    main()

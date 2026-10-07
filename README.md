# Humanity's Plague Productions — E-commerce Automation Toolkit

Python automation toolkit developed to support the **Humanity's Plague Productions (HPP)** WooCommerce catalog and its ongoing e-commerce operations.

The repository focuses on a practical automation problem: managing a large underground-music catalog while keeping **product data, artwork, metadata, SEO and WooCommerce state** consistent with a controlled source of truth.

> **Portfolio project:** this repository demonstrates API integration, data processing, catalog classification, external metadata enrichment, image validation, SEO auditing, AI-assisted content generation, and controlled WooCommerce synchronization.

## Project Context

Humanity's Plague Productions is an underground / extreme-music label and mail-order operation with a substantial physical music catalog.

The underlying store is built with **WordPress + WooCommerce + Elementor**. The broader project includes catalog management, product optimization, technical SEO, e-commerce maintenance and custom automation. Project documentation identifies music catalog importing/classification as a dedicated workflow and records approximately **822 previously processed products**, with approximately **816 identified as music**.

## What This Toolkit Automates

```text
WooCommerce
    │
    ├── Export catalog
    │       ├── Classify products
    │       └── Analyze metadata
    │
    ├── Enrich / recover artwork
    │       ├── MusicBrainz
    │       ├── Cover Art Archive
    │       └── Metal Archives
    │
    ├── Audit SEO
    │       └── Yoast / canonical / robots / metadata
    │
    ├── Generate product content
    │       └── AI + web research → reviewable CSV
    │
    └── Synchronize catalog state
            └── HPP catalog → WooCommerce
```

The important design principle is that **analysis and generation are separated from publication whenever possible**. Several scripts produce reviewable CSV reports before making changes to the live store.

---

## Automation Modules

### 1. WooCommerce Product Export

**`export_products.py`**

Exports the published WooCommerce catalog through the REST API.

Collects product IDs, names, SKUs, types, status, descriptions, categories, tags, image URLs and permalinks.

The exporter is read-only and does not modify the catalog.

---

### 2. Catalog Classification

**`catalog_classifier.py`**

Classifies products using deterministic rules based on categories and product names.

Current classifications:

- `MUSIC`
- `MERCH`
- `OTHER`

For music releases it also attempts to extract:

- Artist
- Album
- Format
- Review-required status

Ambiguous products are flagged for review instead of being silently transformed.

---

### 3. Catalog Metadata Analysis

**`catalog_metadata_analyzer.py`**

Performs structural analysis of music-product metadata and detects:

- Release years
- Barcodes / EAN / UPC-like values
- Catalog numbers
- Record labels
- Tracklists
- Description availability
- Short-description availability

Output:

```text
catalog_metadata_analysis.csv
```

---

### 4. Automated Cover-Art Import

**`music_cover_importer_v2.11.1.py`**

The main artwork-enrichment module.

It resolves missing product artwork using:

1. **MusicBrainz**
2. **Cover Art Archive**
3. **Metal Archives**

Matching considers artist similarity, album/title similarity, split-release components, SKU/catalog number, release format and release metadata.

The importer can also use Selenium for Metal Archives when required.

#### Image validation

Before a cover URL is assigned to WooCommerce, the script verifies that:

1. The URL is accessible.
2. The response is an image.
3. The image can be decoded successfully.
4. The downloaded payload stays within the configured size limit.

#### Reliability safeguards

The importer includes:

- Retry handling for transient HTTP failures.
- Rate limiting.
- Per-product error isolation.
- Periodic checkpoint reports.
- Final report persistence.
- `DRY_RUN=True` by default.
- Review reports for unresolved matches.

---

### 5. AI-Assisted Product Content Generation

**`generar_descripciones_gemini_v2.py`**

Despite its historical filename, the current implementation uses the **Groq API** and the `groq/compound-mini` model with web-search capability.

It generates structured WooCommerce content including:

- SEO title
- Meta description
- Focus keyword
- Image ALT text
- Short description
- Full HTML description
- Tracklist verification status
- Research source

The workflow separates generation from publication:

```text
WooCommerce
    ↓
AI + web research
    ↓
gemini_resultados.csv
    ↓
Human review
    ↓
--aplicar
    ↓
WooCommerce batch update
```

The default generation mode does **not** modify the store.

---

### 6. SEO Catalog Audit

**`seo_catalog_audit_v2.py`**

Read-only SEO auditing tool for WooCommerce products.

It evaluates:

- Product descriptions
- Short descriptions
- Product images
- Image ALT attributes
- Yoast availability
- SEO titles and length
- Expected title structure
- Meta descriptions and length
- Canonical URLs
- Robots directives
- Missing categories
- Missing slugs
- Duplicate SEO titles
- Duplicate meta descriptions

Expected title structure:

```text
Artist - Album (Format) | Humanity's Plague Productions
```

The audit distinguishes **missing SEO data** from **an unsuccessful Yoast API response**, avoiding false positives.

Outputs:

```text
seo_catalog_audit_v2.csv
seo_catalog_audit_v2_summary.txt
```

---

### 7. Catalog-to-WooCommerce Synchronization

**`sync_woocommerce_con_hpp.py`**

Synchronizes WooCommerce against an HPP catalog treated as the canonical product set.

The script:

1. Reads the HPP Excel catalog.
2. Extracts and normalizes canonical SKUs.
3. Reads the current WooCommerce export.
4. Compares WooCommerce SKUs against the canonical catalog.
5. Generates a deletion candidate report.
6. Performs no destructive action unless explicitly requested.

Default execution is a **dry run**.

```bash
python sync_woocommerce_con_hpp.py --execute
```

publishes the deletion operation.

Adding:

```bash
--force
```

permanently deletes products instead of sending them to the trash.

Products without SKUs are excluded from automatic deletion for safety.

---

## Technology Stack

| Area | Technology |
|---|---|
| Language | Python 3 |
| E-commerce | WooCommerce |
| CMS | WordPress |
| API | WooCommerce REST API |
| Catalog data | CSV / XLSX |
| Data processing | pandas |
| Excel processing | openpyxl |
| HTTP | requests |
| Image validation | Pillow |
| Web parsing | BeautifulSoup |
| Browser automation | Selenium |
| Music metadata | MusicBrainz |
| Cover artwork | Cover Art Archive |
| Music database | Metal Archives |
| SEO | Yoast / Rank Math metadata |
| AI content generation | Groq API |
| Version control | Git / GitHub |

---

## Engineering Principles

### Read before write

Whenever practical, the workflow first exports or audits the existing state before changing it.

### Dry-run by default

Destructive or publishing operations are separated from analysis and generation.

### Human review for ambiguity

When an automated matcher cannot establish a sufficiently reliable identity, it produces a review candidate instead of guessing.

### Source identity matters

For music releases, SKU/catalog number is treated as an important identity signal alongside artist and title.

### External data is validated

Remote artwork is not trusted merely because an HTTP request succeeded. The downloaded payload is checked as an actual image.

### Credentials stay outside the code

WooCommerce credentials are loaded from environment variables or local credential files rather than being embedded in the automation workflow.

### Fault isolation

Long-running processing is designed so that a failure affecting one product does not unnecessarily abort the complete batch.

### Evidence-driven debugging

The broader HPP project follows:

```text
Evidence
   ↓
Hypothesis
   ↓
Test
   ↓
Root Cause
   ↓
Minimal Fix
   ↓
Verification
```

---

## Installation

Clone the repository:

```bash
git clone https://github.com/mgale13/hpp.git
cd hpp
```

Create a virtual environment:

### Windows

```powershell
python -m venv venv
venv\Scripts\activate
```

### Linux / macOS

```bash
python3 -m venv venv
source venv/bin/activate
```

Install the common dependencies:

```bash
pip install requests pandas openpyxl python-dotenv pillow beautifulsoup4 selenium groq
```

Individual scripts may require only a subset of these dependencies.

---

## Configuration

Credentials should be supplied through environment variables or a local untracked credential file.

Typical WooCommerce configuration:

```text
WC_URL=https://your-store.example
WC_BASE_URL=https://your-store.example/wp-json/wc/v3
WC_CONSUMER_KEY=ck_...
WC_CONSUMER_SECRET=cs_...
```

For AI-assisted generation:

```text
GROQ_API_KEY=...
```

### Never commit secrets

Do not commit:

```text
.env
credentials.txt
API keys
WooCommerce consumer secrets
private exports containing sensitive information
```

---

## Typical Workflow

```text
1. Export WooCommerce
        ↓
2. Classify catalog
        ↓
3. Analyze metadata
        ↓
4. Resolve missing artwork
        ↓
5. Audit SEO
        ↓
6. Generate content
        ↓
7. Review CSV outputs
        ↓
8. Publish approved changes
        ↓
9. Re-audit WooCommerce
```

This separation makes the process easier to inspect, reproduce and recover than a single monolithic synchronization script.

---

## Safety Model

| Operation | Default behavior |
|---|---|
| Product export | Read-only |
| Catalog classification | Read-only |
| Metadata analysis | Read-only |
| SEO audit | Read-only |
| Cover import | Dry-run / controlled processing |
| AI content generation | Generates reviewable CSV |
| AI publication | Explicit `--aplicar` |
| Catalog deletion | Dry-run |
| Permanent deletion | Explicit `--execute --force` |

This is especially important for e-commerce automation because an incorrect match can affect inventory, product visibility, URLs, SEO and customer-facing content.

---

## Repository Structure

```text
hpp/
├── catalog_classifier.py
├── catalog_metadata_analyzer.py
├── export_products.py
├── generar_descripciones_gemini_v2.py
├── music_cover_importer_v2.11.1.py
├── seo_catalog_audit_v2.py
└── sync_woocommerce_con_hpp.py
```

Generated CSV reports and local credentials are runtime data rather than source code.

---

## Example Commands

Export the WooCommerce catalog:

```bash
python export_products.py
```

Classify products:

```bash
python catalog_classifier.py
```

Analyze catalog metadata:

```bash
python catalog_metadata_analyzer.py
```

Run a limited AI generation test:

```bash
python generar_descripciones_gemini_v2.py --limite 5
```

Run the SEO audit:

```bash
python seo_catalog_audit_v2.py
```

Generate a catalog synchronization report without modifying WooCommerce:

```bash
python sync_woocommerce_con_hpp.py
```

Only after reviewing the generated report:

```bash
python sync_woocommerce_con_hpp.py --execute
```

---

## Important Limitations

This repository is an automation toolkit built around a specific WooCommerce installation and HPP catalog structure. It is **not presented as a generic WooCommerce plugin**.

Several workflows depend on external services and site-specific conventions, including:

- WooCommerce REST API
- MusicBrainz
- Cover Art Archive
- Metal Archives
- Yoast
- Groq
- HPP catalog XLSX structure
- HPP product naming conventions

External APIs can change, rate-limit requests or return incomplete information. The scripts therefore include retry handling, review states and dry-run paths where appropriate.

The repository should be treated as a **portfolio representation of the engineering workflow**, not as a drop-in automation framework for arbitrary WooCommerce stores.

---

## Why This Project Matters

This project demonstrates more than simple scripting.

It combines:

- REST API integration
- E-commerce automation
- ETL-style catalog processing
- Data normalization
- Fuzzy matching
- External metadata enrichment
- Web research
- Image validation
- SEO auditing
- AI-assisted content pipelines
- Batch API operations
- Error isolation
- Retry strategies
- Dry-run safety
- Human-in-the-loop workflows
- Structured reporting
- Security-conscious credential handling

The core challenge was not simply **"automate WooCommerce"**, but building a workflow capable of handling imperfect real-world catalog data without turning uncertainty into silent data corruption.

---

## Project Website

**Humanity's Plague Productions**

https://humanitysplagueprod.com/

---

## Author

Developed and maintained by **Matías Agustín Galeano**.

Focused on **IT operations, automation, e-commerce systems, WordPress/WooCommerce and practical software solutions**.

---

## License

No open-source license is currently declared for this repository.

Unless a license is added to the repository, the source code should not be assumed to be freely reusable or redistributable.

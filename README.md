# pulso-nexus

Motor de inteligencia operativa: modular, reproducible e inmutable.

**Estado: E1-A (núcleo reproducible)**, pendiente de revisión independiente.
E1-B y E1-C no están autorizadas. En esta etapa no hay SQL, ni conexiones corporativas,
ni credenciales, ni datos reales.

## Qué contiene E1-A

| Módulo | Responsabilidad |
|---|---|
| `nexus.contracts` | Contratos versionados con huellas fijadas (`fingerprints.json`) |
| `nexus.hashing` | `content_hash` (bytes) y `logical_hash` canónico (`nexus.logical/1`) |
| `nexus.storage` | Almacén de objetos inmutables: staging, `fsync`, publicación **sin reemplazo**, verificación al leer |
| `nexus.snapshots` | Capturas inmutables con manifiesto autosellado |
| `nexus.connectors` | Solo `synthetic_file` (allowlist). SharePoint y Graph están excluidos |
| `nexus.normalize` | Normalizador sintético puro, con rechazos con motivo y conservación de filas |
| `nexus.engines` | Motor **sintético no comercial** `synthetic_sum` (puro y determinista) |
| `nexus.pipeline` | `run`, `replay` (L1/L2/L3) y `verify`: único integrador |
| `nexus.cli` | `nexus snapshot`, `run`, `replay` y `verify` |

Arquitectura y decisiones: `docs/architecture/E0.md`, `docs/decisions-register.md` y `docs/adr/`.
Evidencia de E1-A: `docs/evidence/E1-A/REPORT.md`.

## Reproducir desde una clonación limpia

Requisitos: `git`, [`uv`](https://docs.astral.sh/uv/) y Python 3.12 (`uv python install 3.12`).

```bash
git clone --branch claude/blissful-feynman-obiif0 <url-del-repositorio> pulso-nexus
```
```bash
cd pulso-nexus
```
```bash
uv sync --frozen
```
```bash
uv run ruff check . && uv run ruff format --check .
```
```bash
uv run mypy --strict src
```
```bash
uv run lint-imports
```
```bash
uv run pytest --cov=nexus --cov-branch --cov-fail-under=90
```
```bash
uv run pytest tests/reproducibility tests/adversarial tests/architecture -v
```
```bash
NEXUS_THREADS=1 TZ=UTC LC_ALL=C uv run pytest tests/reproducibility -v
```
```bash
NEXUS_THREADS=8 TZ=America/Mexico_City LC_ALL=C.UTF-8 uv run pytest tests/reproducibility -v
```
```bash
bash scripts/e1a_demo.sh
```
```bash
sha256sum -c docs/evidence/E1-A/MANIFEST.sha256
```

## Uso manual o programado (mismo comando)

```bash
uv run nexus snapshot --store ./.nexus-local --path tests/fixtures/synthetic/input_v1.csv
```
```bash
uv run nexus run --store ./.nexus-local --snapshot <snapshot_id> --as-of 2026-12-31T00:00:00Z
```
```bash
uv run nexus replay --store ./.nexus-local --run-id <run_id> --level L3
```
```bash
uv run nexus verify --store ./.nexus-local --all
```

Un planificador solo debe invocar `nexus run … --trigger scheduled`. El código que se
ejecuta es el mismo que en la ejecución manual.

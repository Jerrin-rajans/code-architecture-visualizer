# ArchLens

[![CI](https://github.com/Jerrin-rajans/code-architecture-visualizer/actions/workflows/ci.yml/badge.svg)](https://github.com/Jerrin-rajans/code-architecture-visualizer/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> The repository is `code-architecture-visualizer`; the package, CLI command and
> import name are all `archlens`.

Point it at any codebase. It reads the source, the dependency manifests and the
infrastructure-as-code, works out what the system actually is, and draws it:

- **Architecture diagrams** with real AWS / Azure / GCP / Kubernetes iconography,
  rendered through Graphviz to PNG and SVG.
- **Flowcharts and sequence diagrams** (Mermaid) showing how requests and data
  actually move through the system.
- **A browsable evidence trail** — every component links back to the files that
  prove it exists.

Analysis is hybrid. Static analysis establishes the facts; Claude interprets
them. Without an API key the static half still runs and still produces diagrams.

---

## Install

```bash
# from the project root
pip install -e .

# the Claude pass (optional but recommended)
pip install anthropic
```

Graphviz is a native binary and must be installed separately:

```bash
conda install -c conda-forge graphviz     # conda
winget install Graphviz.Graphviz          # Windows
brew install graphviz                     # macOS
sudo apt install graphviz                 # Debian/Ubuntu
```

Check everything landed:

```bash
archlens doctor
```

### Finding Graphviz

`dot` does not need to be on `PATH`. ArchLens looks, in order, at
`$ARCHLENS_GRAPHVIZ_BIN`, a `.tools/graphviz/bin` folder beside the project,
`%ProgramFiles%\Graphviz*\bin`, `$CONDA_PREFIX/Library/bin`, and the usual Unix
locations — and prepends whatever it finds to `PATH` for its own process. To
point it somewhere explicit:

```bash
set ARCHLENS_GRAPHVIZ_BIN=D:\tools\graphviz\bin     # Windows
export ARCHLENS_GRAPHVIZ_BIN=/opt/graphviz/bin      # macOS / Linux
```

### If the install fights back (Windows / Anaconda)

Two failures are common enough to be worth naming:

- **`conda install` into the Anaconda base env fails** with
  `RemoteError: 'truststore' is a dependency of conda and cannot be removed`.
  The transaction rolls back, so nothing is linked. Install into a fresh env
  instead — `conda create -n graphviz -c conda-forge graphviz` — and point
  `ARCHLENS_GRAPHVIZ_BIN` at that env's `Library\bin`.
- **The Graphviz plugins load but nothing draws.** If `dot -c` reports
  `Could not load gvplugin_pango.dll` / `gvplugin_gd.dll`, the renderer cannot
  decode the icon PNGs and you get a diagram of labelled boxes with no icons.
  It is almost always one missing DLL in the same folder as `dot.exe` —
  `cairo.dll`, `libjpeg.dll` or `libffi.dll`. Re-run `dot -c` after adding it;
  a healthy run prints no warnings and writes a non-empty `config6`.

To enable the Claude pass:

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."       # Windows, new shell after
export ANTHROPIC_API_KEY="sk-ant-..."     # macOS / Linux
```

---

## Use

### Web UI

```bash
archlens serve
```

Opens `http://localhost:8420`. Paste or browse to a repository, hit **Analyse
codebase**, and you get five tabs: the architecture diagram (pan/zoom,
downloadable), the flowcharts, a component breakdown, the detected tech stack,
and the raw evidence.

### CLI

```bash
archlens scan ./my-project
archlens scan ./my-project --no-llm --dark -o ./docs/diagrams
```

Writes `architecture.png`, `architecture.svg`, `context.png`, `architecture.json`
and one `.mmd` file per flowchart. The JSON model is stable enough to diff in
CI, which makes "did this PR change the architecture?" a mechanical question.

### As a library

```python
from archlens.pipeline import analyse

result = analyse("./my-project", "./out", use_llm=True)

print(result.architecture.summary)
for component in result.architecture.components:
    print(f"{component.name:28} {component.service:24} {component.paths}")
```

---

## How it works

```
repository
    │
    ├── walker      prune vendored/generated dirs, honour .gitignore
    ├── manifests   requirements.txt, pyproject, package.json, pom.xml, go.mod, …
    ├── code        Python AST; regex passes for TS/Java/Go/C# — imports, routes, calls
    ├── iac         Terraform, CloudFormation/SAM, Kubernetes, Compose, Bicep/ARM, Serverless
    │
    ▼
 Evidence ─────────── strictly factual; every item cites a file
    │
    ├── heuristics  rule-based component & edge model  (always runs)
    └── llm         Claude refines it                  (optional)
    │
    ▼
 Architecture
    │
    ├── diagrams + Graphviz  →  PNG / SVG
    └── Mermaid              →  flowcharts, sequence diagrams
```

The layer split is the point. `Evidence` never contains an interpretation, so
the UI can show you what was found separately from what was concluded — and a
hallucinated component is immediately visible as one with no `paths`.

### What it detects

| Category | Examples |
|---|---|
| Cloud services | Lambda, ECS, EKS, RDS, Aurora, DynamoDB, S3, SQS, SNS, Kinesis, API Gateway, CloudFront, Cognito; App Service, Functions, AKS, Cosmos DB, Service Bus, Event Hubs, Blob Storage, Key Vault; Cloud Run, GKE, Pub/Sub, BigQuery, Firestore, Spanner |
| Datastores | PostgreSQL, MySQL, MongoDB, Redis, Cassandra, SQL Server, Oracle, ClickHouse, Neo4j, Elasticsearch |
| Messaging | Kafka, RabbitMQ, NATS, ActiveMQ, Celery, SQS/SNS, Service Bus, Pub/Sub |
| Frameworks | FastAPI, Flask, Django, Express, NestJS, Next.js, React, Vue, Angular, Spring Boot, Rails, Laravel |
| Infrastructure | Terraform, CloudFormation, SAM, Bicep/ARM, Kubernetes, Helm, Docker, Compose, Serverless Framework |
| CI/CD | GitHub Actions, GitLab CI, Jenkins, Azure Pipelines, CircleCI |

Detection is evidence-weighted: a Terraform `aws_rds_cluster` (0.95 confidence)
outranks a loose `psycopg2` import (0.75), so the diagram says "Amazon Aurora"
rather than "a SQL database".

---

## Cost

The Claude pass makes two calls per scan against `claude-opus-5`. Both share a
cached prefix — the frozen instruction block is identical across every scan, and
the per-repository evidence block is shared between the two calls — so the
second call reads most of its input from cache at a tenth of the price.

A typical mid-sized repository costs a few cents. `--no-llm` costs nothing and
still produces diagrams.

---

## Project layout

```
archlens/
├── models.py              Pydantic domain model (Evidence → Architecture → output)
├── catalog.py             Detection rules: deps/imports/IaC types → canonical service keys
├── pipeline.py            scan → infer → render
├── scanner/
│   ├── walker.py          traversal and file filtering
│   ├── manifests.py       dependency manifest parsers
│   ├── code.py            per-language source analysis
│   └── iac.py             infrastructure-as-code parsers
├── inference/
│   ├── heuristics.py      rule-based architecture derivation
│   ├── flows.py           Mermaid generation
│   ├── prompts.py         prompt construction + cache layout
│   └── llm.py             Claude calls, response coercion
├── render/
│   ├── icons.py           canonical key → diagrams node class
│   ├── architecture.py    Graphviz rendering
│   └── theme.py           visual styling
└── server/
    ├── app.py             FastAPI API
    └── static/            the web UI (no build step)
```

### Extending the catalog

To teach it a new service, add one row to [`catalog.py`](archlens/catalog.py)
and one to [`icons.py`](archlens/render/icons.py):

```python
# catalog.py
TERRAFORM_RULES["aws_mq_broker"] = ("aws.mq", "Amazon MQ", K.QUEUE)

# render/icons.py
ICON_REGISTRY["aws.mq"] = [("diagrams.aws.integration", "MQ")]
```

Icon entries are candidate *lists* — the first importable one wins, so a name
that only exists in newer `diagrams` releases degrades to the next candidate
instead of breaking the render.

### Icons for services `diagrams` doesn't ship

`diagrams` has no icon for Stripe, SendGrid, vector databases and a handful of
others. ArchLens ships its own tiles for those in
[`render/assets/`](archlens/render/assets/), generated by
[`tools/generate_icons.py`](tools/generate_icons.py) and mapped in
`CUSTOM_ICONS`. A custom tile takes precedence over the registry, so these
render properly instead of falling back to a blank box.

They are **role glyphs in brand colours, not vendor logos** — a card for
payments, an envelope for email, a cylinder for a datastore. Reproducing a real
logo is a trademark problem; substituting a *different* company's logo is worse.
(This project shipped Facebook's mark for Stripe at one point, which is what
prompted the rule.)

To add one, draw a glyph in the generator, add it to `ICONS`, re-run the script,
and map the service key:

```python
# tools/generate_icons.py
ICONS["datadog"] = (paw_glyph, "#632CA6")

# render/icons.py
CUSTOM_ICONS["saas.datadog"] = "datadog"
```

---

## Tests

```bash
python -m pytest tests/ -q
```

[`tests/test_regressions.py`](tests/test_regressions.py) pins the failures found
by dogfooding — each one produced a plausible-looking diagram that was quietly
wrong, which is exactly the class of bug worth guarding:

| Guard | The bug it pins |
|---|---|
| BOM-prefixed manifests | A UTF-8 BOM made `package.json` parse to zero dependencies, silently dropping the frontend |
| Probe confidence | A regex hit in source invented infrastructure — scanning ArchLens reported "runs on AWS" because its own catalog contains `s3://` |
| Probe corroboration | The fix above initially over-corrected, so a real `boto3.client("s3")` call stopped registering |
| Documentation exclusion | A README *mentioning* a technology counted as *using* it |
| Test-code exclusion | `boto3.client("s3")` as a string literal in this project's own test fixtures re-created the same false positive |
| Comment stripping | A comment *explaining* the S3 probe matched the S3 probe |
| Quote-aware stripping | A naive stripper eats `"#fff"` in JS and truncates every `https://` URL |
| Provider consistency | The diagram was labelled Azure while containing no Azure component |
| SVG inlining | Graphviz emitted absolute `C:\...` paths, so every icon vanished when the SVG was served over HTTP |
| Vendor logos | `saas.stripe` resolved to Facebook's icon |

### The rule these encode

Evidence has tiers, and only the strong tier may create a component:

| Tier | Source | Confidence | Can create a component? |
|---|---|---|---|
| Declared | IaC resource | 0.95 | yes |
| Declared | Dependency manifest | 0.85 | yes |
| Used | Import statement | 0.75 | yes |
| Used | SDK call shape (`boto3.client("s3")`) | 0.80 | yes |
| Mentioned | URL or hostname (`s3://`) | 0.60 | corroborates only |
| Non-production | Anything in test code | ≤ 0.65 | corroborates only |
| Prose | Comments, docstrings, Markdown | — | ignored entirely |

Weak evidence still appears in the **Evidence** tab with the file that produced
it — it just doesn't get to draw a box.

---

## Limitations

- Non-Python languages are analysed with regexes, not parsers. Imports and route
  decorators are found reliably; call graphs are not.
- Runtime-only topology (service discovery, dynamic config, feature flags) is
  invisible to static analysis. If a connection is never named in code or IaC,
  it will not appear.
- Very large monorepos hit the 20,000-file walk cap and 4,000-file deep-analysis
  cap; the scan still completes and says so in the notes.

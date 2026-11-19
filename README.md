# InsightAnalyst

InsightAnalyst is an autonomous, natural-language business analytics agent built in Python over a SQLite Northwind database. It answers *"what happened and why"* questions (variance, root-cause, ranking, and trend analysis), plans read-only SQL queries, enforces database sanity checks, self-heals broken queries, and produces verifiable reports where every single number is bound to an executed query ID (`[Q1]`, `[Q2]`).

---

## Project Structure

```text
InsightAnalyst/
├── agent/
│   ├── contribution.py    # Pure Python variance, driver ranking & reconciliation analysis
│   ├── llm.py             # LLM wrapper with jittered backoff, Ollama fallback & response caching
│   ├── loop.py            # Main autonomous loop with retry caps, query repair & drill-down
│   ├── narrative.py       # Zero-hallucination narrative generator with regex audit & code fill
│   ├── planner.py         # Structured JSON planner with clarification & refusal guards
│   └── timewindow.py      # Relative time window resolver (anchored to dataset max date)
├── data/
│   └── northwind.db       # Modernized SQLite Northwind database (with dim_country_region)
├── knowledge/
│   ├── build_index.py     # Persistent ChromaDB indexer for schema, metrics, and business rules
│   ├── metrics.yaml       # Formalized definitions (revenue, units_sold, order_count, AOV)
│   ├── retrieve.py        # Semantic retriever returning ranked context chunks
│   ├── rules.yaml         # Business rules (time anchoring, region join, reconciliation)
│   └── schema_notes.md    # Granular table notes, primary keys, relationships, and gotchas
├── migrations/
│   └── 001_create_dim_country_region.py # Country-to-region mapping table (Europe, North, South)
├── prompts/
│   ├── narrative.txt      # Prompt enforcing placeholder-only narratives ({Finding.val})
│   ├── planner.txt        # Prompt guiding JSON query planning and constraint compliance
│   └── repair.txt         # Prompt for diagnostic query repair on execution/validation errors
├── scripts/
│   ├── check_demo.py      # Strict offline regression checker (CACHE_MODE=readonly)
│   ├── evaluate_golden_set.py # Automated 18-question golden set accuracy & traceability benchmark runner
│   ├── run_once.py        # Single-query CLI inspection pipeline
│   └── warm_cache.py      # Cache pre-warmer seeding .cache/llm/ for demo questions
├── tests/                 # Automated test suite (116 offline unit & integration tests)
│   ├── golden_set.json    # 18 hand-verified business benchmark questions with ground truth SQL
│   └── golden_evaluation_report.md # Generated evaluation scorecard (100% accuracy)
├── app.py                 # Streamlit web application with live trace & audit accordions
├── contracts.py           # Shared dataclasses (Plan, QueryResult, ValidationResult, Finding, AgentRun)
├── executor.py            # Read-only SQLite query executor with query-only mode and authorizer
├── reporter.py            # Traceable Markdown report generator & Plotly visualization engine
├── validator.py           # Multi-point result validation (nulls, empty, date window, reconciliation)
├── requirements.txt       # Minimal Python dependencies
└── .env.example           # Environment variable template
```

---

## Agent Architecture & Execution (Person A)

### 1. Knowledge Layer & Retrieval
The agent grounds all reasoning in domain knowledge to prevent hallucinations:
- **`knowledge/schema_notes.md`**: Granular table-by-table schema documentation for 6 tables (`[Order Details]`, `Orders`, `Customers`, `Products`, `Categories`, and `dim_country_region`). Gotchas are explicitly stated: table names with spaces require brackets, `Freight` is shipping cost and not revenue, and customer geography joins exclusively through `dim_country_region`.
- **`knowledge/metrics.yaml`**: Defines mathematical formulas in SQLite syntax for 4 standard business metrics:
  - `revenue`: `SUM(od.UnitPrice * od.Quantity * (1 - od.Discount))` (Freight strictly excluded).
  - `units_sold`: `SUM(od.Quantity)`.
  - `order_count`: `COUNT(DISTINCT o.OrderID)`.
  - `average_order_value`: Total discounted revenue divided by distinct order count.
- **`knowledge/rules.yaml`**: Formal business rules:
  - `rule_time_anchoring`: Relative dates like "last month" anchor to the last complete calendar month in the dataset (`2023-09`), not today's system date.
  - `rule_region_join`: Every customer belongs to exactly one sales region via `dim_country_region` joined on `Customers.Country`.
  - `rule_reconciliation`: Sub-dimension breakdowns must sum to the aggregate total within a 0.01 tolerance.
  - `rule_revenue_definition`: Revenue must never include Freight charges.
- **`knowledge/retrieve.py`**: Semantic retriever querying ChromaDB (`cosine` distance, default $k=6$) returning chunk id, text, type (`table` | `metric` | `rule`), and distance.

### 2. How to Rebuild the Index
The ChromaDB vector index stores 14 distinct chunks embedded with `all-MiniLM-L6-v2`:
```bash
python3 knowledge/build_index.py
```
- **Idempotent**: Safely deletes and recreates the `insight_knowledge` collection without duplicate records.
- **Offline Capable**: Downloads the lightweight embedding model (~80 MB) on the first invocation to `~/.cache/chroma/onnx_models/` and reuses it completely offline thereafter.

### 3. LLM Configuration & Fallback Architecture
The LLM integration ([agent/llm.py](agent/llm.py)) provides resilient execution across free-tier and offline providers:
- **Configuration** (configured in `.env`):
  ```ini
  LLM_PROVIDER=gemini        # Options: "gemini", "groq", "ollama", "fake"
  GEMINI_API_KEY=AIzaSy...    # Google Gemini API key
  GROQ_API_KEY=gsk_...        # Groq API key
  OLLAMA_BASE_URL=http://localhost:11434
  OLLAMA_MODEL=llama3.2:3b
  ```
- **Rate-Limit Resilience**: Retries on rate limits (`429` / `ResourceExhausted`) using exponential backoff with randomized jitter (maximum 4 attempts, 30-second timeout per attempt).
- **Ollama Fallback**: If the primary cloud provider encounters network errors or timeouts and Ollama is configured, the agent automatically falls back to the local Ollama instance and logs the event.
- **Fake Provider**: Setting `LLM_PROVIDER=fake` (using canned responses in `tests/canned_plans.json`) enables 100% offline development, testing, and continuous integration without using API quota.
- **Security**: API keys are loaded strictly from environment variables and never logged or serialized.

### 4. Response Caching Mechanics
LLM calls are cached on disk in `.cache/llm/` to ensure zero-latency, deterministic demo runs:
- **Cache Key**: SHA-256 hash across `provider:model:system:prompt:temperature`.
- **Modes** (controlled via `CACHE_MODE` environment variable):
  - `readwrite` (default): Checks cache first; on miss, executes the LLM call and writes the response payload to disk.
  - `readonly` (demo & presentation mode): Strictly serves cached files. Any cache miss immediately raises a clear `LLMError("Cache miss in readonly mode...")` without touching the network.
  - `off`: Bypasses on-disk caching entirely.
- **Pre-warming & Checking**:
  - `python3 scripts/warm_cache.py`: Runs demo questions in `readwrite` mode once to populate `.cache/llm/`.
  - `python3 scripts/check_demo.py`: Evaluates all demo questions in `readonly` mode and audits numerical evidence citations.

### 5. Agent Loop Execution & Budget Caps
The core orchestrator ([agent/loop.py](agent/loop.py)) manages the pipeline with strict safety boundaries:
- **Budget Caps**:
  - Maximum **8 total queries** per question (`MAX_TOTAL_QUERIES = 8`).
  - Maximum **3 retries** per query (`MAX_RETRIES_PER_QUERY = 3`).
- **Query Repair Loop**:
  - If a query fails execution (syntax error, missing column) or fails a validation sanity check (`fail` or `suspicious`), the loop calls `repair_query()` with diagnostic context and schema chunks.
  - The LLM returns a corrected single `SELECT` query. If 3 repair attempts fail, the query is marked failed, and the loop proceeds if remaining queries can support a partial answer. Numbers from failed queries are **never** presented in findings.
- **Contribution Analysis & Dynamic Drill-Down**:
  - In Q1, the agent compares baseline performance across time windows.
  - [agent/contribution.py](agent/contribution.py) computes absolute change, percentage change, and contribution percentages across dimension items in pure Python.
  - Time columns (`month`, `ordermonth`, `date`) are excluded from driver rankings so only true business entities (products, categories, regions, countries) are identified as drivers.
  - Automatically generates dynamic drill-down query `Q2` targeting top contributors, dynamically adapting `ORDER BY ... DESC` (for top/highest requests) or `ASC` (for bottom/drop root-causes), bounded by the planned time window.
- **Trace Streaming**:
  - Emits real-time `TraceEvent` records (`plan`, `query`, `check`, `retry`, `drilldown`, `done`, `error`) for live UI progress bars and audit logs.

### 6. Zero-Hallucination Guard & Narrative Placeholders
To guarantee that the LLM never hallucinates numerical figures:
- **Placeholder Rule**: The narrative prompt instructs the LLM to write summaries using **only** placeholders (e.g., `{F_headline.total_abs_change}`, `{driver_1.dimension_value}`, `[{query_id}]`).
- **Regex Audit**: [agent/narrative.py](agent/narrative.py) scans the LLM output with a regex to verify that **no naked digits** exist outside curly braces `{...}`.
- **Deterministic Fallback**: If naked digits are detected, the agent regenerates once with an explicit warning. If digits persist, it falls back to a deterministic, code-assembled summary template.
- **Pure Python Fill**: All placeholders are filled by code using verified values stored in `Finding.values`.

### 7. Known Limitations
- **Single-Dimension Drill-Down**: Automatically drills down along one primary dimension (`plan.drill_down_dimensions[0]`) rather than exploring multi-dimensional hierarchies simultaneously.
- **Static Time Anchoring**: Anchored to the modernized Northwind dataset (`2023-09` as the last complete calendar month, `2023-08` as comparison baseline).
- **Read-Only Scope**: Any destructive statements (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `CREATE`, `ATTACH`, `DETACH`, `PRAGMA`) are blocked at both planner and executor layers.

### 8. Not Implemented
- Multi-turn conversational memory (each `run_agent()` call is stateless).
- Automated weekly review cron execution (`weekly.py` - Phase 5 stretch goal).
- Direct database schema alterations or migrations through LLM prompts.

---

## Data, Trust & Delivery Architecture (Person B)

### 1. Secure SQL Execution & Authorizer (`executor.py`)
- **Strict Read-Only Connection**: SQLite opened in URI read-only mode (`file:data/northwind.db?mode=ro`).
- **SELECT-Only Guard**: Queries must begin strictly with `SELECT` or `WITH`. Rejects multiple statements (semicolon attacks), `PRAGMA`, `ATTACH`, `DETACH`, and all DML/DDL write keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`).
- **Audit Logging**: Any blocked execution attempts are captured in `BlockedQueryLog` without halting the system.

### 2. Multi-Point Result Validation (`validator.py`)
Runs 6 automated sanity checks on every executed query:
1. **Execution Check**: Captures syntax errors and missing tables/columns.
2. **Empty Result Check**: Flags suspicious 0-row returns.
3. **Null Value Check**: Audits results for missing or null data.
4. **Date Window Check**: Verifies returned dates fall within the requested period.
5. **Metric Definition Check**: Validates that required formula expressions exist in the SQL.
6. **Reconciliation Check**: Ensures dimension breakdowns sum to the aggregate total within a $\pm 0.01$ tolerance.

### 3. Traceable Business Reporter (`reporter.py`)
- **Zero-Hallucination Delivery**: Numbers appear only in `Finding.values` and are filled into templates by Python code. Every number stated in the report is bound to an executed query citation (e.g. `[Q1]`, `[Q2]`).
- **Report Specification**:
  1. *Headline Answer* (with `[Q1]` citation).
  2. *Key Drivers* (ranked contributors with `[Q#]` citations).
  3. *Interactive Visualizations* (Trend Chart & Driver Breakdown).
  4. *Assumptions & Business Rules* (date anchoring, formulas, region join).
  5. *Evidence Appendix* (executed SQL, result tables, validation checks).
- **Plotly Visualizations**:
  - `generate_trend_chart`: Period-over-period comparison bar/line chart.
  - `generate_driver_chart`: Horizontal bar chart ranking dimensional contributors.
  - Generates lightweight `ChartSpec` fallbacks in minimal environments.

### 4. Interactive Streamlit Dashboard (`app.py`)
- **Question Input**: Natural language prompt input with 6 quick-launch demo and guardrail presets.
- **Live Reasoning Trace**: Real-time visual stepper tracking `TraceEvent` milestones (`plan` → `query` → `check` → `retry` → `drilldown` → `done`).
- **Visual Analytics**: Interactive Plotly figures alongside headline KPI cards.
- **Auditable Query Accordions**: Deep inspection of executed SQL statements, row counts, data tables, and sanity check results per query ID.
- **Report Download**: Instant export of traceable Markdown reports (`insightanalyst_report.md`).

### 5. 18-Question Golden Benchmark Set & Evaluation Runner (`tests/golden_set.json` & `scripts/evaluate_golden_set.py`)
- **Ground Truth Grounding**: 18 benchmark questions with hand-verified reference SQL and expected numbers computed directly from Northwind SQLite.
- **Mandatory Trap Cases Tested**:
  - *Relative Date Window*: Anchoring "last month" to September 2023 (`2023-09`).
  - *Join Fan-Out*: Proving single-region mapping via `dim_country_region` avoids revenue duplication.
  - *Ambiguous Query*: Vague question triggers clarification request (`needs_clarification=True`).
  - *Write Refusal*: Malicious query triggers safety refusal (`refused=True`).
- **Automated Scorecard**: Measures numeric accuracy ($\ge 85\%$), query traceability ($100\%$), and write guardrail blocks ($100\%$). Scorecard published to `tests/golden_evaluation_report.md`.

---

## Setup & Installation

### 1. Clone the Repository
```bash
git clone https://github.com/nyx-suk/InsightAnalyst.git
cd InsightAnalyst
```

### 2. Create and Activate Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Database Setup & Migration
Download the modernized Northwind SQLite database and run the region mapping migration:
```bash
curl -L -o data/northwind.db https://github.com/jpwhite3/northwind-SQLite3/raw/main/dist/northwind.db
python3 migrations/001_create_dim_country_region.py
```
*Verify: Confirm 21 countries are mapped across Europe (15), North (3), and South (3).*

### 5. Build Knowledge Base
```bash
python3 knowledge/build_index.py
```

### 6. Configure Environment
```bash
cp .env.example .env
# Open .env and add your GEMINI_API_KEY or GROQ_API_KEY
```

---

## Running the Agent & Verification

### Launch Interactive Streamlit Web App
```bash
streamlit run app.py
```

### Run Single Query Pipeline
```bash
python3 scripts/run_once.py "Why did sales drop in the South region last month?"
```

### Pre-warm Cache for Demo Rehearsal
```bash
python3 scripts/warm_cache.py
```

### Run Offline Regression & Citation Audit
Runs 100% offline with `CACHE_MODE=readonly`:
```bash
python3 scripts/check_demo.py
```

### Run 18-Question Golden Benchmark Evaluation
Runs the full evaluation benchmark and writes `tests/golden_evaluation_report.md`:
```bash
python3 scripts/evaluate_golden_set.py
```

### Run Full Test Suite
Executes all 116 offline unit and integration tests:
```bash
python3 -m unittest discover -v
```

---

## Security Review & Licensing Compliance

- **Zero Hardcoded Secrets**: Comprehensive regex scans across the entire git commit history confirmed 0 leaked keys or credentials. Real keys reside strictly in untracked `.env` files.
- **Read-Only Database Enforcement**: All 15 security tests pass in `tests/test_executor.py`. DDL (`DROP`, `ALTER`, `CREATE`), DML (`INSERT`, `UPDATE`, `DELETE`), stacked statements, `PRAGMA`, and database attachment exploits are strictly blocked.
- **Privacy & No PII**: Logs record only operational metadata (query latency, cache hit/miss, query IDs). No user personal identifiers are stored.
- **Open-Source Licenses**:
  - `sqlite3`: Public Domain (Python Standard Library)
  - `chromadb`: Apache License 2.0
  - `streamlit`: Apache License 2.0
  - `plotly`: MIT License
  - `pyyaml`: MIT License
  - `python-dotenv`: BSD-3-Clause
  - `google-genai`: Apache License 2.0
  - `Northwind Dataset`: Permissive public sample dataset

---

## Contributors
1. **Srigadde Sukrutha** (1CR24CS186) — *Person A: Agent Brain (Contracts, LLM Wrapper, Planner, Loop, Narrative, Retrieval)*
2. **Harini RK** (1CR24CS071) — *Person B: Data & Delivery (Database, Executor, Validator, Reporter, UI)*
3. **Archanaa A M** (1CR24CS030)
4. **Hannan Israili** (1CR24CS070)

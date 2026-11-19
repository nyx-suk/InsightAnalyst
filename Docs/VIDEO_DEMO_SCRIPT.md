# InsightAnalyst: Hackathon Video Demo Script (PS 37)
**Target Duration:** 3.5 to 4.0 Minutes  
**Focus:** Person A (Agent Brain) & End-to-End System Integration  
**Tone:** Confident, technical, crisp, and business-focused

---

## Pre-Recording Checklist & Terminal Setup

Before you press record, set up your screen for maximum clarity:
1. **Terminal Font Size:** Increase font to `16pt` or `18pt` so code and logs are easily readable on video.
2. **Clean Terminal:** Run `clear` before each scene.
3. **Environment Prep:**
   - Ensure you are in the project root: `cd /Users/sukruthasrigadde/stuff/AgentForge`
   - Activate your virtual environment if applicable: `source venv/bin/activate`
   - If recording offline without relying on network latency, prepend commands with `LLM_PROVIDER=fake` or set `CACHE_MODE=readonly`.

---

## Scene-by-Scene Video Script

### Act 1: The Hook & The Problem (0:00 – 0:35)

| Timestamp | Visual / Screen Action | Spoken Audio / Voiceover |
| :--- | :--- | :--- |
| **0:00 – 0:15** | **Screen:** Title slide or README header showing `InsightAnalyst (PS 37): Autonomous Business Analytics Agent`. | *"Hi everyone, this is InsightAnalyst—an autonomous analytics agent built over a SQLite Northwind database for Problem Statement 37."* |
| **0:15 – 0:35** | **Screen:** Switch to VS Code showing [README.md](file:///Users/sukruthasrigadde/stuff/AgentForge/README.md) or the architecture diagram. | *"Most text-to-SQL agents fail in production because of two fatal flaws: first, LLMs hallucinate numbers that don't match the database; and second, fragile queries break on schema quirks. InsightAnalyst solves both. It retrieves grounded business definitions, plans read-only SQL, self-heals broken queries, automatically drills down into root causes, and enforces a strict **zero-hallucination guarantee** where every single number traces to an executed query ID."* |

---

### Act 2: Architecture & Knowledge Layer (0:35 – 1:15)

| Timestamp | Visual / Screen Action | Spoken Audio / Voiceover |
| :--- | :--- | :--- |
| **0:35 – 0:55** | **Screen:** Quick pan over [knowledge/](file:///Users/sukruthasrigadde/stuff/AgentForge/knowledge): open [schema_notes.md](file:///Users/sukruthasrigadde/stuff/AgentForge/knowledge/schema_notes.md) and [metrics.yaml](file:///Users/sukruthasrigadde/stuff/AgentForge/knowledge/metrics.yaml). | *"I am Person A on our team, responsible for the Agent Brain: the knowledge layer, LLM wrapper, planner, and loop. Everything starts in our knowledge layer. Instead of feeding raw schema dumps, we use ChromaDB to retrieve curated domain rules.*<br><br>*For example, in `metrics.yaml`, revenue is formally defined as `UnitPrice * Quantity * (1 - Discount)`—strictly excluding shipping freight. In `schema_notes.md`, we document critical database gotchas, like bracket-escaping `[Order Details]` and joining sales regions via `dim_country_region`."* |
| **0:55 – 1:15** | **Screen:** Show [agent/llm.py](file:///Users/sukruthasrigadde/stuff/AgentForge/agent/llm.py) highlighting the retry backoff and caching mechanics. | *"Our LLM wrapper handles rate-limit retries with jittered exponential backoff, includes a local Ollama fallback, and features disk caching in `.cache/llm/` to ensure zero API latency and zero demo risk."* |

---

### Act 3: Live Pipeline Execution & Time Anchoring (1:15 – 2:05)

| Timestamp | Visual / Screen Action | Spoken Audio / Voiceover |
| :--- | :--- | :--- |
| **1:15 – 1:25** | **Screen:** Terminal. Clear screen, then run: `python3 scripts/run_once.py "Why did sales drop in the South region last month?"` *(or with `LLM_PROVIDER=fake`)* | *"Let's see the agent in action on a real business question: 'Why did sales drop in the South region last month?'"* |
| **1:25 – 1:45** | **Screen:** Terminal output scrolling. Highlight Step 1 (Retrieval) and Step 3 (Time Window). | *"Notice what happens in step 1: ChromaDB retrieves the exact metric formula and regional rules. In step 3, our time-window resolver anchors 'last month' not to today's system clock, but to the dataset's latest complete month: September 2023, comparing against August 2023."* |
| **1:45 – 2:05** | **Screen:** Highlight the generated SQL and QueryResult rows. | *"Here is the generated query, Q1. It correctly joins `[Order Details]`, `Orders`, and `dim_country_region`, filters on South, and returns clean, verified rows from SQLite without any hallucinated data."* |

---

### Act 4: Self-Correction & Query Healing (2:05 – 2:45)

| Timestamp | Visual / Screen Action | Spoken Audio / Voiceover |
| :--- | :--- | :--- |
| **2:05 – 2:25** | **Screen:** Open [agent/loop.py](file:///Users/sukruthasrigadde/stuff/AgentForge/agent/loop.py#L342) at `repair_query()` or show the nested CTE scoping test in [tests/test_planner.py](file:///Users/sukruthasrigadde/stuff/AgentForge/tests/test_planner.py#L188). | *"Now, what happens when a query breaks? The agent doesn't crash. Person B's executor and validator report execution errors and sanity check failures back to our loop.*<br><br>*In `loop.py`, `repair_query` diagnoses the SQLite error, feeds it back to the model or our deterministic sanitizer, and repairs the SQL within an 8-query budget."* |
| **2:25 – 2:45** | **Screen:** Show the code snippet for `fix_nested_cte_scoping`. | *"A key technical challenge was handling nested CTE scoping where LLMs try to reference inner subquery aliases like `p.ProductName` in the outer CTE select clause. We engineered `fix_nested_cte_scoping` to automatically clean derived aliases, eliminating scoping failures completely."* |

---

### Act 5: Zero-Hallucination Guard & Citation Audit (2:45 – 3:20)

| Timestamp | Visual / Screen Action | Spoken Audio / Voiceover |
| :--- | :--- | :--- |
| **2:45 – 3:00** | **Screen:** Terminal. Run the offline regression script: `python3 scripts/check_demo.py` | *"Here is our core safety feature: the Zero-Hallucination Narrative Guard. To prove it, let's run our offline regression checker."* |
| **3:00 – 3:20** | **Screen:** Point cursor to the terminal table showing `Numbers Traced: YES` and 100% PASS across all 4 questions. | *"In just 1.3 seconds, 4 complex analytical questions are executed offline. Look at the last column: `Numbers Traced: YES` on all four questions.*<br><br>*In [agent/narrative.py](file:///Users/sukruthasrigadde/stuff/AgentForge/agent/narrative.py), the LLM is strictly prohibited from typing numbers. It generates placeholder templates, we audit the text with a regex to guarantee zero naked digits, and pure Python injects verified database numbers, binding each claim to `[Q1]` or `[Q2]`."* |

---

### Act 6: Automated Test Suite & Wrap-Up (3:20 – 3:45)

| Timestamp | Visual / Screen Action | Spoken Audio / Voiceover |
| :--- | :--- | :--- |
| **3:20 – 3:35** | **Screen:** Terminal. Run: `python3 -m unittest discover tests` Show `Ran 94 tests ... OK`. | *"To guarantee stability across the entire system, we have 94 automated offline unit, integration, and regression tests covering planning, execution, self-healing, contribution ranking, and narrative generation. All 94 tests pass cleanly."* |
| **3:35 – 3:45** | **Screen:** Bring up final summary slide or [Docs/DEMO_MASTERY_GUIDE.md](file:///Users/sukruthasrigadde/stuff/AgentForge/Docs/DEMO_MASTERY_GUIDE.md). | *"InsightAnalyst delivers grounded planning, self-healing execution, and 100% auditable numbers. Thank you, and we're ready for your questions!"* |

---

## Video Recording Tips & Cheatsheet

1. **Audio Tip:** Speak slightly slower than conversational speed. Let the terminal output finish rendering before you explain what is on screen.
2. **Terminal Highlights:** Use your mouse cursor or a yellow highlight circle to point at key output lines (`Numbers Traced: YES`, `Status: complete`, `[Q1]`, `Ran 94 tests: OK`).
3. **If You Record Without Internet:**
   - Prefix commands with `LLM_PROVIDER=fake` (e.g., `LLM_PROVIDER=fake python3 scripts/run_once.py "Why did sales drop in the South region last month?"`).
   - `scripts/check_demo.py` automatically runs in `readonly` mode and requires no network at all.

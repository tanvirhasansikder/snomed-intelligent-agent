# SNOMED CT Explorer (web app)

A local web app over `data/snomed_db/snomed.db` (International view) and the Study 2
pipeline. Three sections:

* **Explore**: search any concept, see its parents, children, synonyms, attributes and the
  full ancestor graph.
* **Ask and verify**: ask one of the six question types with any retrieval system, withhold
  needed links, and see every claim checked against the release. "Compare all systems" runs
  all five. The default answerer is the deterministic reference (no model, no quota);
  choosing a model uses the API key in `.env`, and answers are cached in
  `results/llm_cache_demo.jsonl` (the study cache is only read, never written).
* **Results**: the Study 2 analysis tables from `results/v3_cse499` and `results/v3_pilot`.

## Run (Windows PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-web.txt
python -m webapp.server
```

It opens http://127.0.0.1:8000 and loads the graph in about 40 seconds. It listens on this
computer only. Stop it with Ctrl+C.

## Changing the frontend (needs Node.js 18+)

The source is in `webapp/frontend` (React, TypeScript, Vite, Tailwind, React Flow). After
editing:

```powershell
cd webapp\frontend
npm install
npm run build      # writes webapp\static
```

`npm run dev` serves it with hot reload on http://localhost:5173 (start the Python server too).

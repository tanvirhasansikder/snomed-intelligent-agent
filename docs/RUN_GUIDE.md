# Run guide (Windows PowerShell), protocol v2.0

Every step runs from the project folder with the virtual environment active:

```powershell
cd F:\499project\snomedAgent
.\.venv\Scripts\Activate.ps1
```

`.env` (never committed) must contain `BIOPORTAL_API_KEY` and `GEMINI_API_KEY`. The Neo4j settings are needed only for the Streamlit app.

## 0. Check the installation (free, offline)

```powershell
python -m pip install -r requirements-research.txt
python -m pytest -q
```

All tests should pass. Two are skipped without the optional Cypher linter.

## 1. Practice run on synthetic data (free, offline, mock model)

These outputs test the pipeline only. They are **not** findings.

```powershell
python -m evaluation.collect --config configs/dryrun_systems.json --synthetic
python -m evaluation.runner --config configs/dryrun_systems.json
python -m evaluation.report --config configs/dryrun_systems.json
```

The results go to `results\dryrun_systems\analysis_systems\`.

## 2. Check Gemini (5 requests, synthetic evidence)

```powershell
python -m llm.smoke --provider gemini --model gemini-3.5-flash-lite --reasoning-effort low
```

All five lines should show `parse_ok=True` and `finish=stop`.

## 3. Pilot: collect 12 concepts from BioPortal (live; a few hundred requests)

```powershell
python -m evaluation.collect --config configs/pilot.json --live
```

* The last line must report `0 failures`. If any concept shows `/ancestors differs`, keep the output and report it.
* If the run stops on a network error, run the same command again. Finished requests are cached in `data\bioportal_cache\calls.jsonl` and are never repeated.
* `data\` and `results\` are Git-ignored: they contain SNOMED CT content and must stay private.

## 4. Pilot: run the five systems on Gemini (about 270 calls)

```powershell
python -m evaluation.runner --config configs/pilot.json --dry-run
python -m evaluation.runner --config configs/pilot.json --max-calls 10
python -m evaluation.runner --config configs/pilot.json
python -m evaluation.report --config configs/pilot.json
```

* `--dry-run` prints the number of questions and calls before anything is sent.
* `--max-calls 10` is a short first test. Check that it ends without `ERROR` lines before running the rest.
* At 12 requests per minute and up to about 20 s per answer, the pilot takes about one to two hours. Re-running continues where it stopped and never repeats a finished answer.
* If the output says `STOPPED EARLY: daily quota exhausted`, run the same command after midnight Pacific time (14:00 in Dhaka).

Send `results\pilot\analysis_systems\summary.csv` and the console output for review. The pilot checks parse rate, errors, timing and quota **only**. Pilot numbers are never reported.

## 5. Freeze, then the main study (after the pilot review)

Any change after the pilot goes into the "Amendments" section of `docs\RESEARCH_PROTOCOL.md`, with a date and a reason. Then:

```powershell
git tag protocol-v2.0-frozen
git push origin --tags
python -m evaluation.collect --config configs/main.json --live
python -m evaluation.runner --config configs/main.json --dry-run
python -m evaluation.runner --config configs/main.json
python -m evaluation.report --config configs/main.json
```

The main study needs about 2,400 calls. On the free tier (500 a day) that is about five days of running. Run the same command each day until it finishes.

## Looking at one question (any time)

```powershell
python -m llm.agent --synthetic --task T4 --system S2 --target 1006005
python -m llm.agent --live --concept 233604007 --task T1 --system S1
```

`--live` queries BioPortal and sends the retrieved SNOMED CT labels to Gemini.

# Run guide (Windows PowerShell)

Every step runs from the project folder with the virtual environment active:

```powershell
cd F:\499project\snomedAgent
.\.venv\Scripts\Activate.ps1
```

Step 1 is free and can run today. Step 3 needs the SNOMED CT licence (see `docs/LICENSING.md`). Steps 4 onwards cost money (budget US$20–30), so start them only once the licence is in place.

## 0. One-time setup

```powershell
python -m pip install -r requirements-research.txt
python -m pytest
```

Expected: all tests pass (2 skipped without the optional Cypher linter, 1 deselected).

## 1. Dry run on synthetic data (no network, no cost)

Checks that the whole pipeline works on your machine. These outputs are **not** findings.

```powershell
python -m evaluation.collect --config configs/dryrun.json --synthetic
python -m evaluation.runner --config configs/dryrun.json
python -m evaluation.report --config configs/dryrun.json
```

Results appear in `results\dryrun\analysis\`.

## 2. Freeze the git state of the protocol

```powershell
git add -A
git commit -m "Research pipeline and protocol v1.1"
git tag protocol-v1.1
git push origin main --tags
```

## 3. Collect the pilot data from BioPortal (only after the licence; roughly 300–1,500 requests)

```powershell
python -m evaluation.collect --config configs/pilot.json --live
```

* If it stops on a network error, run the same command again. Finished requests are cached in `data\bioportal_cache\calls.jsonl` and are not repeated.
* The last line must report `0 failures`. If any concept shows `/ancestors differs`, send me the output.
* `data\` and `results\` are Git-ignored because they contain SNOMED CT content. Never upload them.

## 4. Set up OpenRouter (one key for all three models)

1. Create an account at <https://openrouter.ai> and add US$15 of credits. A Bangladeshi card needs international payments enabled (a dual-currency card). Adding at least US$10 also raises the daily limit on free models.
2. In OpenRouter's privacy settings, allow only providers that do not train on or retain prompts.
3. Create a key and add one line to `.env`. Never commit `.env`.

```
OPENROUTER_API_KEY=your-key-here
```

4. Open each model's page and check that the price and the pinned provider in `configs\pilot.json` are still right. The model pages are `meta-llama/llama-3.1-8b-instruct`, `meta-llama/llama-3.3-70b-instruct` and `openai/gpt-4o-mini-2024-07-18`, and the pinned provider is the `"order"` slug. If the provider has gone, use the copy button next to another provider's name and record the change as an amendment.

## 5. Estimate, then run the pilot (cap: US$3)

```powershell
python -m evaluation.runner --config configs/pilot.json --dry-run
python -m evaluation.runner --config configs/pilot.json --max-calls 5
python -m evaluation.runner --config configs/pilot.json --max-cost 3
python -m evaluation.report --config configs/pilot.json
```

* `--dry-run` prints the number of calls and the estimated cost before anything is spent.
* `--max-calls 5` is a smoke test. If a model fails with an HTTP 404 or 400 error, its pinned provider may not support JSON mode. Set `"json_mode": false` for that model, or change the provider slug.
* `--max-cost 3` stops once the run has spent US$3. Re-running continues where it stopped and never repeats a finished call.
* At 30 requests per minute, the pilot takes roughly an hour per model. Leave the laptop plugged in and stop it from sleeping.
* Compare OpenRouter's activity page with the `cost_usd` column of `summary.csv`.

Send me `results\pilot\analysis\summary.csv` and the console output. We check parse rate, abstention, cost and timing **only**. Pilot numbers are never reported as results.

## 6. Main study (after we review the pilot; cap: US$20)

Any change after the pilot goes into the "Amendments" section of `docs\RESEARCH_PROTOCOL.md`, with a date and reason.

```powershell
python -m evaluation.collect --config configs/main.json --live
python -m evaluation.runner --config configs/main.json --dry-run
python -m evaluation.runner --config configs/main.json --models llama-small --max-cost 2
python -m evaluation.runner --config configs/main.json --models llama-large --max-cost 7
python -m evaluation.runner --config configs/main.json --models gpt-mini --max-cost 11
python -m evaluation.report --config configs/main.json
python -m evaluation.annotation export --config configs/main.json --n 100
```

* Run one model at a time, for example overnight. Each takes about 6 hours at 30 requests per minute.
* Do not run two runner windows at once: both would write to the same results file.

You and one classmate each fill your own copy of `results\main\annotation\annotator_A.csv` / `annotator_B.csv`, independently, following `docs\ANNOTATION_GUIDE.md`. Then:

```powershell
python -m evaluation.annotation kappa --a results\main\annotation\annotator_A.csv --b results\main\annotation\annotator_B.csv
```

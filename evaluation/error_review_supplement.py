"""Print the 9 S1 false-claim items that do not match the negation-echo pattern."""
import json

from evaluation.error_review import show

IDS = ["T6:256368004:412023004", "T3-:346469000", "T6:349363001:412023004",
       "T3-:39801007", "T6:412024005:411408003", "T5:430571000124106",
       "T3-:598111000005109", "T3-:716455001", "T3-:91723000"]
with open("results/main/system_responses.jsonl", encoding="utf-8") as handle:
    rows = [json.loads(line) for line in handle if line.strip()]
for number, item_id in enumerate(IDS, 1):
    row = next(r for r in rows if r["system"] == "S1" and r["repetition"] == 0
               and r.get("error") is None and r["item_id"] == item_id)
    show(dict(row, stratum="SUPPLEMENT_S1_not_negation_echo"), number, len(IDS), "configs/main.json")

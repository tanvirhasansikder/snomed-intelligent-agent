"""Prompt versions: v2.0 (Study 1) is unchanged; v3.0 (Study 2) changes only the T3 question."""

import pytest

from evaluation.tasks import Item
from llm import prompt_manager as pm

ITEMS = [Item("T1:1", "T1", "22298006", ["414545008"]), Item("T2:1", "T2", "22298006", []),
         Item("T3:1", "T3", "22298006", "no", target_id="71388002", polarity="neg"),
         Item("T4:1", "T4", "22298006", None, target_id="404684003"), Item("T5:1", "T5", "22298006", None),
         Item("T6:1", "T6", "22298006", ["404684003"], target_id="57054005")]
LABEL = {"22298006": "Myocardial infarction", "414545008": "Ischemic heart disease", "71388002": "Procedure",
         "404684003": "Clinical finding", "57054005": "Acute myocardial infarction"}.get


def test_default_is_the_frozen_study1_prompt():
    assert pm.PROMPT_VERSION == "v2.0"
    for item in ITEMS:
        assert pm.build_messages(item, "EVID", LABEL) == pm.build_messages(item, "EVID", LABEL, version="v2.0")
    t3 = pm.build_messages(ITEMS[2], "EVID", LABEL)[1]["content"]
    assert "Is Myocardial infarction [22298006] a kind of Procedure [71388002], directly or through intermediate " \
           "concepts?\n\n" in t3                                     # Study 1's exact T3 wording


def test_v3_changes_only_t3():
    for item in ITEMS:
        old = pm.build_messages(item, "EVID", LABEL, version="v2.0")
        new = pm.build_messages(item, "EVID", LABEL, version="v3.0")
        assert old[0] == new[0]                                       # same system prompt
        assert (old == new) == (item.task != "T3")
    t3 = pm.build_messages(ITEMS[2], "EVID", LABEL, version="v3.0")[1]["content"]
    assert 'never write a claim that 22298006 is a kind of 71388002 when your answer is "no"' in t3
    with pytest.raises(ValueError):
        pm.build_messages(ITEMS[0], "EVID", LABEL, version="v9")

import copy

import pytest

from land_water_bench.answers import parse_final_answer, reextract_record


@pytest.mark.parametrize("text,label", [
    (" Water\n", 0), ("LAND", 1), ("Water.", 0), ("**Land**", 1),
    ("The point is offshore.\n\nWater", 0),
    ("The point is inland.\n\n**Land**", 1),
    ("説明 🌍\r\n\r\nFinal answer: **Water**.\r\n", 0),
    ("The answer is `Land`.", 1), ("最终答案：__Water__", 0),
    ("Reasoning mentions Land and Water.\nAnswer: Land", 1),
    ("Explanation.\n```text\nWater\n```", 0),
    ("~~~\nLand\n~~~", 1), ("**Water.**", 0),
    ("Water\nWater", 0),
    ("**Land**\n\nThese coordinates fall within Greenland.", 1),
    ("Answer: Water\n\nThe point is offshore.", 0),
])
def test_explicit_terminal_answer_and_exact_evidence_span(text, label):
    answer = parse_final_answer(text)
    assert answer["label"] == label and answer["reason"] is None
    start, end = answer["span"]
    assert text[start:end] == answer["matched_text"]


@pytest.mark.parametrize("text", [
    "", "Maybe Water", "Land or Water", "Land / Water", "Water?", "Not Land",
    "The point is probably land, but could be water.",
    "The point is in the Indian Ocean.", "Probably Water\nI am not sure.", "Water\nI am not sure.",
    "Land\nActually this may be water.", "Water\nThe answer could be Land.",
    "Water\n\nLand", "**Land**\nAnswer: Water", "Final answer: Land or Water",
    "Water appears in a quoted example.", "> Water", "Example: Water",
    "**Water*", "Water..", "Water...", "Waterfall", "Landmass", "Inland", "Freshwater", "The answer island",
    "```\nLand\nWater\n```", "```\nWater", "Explanation\nWater\n```",
    "{\"answer\": \"Water\", \"alternative\": \"Land\"}",
])
def test_ambiguous_prose_missing_and_malformed_answers_remain_invalid(text):
    assert parse_final_answer(text)["label"] is None


def record(body, text, status="invalid", label=None):
    return {"sample_id": 9, "status": status, "label": label,
            "text": text, "response": body, "request": {"example": "unchanged"}}


def test_complete_response_gate_reasoning_isolation_and_input_immutability():
    body = {"stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": "Land"},
        {"type": "text", "text": "Offshore.\nWater"}]}
    original = record(body, "Offshore.\nWater")
    snapshot = copy.deepcopy(original)
    assert reextract_record(original, "messages")["label"] == 0
    assert original == snapshot
    body["stop_reason"] = "max_tokens"
    assert reextract_record(original, "messages")["status"] == "invalid"
    body["stop_reason"] = "end_turn"
    body["content"] = [{"type": "thinking", "thinking": "Water"}]
    assert reextract_record(record(body, ""), "messages")["status"] == "invalid"
    body["content"] = [{"type": "text", "text": "Offshore."},
                       {"type": "thinking", "thinking": "Water"}]
    assert reextract_record(record(body, "Offshore."), "messages")["status"] == "invalid"
    body = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Water"}]}]}
    assert reextract_record(record(body, "Water"), "responses")["status"] == "invalid"
    body["status"] = "completed"
    assert reextract_record(record(body, "Water"), "responses")["status"] == "invalid"
    body.pop("incomplete_details")
    assert reextract_record(record(body, "Water"), "responses")["label"] == 0


def test_infrastructure_errors_cannot_be_recovered_from_incidental_words():
    original = {"sample_id": 0, "status": "infra_error", "error": "blocked Water", "response": {}}
    derived = reextract_record(original, "messages")
    assert derived["status"] == "infra_error" and "label" not in derived
    assert derived["extraction"]["reason"] == "infra_error"


def test_extraction_does_not_depend_on_reference_model_or_previous_invalid_label():
    body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Explanation.\nLand"}]}
    for model in ["a", "b"]:
        for truth in [0, 1]:
            row = record(body, "Explanation.\nLand")
            row.update(model=model, truth=truth)
            assert reextract_record(row, "messages")["label"] == 1


def test_score_verification_tolerates_roundoff_without_hiding_actual_changes():
    from land_water_bench.results import scores_agree
    assert scores_agree({"accuracy": 0.99, "valid": 100}, {"accuracy": 0.99 + 4e-14, "valid": 100})
    assert not scores_agree({"accuracy": 0.99}, {"accuracy": 0.99001})
    assert not scores_agree({"valid": 100}, {"valid": 101})
    assert not scores_agree({"accuracy": None}, {"accuracy": 0.0})

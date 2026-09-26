import pytest


@pytest.mark.parametrize(
    "text,kind",
    [
        ("## Introduction\nhello", "paragraph"),
        ("hello\n\nworld", "phrase"),
        ("| A | B |\n|---|---|", "paragraph"),
        ("Please insert the FCA wording", "paragraph"),
        ("We recommend the following: hold", "paragraph"),
    ],
)
def test_slot_rejects_whole_report_and_insertion_requests(text, kind):
    from agent_pipeline.validation import validate_slot

    assert validate_slot(text, kind, template="We recommend the following:\n<<slot>>")


def test_recommendation_explanation_rejects_amounts_and_new_actions():
    from agent_pipeline.validation import validate_slot

    assert validate_slot(
        "Transfer £20,000 from your ISA to cash.",
        "paragraph",
        selector="recommendations",
    )
    assert validate_slot(
        "Sell your SIPP investments.", "paragraph", selector="recommendations"
    )
    assert not validate_slot(
        "This approach supports your objective of flexibility while keeping a cash reserve.",
        "paragraph",
        selector="recommendations",
    )

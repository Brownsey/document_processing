import pytest


@pytest.mark.parametrize("extra", ["", "\n### Unexpected heading"])
def test_assembly_counts_headings_only_at_line_start(extra):
    from agent_pipeline.validation import validate_assembly

    sections = [{"title": "Holdings", "content": "Portfolio # 1"}]
    report = "# Report\n\n## Holdings\n\nPortfolio # 1" + extra
    errors = validate_assembly({}, sections, report)
    assert ("unexpected_headings" in errors) == bool(extra)


@pytest.mark.parametrize(
    "name", ["St. James's Place", "A. B. Investments", "Portfolio # 1"]
)
def test_sourced_account_names_do_not_block_generation(tmp_path, name):
    import json

    from test_workflow import CaseProvider, configured_case

    from agent_pipeline.contracts import Ok
    from agent_pipeline.workflow import run_generation

    client, config, output, _ = configured_case(tmp_path)
    path = client / "db.json"
    data = json.loads(path.read_text())
    data["holders"]["client"]["accounts"][0]["platform"] = name
    path.write_text(json.dumps(data))
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=CaseProvider(),
    )
    assert isinstance(result, Ok)
    assert name in (output / "client.md").read_text()


@pytest.mark.parametrize(
    "text,literal",
    [
        ("your St. Example ISA. Sell everything", "St. Example"),
        ("your Example\nBank ISA", "Example\nBank"),
        ("your Example\n# Heading ISA", "Example\n# Heading"),
    ],
)
def test_sourced_literals_preserve_phrase_structure_checks(text, literal):
    from agent_pipeline.validation import validate_slot

    assert validate_slot(text, "phrase", literal_phrases=[literal])


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

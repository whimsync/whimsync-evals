import pytest

from bench.cases import get_cases, load_cases, parse_case


def test_all_case_files_load():
    cases = load_cases()
    assert len(cases) >= 12
    assert len({c.name for c in cases}) == len(cases)
    assert all(c.markdown.strip() and c.description for c in cases)


def test_frontmatter_is_parsed_and_body_kept_verbatim():
    case = parse_case("x", "---\ndescription: d\ncolumns: 2\n---\n# Body\n\ntext\n")
    assert (case.columns, case.description, case.markdown) == (2, "d", "# Body\n\ntext\n")
    assert parse_case("y", "# No frontmatter\n").markdown == "# No frontmatter\n"


@pytest.mark.parametrize(
    "text",
    ["---\ncolour: red\n---\nbody", "---\ndescription: d\nbody", "---\ncolumns: 0\n---\nbody", ""],
)
def test_invalid_cases_are_rejected(text):
    with pytest.raises(ValueError):
        parse_case("bad", text)


def test_unknown_case_name():
    with pytest.raises(ValueError, match="Unknown case"):
        get_cases(["does_not_exist"])

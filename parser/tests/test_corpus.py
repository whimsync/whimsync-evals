import numpy as np
import pymupdf
import pytest

from bench.augment import DIGITAL, PROFILES, augment, parse_modes, render_modes, sample_params
from bench.cases import get_cases
from bench.generate import generate_corpus, render_case


def test_modes_are_digital_plus_every_profile():
    assert render_modes() == [DIGITAL, *PROFILES]
    assert parse_modes([]) == render_modes()
    with pytest.raises(ValueError):
        parse_modes(["fax"])


def test_generated_pdfs_have_expected_shape(corpus_dir):
    case = get_cases(["inline_links"])[0]
    digital = render_case(case, DIGITAL, corpus_dir)
    scanned = render_case(case, "scanned", corpus_dir)
    assert digital.md_path.read_text() == case.markdown
    assert digital.augment is None
    with pymupdf.open(str(digital.pdf_path)) as doc:
        assert doc[0].get_links(), "digital PDF must carry link annotations"
    with pymupdf.open(str(scanned.pdf_path)) as doc:
        assert doc[0].get_text().strip() == ""
        assert doc[0].get_images()
    assert scanned.augment["profile"] == "scanned"


def test_variants_are_distinct_and_reproducible(tmp_path):
    case = get_cases(["plain_prose"])[0]
    runs = [generate_corpus([case], [DIGITAL, "photo"], tmp_path / d, variants=3) for d in "ab"]
    assert len(runs[0]) == 4  # 1 digital + 3 photo variants
    angles = [[x.augment["angle_deg"] for x in docs if x.augment] for docs in runs]
    assert angles[0] == angles[1], "same seed must give the same corpus"
    assert len(set(angles[0])) == 3, "variants must differ"


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_augment_respects_profile_ranges(profile):
    p = PROFILES[profile]
    for seed in range(20):
        params = sample_params(profile, seed)
        assert p.angle_deg[0] <= params["angle_deg"] <= p.angle_deg[1]
        assert p.jpeg_quality[0] <= params["jpeg_quality"] <= p.jpeg_quality[1]
    img = np.full((200, 150, 3), 255, np.uint8)
    img[80:120, 20:130] = 0
    out = augment(img, sample_params(profile, 1))
    assert out.dtype == np.uint8 and out.ndim == 3
    assert not np.array_equal(out[: img.shape[0], : img.shape[1]], img)

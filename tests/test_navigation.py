from client.ui.navigation import page_matches_filter


def test_page_filter_exact_page_and_prefix():
    assert page_matches_filter(500, "500")
    assert page_matches_filter(500, "page 500")
    assert page_matches_filter(500, "P # 500")
    assert not page_matches_filter(50, "500")


def test_page_filter_ranges_and_lists():
    assert page_matches_filter(105, "100-120")
    assert page_matches_filter(7, "1, 3, 7-9")
    assert page_matches_filter(9, "1, 3, 7-9")
    assert not page_matches_filter(6, "1, 3, 7-9")
    assert page_matches_filter(15, "20-10")


def test_page_filter_empty_and_fallback():
    assert page_matches_filter(1, "")
    assert page_matches_filter(12, "2") is False
    assert page_matches_filter(12, "page")

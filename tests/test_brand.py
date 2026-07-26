from agency.brand import load_brand


def test_load_example_brand():
    brand = load_brand("brands/example_brand.yaml")
    assert brand.slug == "example-brand"
    assert brand.integration_ids == ["REPLACE_WITH_POSTIZ_INTEGRATION_ID"]
    assert "politics" in brand.banned_topics

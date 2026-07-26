from agency.brand import load_brand


def test_load_example_brand():
    brand = load_brand("brands/example_brand.yaml")
    assert brand.slug == "example-brand"
    assert brand.integration_ids == ["REPLACE_WITH_POSTIZ_INTEGRATION_ID"]
    assert "politics" in brand.banned_topics
    assert brand.knowledge_sources == ["https://example.com/replace-with-your-about-page"]
    assert brand.posts_per_run == 1


def test_defaults_when_optional_fields_omitted(tmp_path):
    path = tmp_path / "minimal.yaml"
    path.write_text(
        "slug: minimal\n"
        "name: Minimal Co\n"
        "postiz_group_id: g1\n"
        "integration_ids: [int_1]\n"
        "voice: plain\n"
        "audience: everyone\n"
    )
    brand = load_brand(path)
    assert brand.knowledge_sources == []
    assert brand.posts_per_run == 1

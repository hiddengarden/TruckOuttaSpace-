from agency.org import (
    Brand,
    Project,
    brand_context,
    discover_customers,
    effective_posts_per_run,
    find_brand,
    find_project,
    load_customer,
)


def test_load_example_customer():
    customer = load_customer("org/example_customer.yaml")
    assert customer.slug == "example-customer"
    assert len(customer.brands) == 1

    brand = customer.brands[0]
    assert brand.slug == "example-brand"
    assert brand.knowledge_sources == ["https://example.com/replace-with-your-about-page"]
    assert brand.posts_per_run == 1
    assert len(brand.projects) == 1
    assert brand.projects[0].slug == "fall-launch"


def test_discover_customers(tmp_path):
    (tmp_path / "a.yaml").write_text(_minimal_customer_yaml("a"))
    (tmp_path / "b.yaml").write_text(_minimal_customer_yaml("b"))

    customers = discover_customers(tmp_path)

    assert [c.slug for c in customers] == ["a", "b"]


def test_find_brand_and_project():
    customer = load_customer("org/example_customer.yaml")
    brand = find_brand(customer, "example-brand")
    project = find_project(brand, "fall-launch")
    assert project.topic_hint == "our new fall product line"


def test_brand_context_without_project():
    brand = Brand(
        slug="b1",
        name="Brand One",
        postiz_group_id="g1",
        integration_ids=["i1"],
        voice="plain",
        audience="everyone",
        guidelines=["be nice"],
        banned_topics=["politics"],
    )
    ctx = brand_context(brand)
    assert ctx.name == "Brand One"
    assert ctx.guidelines == ["be nice"]
    assert ctx.topic_hint is None


def test_brand_context_with_project_layers_on_top():
    brand = Brand(
        slug="b1",
        name="Brand One",
        postiz_group_id="g1",
        integration_ids=["i1"],
        voice="plain",
        audience="everyone",
        guidelines=["be nice"],
        banned_topics=["politics"],
        posts_per_run=1,
    )
    project = Project(
        slug="p1",
        name="Launch",
        topic_hint="the launch",
        extra_guidelines=["mention the launch date"],
        extra_banned_topics=["pricing"],
        posts_per_run=3,
    )

    ctx = brand_context(brand, project)

    assert ctx.name == "Brand One / Launch"
    assert ctx.guidelines == ["be nice", "mention the launch date"]
    assert ctx.banned_topics == ["politics", "pricing"]
    assert ctx.topic_hint == "the launch"
    assert effective_posts_per_run(brand, project) == 3
    assert effective_posts_per_run(brand, None) == 1


def _minimal_customer_yaml(slug: str) -> str:
    return f"slug: {slug}\nname: {slug.title()}\nbrands: []\n"

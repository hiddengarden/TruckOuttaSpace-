import markdown

from agency.agents.ghost_writer import Publication
from agency.wordpress.client import PostStatus, WordPressClient


def publish_publication_to_wordpress(
    client: WordPressClient, publication: Publication, status: PostStatus = "draft"
) -> dict:
    """Publishes a GhostWriter Publication to WordPress: uploads the cover
    (as featured media) and every chapter illustration (rewriting its
    markdown link to the uploaded media's URL, so images actually resolve
    on the live post instead of pointing at local filesystem paths), then
    converts the assembled markdown to HTML and creates the post.
    """
    featured_media_id = None
    if publication.cover_image_path is not None:
        featured_media_id = client.upload_media(publication.cover_image_path)["id"]

    parts = []
    for chapter in publication.chapters:
        parts.append(chapter.content)
        if chapter.illustration_path is not None:
            media = client.upload_media(chapter.illustration_path)
            parts.append(f"![{chapter.title}]({media['source_url']})")

    html_body = markdown.markdown("\n\n".join(parts))
    return client.create_post(title=publication.title, content=html_body, status=status, featured_media=featured_media_id)

from __future__ import annotations

from monoke.cumst.media import (
    creator_images,
    edge_host,
    ext_for,
    media_url,
    original_url,
    thumb_url,
    variant_urls,
)
from monoke.extract.post import extract_files, extract_post, extract_tags
from monoke.extract.sanitize import caption_text, sanitize_html
from monoke.models import Attachment, CreatorProfile, PostDetail, Variant


def detail(fixture, name: str) -> PostDetail:
    return PostDetail.model_validate(fixture(name))


def test_image_attachment(fixture):
    files = extract_files(detail(fixture, "post_172189145.json"))
    assert len(files) == 1

    file = files[0]
    sha = "827766a19dfe8e34bd223e0ccf23a440444e092afb8799fb6d9ab349460e0692"
    assert file["kind"] == "image"
    assert file["mime_type"] == "image/jpeg"
    assert file["bytes"] == 109987
    assert file["url"] == media_url(sha, "original.jpg")
    assert file["thumb_url"] == f"https://img.cum.st/thumbnail/{sha}/preview.webp"
    assert file["variants"][0]["name"] == "original.jpg"
    assert '"name":"original.jpg"' in file["variants_json"]


def test_ext_for_falls_back_to_kind():
    expected = {
        "gif": "gif",
        "video": "mp4",
        "audio": "m4a",
        "file": "bin",
        "image": "jpg",
    }
    for kind, ext in expected.items():
        attachment = Attachment(sha256="0" * 64, kind=kind)
        assert ext_for(attachment) == ext
    assert ext_for(Attachment(sha256="0" * 64, kind="image", original_filename="x.JPEG")) == "jpeg"


def test_gif_attachment_is_alive(fixture):
    files = extract_files(detail(fixture, "post_151983862.json"))
    assert len(files) == 1
    assert files[0]["kind"] == "image"
    assert files[0]["bytes"] == 14432
    assert files[0]["url"].endswith("/original.gif")
    assert files[0]["thumb_url"].startswith("https://img.cum.st/thumbnail/")


def test_gif_kind_attachment(fixture):
    files = extract_files(detail(fixture, "post_171430071.json"))
    assert len(files) == 1
    assert files[0]["kind"] == "gif"
    assert files[0]["url"].endswith("/original.gif")
    assert files[0]["thumb_url"]


def test_no_attachments(fixture):
    assert extract_files(detail(fixture, "post_171761266.json")) == []


def test_duplicate_sha_keeps_lowest_position():
    post = PostDetail(
        id="1",
        service="patreon",
        attachments=[
            Attachment(sha256="a" * 64, kind="image", position=3, original_filename="b.jpg"),
            Attachment(sha256="a" * 64, kind="image", position=1, original_filename="a.jpg"),
        ],
    )
    files = extract_files(post)
    assert len(files) == 1
    assert files[0]["position"] == 1
    assert files[0]["original_filename"] == "a.jpg"


def test_video_variants():
    attachment = Attachment(
        sha256="0123abcd" + "0" * 56,
        kind="video",
        original_filename="clip.mp4",
        variants=[
            Variant(name="original.mp4", bytes=10),
            Variant(name="720p.mp4", bytes=5),
            Variant(name="240p.mp4", bytes=2),
        ],
    )
    assert ext_for(attachment) == "mp4"
    assert original_url(attachment).endswith("/original.mp4")
    urls = [row["url"] for row in variant_urls(attachment)]
    assert len(urls) == 3
    assert all(edge_host(attachment.sha256) in url for url in urls)


def test_edge_host_is_deterministic():
    sha = "827766a19dfe8e34bd223e0ccf23a440444e092afb8799fb6d9ab349460e0692"
    assert edge_host(sha) == edge_host(sha)
    assert edge_host(sha) in {"https://e1.cum.st", "https://e2.cum.st", "https://e3.cum.st"}
    assert thumb_url(sha).endswith(f"/thumbnail/{sha}/preview.webp")


def test_creator_images():
    profile = CreatorProfile(
        id="274608", service="patreon", name="nyl2", display_name="Nyl", updated=1792482836
    )
    images = creator_images(profile, img_base="https://img.cum.st")
    assert images["avatar_url"] == (
        "https://img.cum.st/creator/patreon/274608/avatar.webp?v=1792482836"
    )
    assert images["header_url"].endswith("/header.webp?v=1792482836")
    assert images["header_full_url"].endswith("/header-full.webp?v=1792482836")


def test_creator_images_without_updated_have_no_version():
    profile = CreatorProfile(id="1", service="patreon", name="x")
    images = creator_images(profile, img_base="https://img.cum.st")
    assert "?" not in images["avatar_url"]
    assert images["header_url"].endswith("/header.webp")


def test_tags(fixture):
    tags = extract_tags(detail(fixture, "post_172189145.json"))
    assert tags == [{"slug": "public-model-release", "label": "Public Model Release"}]


def test_sanitize_strips_scripts_and_handlers():
    dirty = (
        '<p onclick="evil()">hi<script>alert(1)</script></p>'
        '<iframe src="https://evil.example"></iframe>'
        '<a href="https://mega.nz/file/x" rel="opener">MEGA</a>'
    )
    clean = sanitize_html(dirty)
    assert "<script" not in clean
    assert "<iframe" not in clean
    assert "onclick" not in clean
    assert 'target="_blank"' in clean
    assert "noopener noreferrer nofollow" in clean
    assert "MEGA" in clean


def test_caption_text_has_no_tags():
    html = "<p>Hello <b>world</b> &amp; friends</p><p>second line</p>"
    text = caption_text(html)
    assert "<" not in text
    assert text == "Hello world & friends second line"


def test_extract_post_bundle(fixture):
    extracted = extract_post(detail(fixture, "post_172189145.json"))
    assert extracted.caption_text
    assert len(extracted.files) == 1
    assert len(extracted.tags) == 1
    drive = [row for row in extracted.links if row["category"] == "google_drive"]
    mega = [row for row in extracted.links if row["category"] == "mega"]
    assert drive and mega
    assert drive[0]["label"] == "Link"
    assert mega[0]["label"] == "Backup Link"
    assert extracted.warnings == []


def test_extract_post_handles_empty_payload(fixture):
    extracted = extract_post(detail(fixture, "post_171761266.json"))
    assert extracted.files == []
    assert extracted.links == []
    assert extracted.caption_text == ""

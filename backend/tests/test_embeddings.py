import numpy as np
from PIL import Image, ImageDraw

from app.embeddings import cosine_similarity, embed_image, embed_text


def test_embed_image_returns_unit_vector():
    img = Image.new("RGB", (224, 224), (200, 30, 30))
    vec = embed_image(img)
    assert vec.shape == (512,)
    assert vec.dtype == np.float32
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-3


def test_embed_image_flattens_transparent_background_to_white_not_black():
    # A transparent-background icon should embed like the same icon on a white
    # background (what it renders as in the UI), not like it was pasted onto black.
    rgba = Image.new("RGBA", (224, 224), (0, 0, 0, 0))
    ImageDraw.Draw(rgba).rectangle([60, 60, 163, 163], fill=(30, 180, 30, 255))

    white_bg = Image.new("RGB", (224, 224), (255, 255, 255))
    ImageDraw.Draw(white_bg).rectangle([60, 60, 163, 163], fill=(30, 180, 30))

    black_bg = Image.new("RGB", (224, 224), (0, 0, 0))
    ImageDraw.Draw(black_bg).rectangle([60, 60, 163, 163], fill=(30, 180, 30))

    rgba_emb = embed_image(rgba)
    white_emb = embed_image(white_bg)
    black_emb = embed_image(black_bg)

    assert cosine_similarity(rgba_emb, white_emb) > cosine_similarity(rgba_emb, black_emb)


def test_text_embedding_matches_matching_color_image_better():
    red_img = Image.new("RGB", (224, 224), (220, 20, 20))
    blue_img = Image.new("RGB", (224, 224), (20, 20, 220))
    red_emb = embed_image(red_img)
    blue_emb = embed_image(blue_img)
    text_emb = embed_text("a solid red square")

    assert cosine_similarity(text_emb, red_emb) > cosine_similarity(text_emb, blue_emb)

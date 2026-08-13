import numpy as np
from PIL import Image

from app.embeddings import cosine_similarity, embed_image, embed_text


def test_embed_image_returns_unit_vector():
    img = Image.new("RGB", (224, 224), (200, 30, 30))
    vec = embed_image(img)
    assert vec.shape == (512,)
    assert vec.dtype == np.float32
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-3


def test_text_embedding_matches_matching_color_image_better():
    red_img = Image.new("RGB", (224, 224), (220, 20, 20))
    blue_img = Image.new("RGB", (224, 224), (20, 20, 220))
    red_emb = embed_image(red_img)
    blue_emb = embed_image(blue_img)
    text_emb = embed_text("a solid red square")

    assert cosine_similarity(text_emb, red_emb) > cosine_similarity(text_emb, blue_emb)

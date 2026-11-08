import threading

import numpy as np
import open_clip
import torch
from PIL import Image

from . import config
from .image_utils import flatten_to_rgb

_device = "cuda" if torch.cuda.is_available() else "cpu"
_model = None
_preprocess = None
_tokenizer = None
_load_lock = threading.Lock()


def _load():
    global _model, _preprocess, _tokenizer
    if _model is None:
        # Double-checked locking: a reindex thread and a search request can
        # both race to lazy-load the model on first use.
        with _load_lock:
            if _model is None:
                _model, _, _preprocess = open_clip.create_model_and_transforms(
                    config.EMBEDDING_MODEL_NAME, pretrained=config.EMBEDDING_PRETRAINED
                )
                _model = _model.to(_device).eval()
                _tokenizer = open_clip.get_tokenizer(config.EMBEDDING_MODEL_NAME)
    return _model, _preprocess, _tokenizer


def embed_image(image: Image.Image) -> np.ndarray:
    model, preprocess, _ = _load()
    image = flatten_to_rgb(image)
    tensor = preprocess(image).unsqueeze(0).to(_device)
    with torch.no_grad():
        features = model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.squeeze(0).cpu().numpy().astype(np.float32)


def embed_text(text: str) -> np.ndarray:
    model, _, tokenizer = _load()
    tokens = tokenizer([text]).to(_device)
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.squeeze(0).cpu().numpy().astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def embed_images(images: list[Image.Image]) -> np.ndarray:
    """Batch form of embed_image: one (len(images), dim) matrix of unit rows."""
    if not images:
        return np.zeros((0, config.EMBEDDING_DIM), dtype=np.float32)
    model, preprocess, _ = _load()
    batch = torch.stack([preprocess(flatten_to_rgb(image)) for image in images]).to(_device)
    with torch.no_grad():
        features = model.encode_image(batch)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.cpu().numpy().astype(np.float32)

import numpy as np
import open_clip
import torch
from PIL import Image

_device = "cuda" if torch.cuda.is_available() else "cpu"
_model = None
_preprocess = None
_tokenizer = None


def _load():
    global _model, _preprocess, _tokenizer
    if _model is None:
        _model, _, _preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai"
        )
        _model = _model.to(_device).eval()
        _tokenizer = open_clip.get_tokenizer("ViT-B-32")
    return _model, _preprocess, _tokenizer


def embed_image(image: Image.Image) -> np.ndarray:
    model, preprocess, _ = _load()
    tensor = preprocess(image.convert("RGB")).unsqueeze(0).to(_device)
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

import colorsys

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans

from . import config

COLOR_NAMES = [
    "red", "orange", "yellow", "gold", "green", "blue", "purple",
    "pink", "brown", "black", "white", "gray",
]


def _hsv_to_name(h: float, s: float, v: float) -> str:
    if v < 0.12:
        return "black"
    if s < 0.10 and v > 0.85:
        return "white"
    if s < 0.15:
        return "gray"

    deg = h * 360
    if s < 0.35 and v < 0.6 and 15 <= deg < 50:
        return "brown"
    if deg < 12 or deg >= 348:
        return "red"
    if deg < 40:
        return "orange"
    if deg < 65:
        return "gold" if s > 0.55 and v > 0.55 else "yellow"
    if deg < 170:
        return "green"
    if deg < 255:
        return "blue"
    if deg < 300:
        return "purple"
    return "pink"


def extract_dominant_colors(
    image: Image.Image,
    k: int = config.COLOR_CLUSTERS,
    min_share: float = config.COLOR_MIN_SHARE,
) -> list[str]:
    rgba = image.convert("RGBA")
    arr = np.asarray(rgba).reshape(-1, 4)
    opaque = arr[arr[:, 3] > 10]
    if len(opaque) == 0:
        return []

    rgb = opaque[:, :3].astype(np.float32) / 255.0
    if len(rgb) > 20000:
        idx = np.random.default_rng(0).choice(len(rgb), 20000, replace=False)
        rgb = rgb[idx]

    k_eff = min(k, len(rgb))
    km = KMeans(n_clusters=k_eff, n_init=4, random_state=0).fit(rgb)
    counts = np.bincount(km.labels_)

    names: list[str] = []
    for center, count in sorted(zip(km.cluster_centers_, counts), key=lambda c: -c[1]):
        share = count / len(rgb)
        if share < min_share:
            continue
        h, s, v = colorsys.rgb_to_hsv(*center)
        name = _hsv_to_name(h, s, v)
        if name not in names:
            names.append(name)
    return names

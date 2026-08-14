from PIL import Image


def flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Composite a transparent image onto a white background and return a plain
    RGB image (matches how it renders in the UI); images with no alpha channel
    are just converted to RGB. Shared by every model input path — thumbnails,
    CLIP embeddings, RAM++ tagging, reference-tag matching — so they all agree
    on what a transparent-background image looks like instead of each having
    their own copy of this that could quietly drift apart.
    """
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return image.convert("RGB")

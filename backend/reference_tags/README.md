# Reference images for custom tags

To improve matching for a specific custom tag (especially named entities/characters
that the bare word may not pin down well, e.g. "zeus"), create a subfolder here
named exactly like the tag and drop a few example pictures in it:

```
reference_tags/
  zeus/
    statue1.jpg
    statue2.png
    painting1.jpg
```

An image gets the tag when **either** the word matches it
(`RAM_CUSTOM_TAG_THRESHOLD`, default 0.06) **or** it looks like any one of these
pictures (`RAM_CUSTOM_TAG_REFERENCE_THRESHOLD`, default 0.70). 3-5 clear,
tightly cropped examples work best; vary the pose/style you want to catch.

On CPU this compares whole images, so the subject has to be prominent -
roughly half the picture's width or more; a small instance on a busy sheet is
usually missed. When a reindex finds a GPU it instead compares the examples
with the objects *inside* each image (OWLv2 boxes), which finds small ones too
(`INDEX_EXAMPLE_DETECTION`: `auto` by default, or `on` / `off`).

After adding or changing reference images, click **Reindex** in Settings for the
change to take effect.

Tags with no matching folder here are matched by the word alone.

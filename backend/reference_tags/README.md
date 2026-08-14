# Reference images for custom tags

To improve matching for a specific custom tag (especially named entities/characters
that CLIP's bare text embedding alone may not pin down well, e.g. "zeus"), create a
subfolder here named exactly like the tag and drop a few example photos in it:

```
reference_tags/
  zeus/
    statue1.jpg
    statue2.png
    painting1.jpg
```

3-5 clear, representative example images per tag is usually enough. These are
blended with the tag's text embedding into one match target — reindex (Save &
Reindex in Settings) after adding or changing reference images for the change to
take effect.

Tags with no matching folder here work exactly as before (text-only matching).

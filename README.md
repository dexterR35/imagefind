# ImageFind

Point ImageFind at a folder of images and it automatically figures out what's in
each one — so you can search your pictures the way you'd search the web, instead of
scrolling through folders looking for the one you remember.

## What it does

- **Looks at every image and figures out what's in it** — objects, scenes, people,
  animals, materials — automatically, with no manual labeling.
- **Reads any text written on the image** — logos, signs, banners, watermarks —
  so you can search for words that appear on a picture, not just in its filename.
- **Notices the main colors** in each image, so you can filter by "red", "gold",
  "blue", and so on.
- **Finds images that look alike** — click a picture and it pulls up other images
  that visually resemble it.
- **Search box** — type a word and it finds every image where that word shows up,
  either as something the app recognized in the picture or as text printed on it.

## How it works

The first time (and any time you add new pictures), you run **Reindex**. The app
goes through the folder and looks at every image once, remembering:

- what it recognized in the picture
- any text written on it
- its main colors
- a "fingerprint" of what it generally looks like, used later for "find similar"

That's it — after that, search is instant, because the app already knows what's in
every picture. Adding new photos later and reindexing only looks at the new ones,
so it stays fast even with a large, growing collection.

## When it works well — and when it doesn't

- It's very good at recognizing **general things**: people, animals, food, sports
  equipment, furniture, vehicles, common objects and scenes.
- It reads **printed text** reliably when the text is reasonably clear.
- It can tell you two images **look similar**, even if neither has obvious matching
  words.
- It's **not** good at recognizing specific named people or characters by default
  (for example, it can tell a picture shows a statue, but it won't automatically
  know it's a statue of *Zeus* specifically) — it only knows general categories out
  of the box. If you have specific names or characters you care about, they can be
  taught to the app, and giving it a few example pictures of that specific
  character makes it noticeably better at recognizing it.
- Search only shows results it can actually explain — if it doesn't recognize
  something or the word doesn't appear anywhere in the image, it won't guess.

## Examples

Two real images from the app, with nothing typed in by hand — everything shown
below each one was figured out automatically just by looking at the picture.

<img src="example/1.png" width="320" alt="A red pouch full of fruit, a gold lucky 7, and clovers, spilling coins">

> **Colors noticed:** red, white, gold
> **Things recognized:** a bag/pouch, coins, a clover, a diamond, fruit (banana,
> grape, cherry), gold, numbers, a "pot of gold" theme

Typing **"clover"** into search finds this image immediately, because the app
already recognized a clover in the picture on its own.

<img src="example/2.png" width="320" alt="A promotional storyboard collage of a woman, with a long paragraph of text underneath it">

> **Colors noticed:** black, orange, brown
> **Things recognized:** a photo, a computer/cinema screen, a logo, a hat, a
> diamond, a person, a woman
> **Text read from the image:** an entire paragraph of ad-script copy, in
> Romanian — "NETBET TRAIESTE MOMENTUL CONCEPT: Net Bet Pentru cont nou...", read
> word for word even though it's dense, small, and spans the whole image

Typing **"netbet"** — or any other word from that paragraph — into search finds
this image immediately, because the app already read every word printed on it.

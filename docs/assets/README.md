# Website assets

## Application screenshots

`screenshots/overlay.png`, `checklist.png`, `settings-ai.png` and `settings-voice.png`
are captures of the repository's actual Qt widgets, rendered with safe sample content by
`build/capture_site.py`. They retain the current French UI. No personal configuration,
credentials, microphone recording or live DCS session is involved.

## Aviation artwork

`aviation-artwork.webp` is AI-generated atmospheric artwork, not a DCS World screenshot.
Created with the built-in ImageGen tool, then encoded as WebP for a smaller page download.
The page labels it as artwork. The original PNG is retained outside the repository in
the generating session's image library.

Final generation prompt:

> Create a cinematic aviation artwork for the homepage of DCS Copilote, a voice assistant for a flight simulator. Wide landscape aspect ratio 16:9 or wider, high resolution. Scene: realistic two-seat twin-tail naval fighter jet resembling an F/A-18 viewed from the rear left quarter, banking gently above a vast layered cloudscape at dusk. The aircraft is primarily in the right half of the frame, crisp beautiful metallic surface details, understated warm rim light on wings, cool slate blue sea and cloud layers, soft pale peach horizon. Large tranquil dark navy negative space across the LEFT third for white typography. Premium editorial aviation photography aesthetic, restrained film grain, deep blacks, natural light, beautiful atmospheric depth, believable aerodynamic silhouette. No weapon firing, no explosions, no text, no logos, no watermark, no HUD, no interface elements. This is atmospheric artwork, not a screenshot of a video game.

## Parallax layers (version 2)

- `parallax-sky.webp`: the existing artwork edited to remove the aircraft and reconstruct the cloudscape.
- `parallax-aircraft.webp`: the aircraft extracted onto a true transparent background.

Both edits use the built-in ImageGen tool with `aviation-artwork.webp` as the reference.
The images were encoded as WebP, preserving the aircraft's alpha channel. Their original
generated PNGs remain in the session's image library. These are illustrations, not simulator captures.

Background edit prompt:

> Edit this aviation artwork for a layered parallax website. Remove the entire fighter aircraft and all missiles/pylons from the scene. Reconstruct the cloudscape, islands, sea and sunset seamlessly in the area it occupied. Preserve the beautiful existing navy blue and warm sunset light, perspective, horizon height, scene composition and wide 1983 by 793 aspect ratio. The whole output must be only the empty atmospheric sky, clouds and distant sea. No planes, no other vehicles, no text, no UI, no logos. Keep ample dark negative space on the left. Photorealistic cinematic background plate, a clean scene for placing a separate aircraft cutout over it.

Aircraft edit prompt:

> Extract ONLY the entire fighter aircraft from this aviation artwork as an immaculate high-resolution transparent-background cutout. Preserve the rear-left three-quarter view, whole silhouette including BOTH wings, tail fins, canopy and engines, original proportions, realistic metal texture and warm sunset highlights. Remove all clouds, sky, water and islands completely to true transparent alpha. Center the complete aircraft on a wide landscape transparent canvas, keeping the entire wingspan visible with a modest transparent margin, no cropping. No added shadow on a surface, no lettering, no logos. This is the foreground aircraft layer for a premium parallax aviation website.

## Fonts

- [Manrope](https://github.com/google/fonts/tree/main/ofl/manrope), variable TrueType font.
- [Space Grotesk](https://github.com/google/fonts/tree/main/ofl/spacegrotesk), variable TrueType font.

Both are self-hosted under the SIL Open Font License. The original license notices are
included alongside the fonts in `fonts/`. No Google Fonts request is made by the website.

## Branding

`marque.svg` and `icone.svg` are copies of the project's existing artwork in `assets/logo/`.

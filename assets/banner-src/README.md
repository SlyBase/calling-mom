# Banner and icon sources

`../calling-mom-banner.jpg` and `../calling-mom-icon.png` are built from these files.
The output is deterministic (fixed seeds).

| File | Role |
|---|---|
| `gen2b.png` | AI-generated engraving (Thetis answers Achilles, Iliad I), the raw motif |
| `olymp.png` | AI-generated engraving of Mount Olympus (the rare top tier), placed small and dim in the banner |
| `compose_art.py` | Splits the motif into two layers (goddess 1.15x, hero 0.84x), removes the baked-in frame, adds contrast and shadows -> `art.png` |
| `make_banner.py` | 1200x600 banner in the Hermes official-plugin style -> `../calling-mom-banner.jpg` |
| `make_icon.py` | 1024x1024 rounded icon tile -> `../calling-mom-icon.png` |

Run from this folder with Pillow and NumPy installed. The scripts expect a `fonts/`
folder with `Anton.ttf` and `ArchivoBlack.ttf` from Google Fonts (not committed):

```bash
python compose_art.py && python make_banner.py && python make_icon.py
```

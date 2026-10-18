# Assets

| Folder | What | Made by |
|---|---|---|
| `characters/<id>/` | standing art, `<pose>_<mood>.png`, 600 × 900 transparent | `scripts/art/make_art.py` + `finish.py` |
| `characters/manifest.json` | the list the game reads | `finish.py` (rebuilt automatically) |
| `characters/_references/<id>.png` | each character's chosen reference: the face every new image follows | `make_art.py pick` |
| `characters/_raw/` | full-size sources on grey (`_reference.png`, the `<pose>_neutral` images new emotions are edited from) and `costs.jsonl`; not committed | `make_art.py` |
| `sprites/<id>.png` | office-map sprite sheets (Boss and the four) | `scripts/art/map_sprites.py cut` |
| `sprites/_raw/` | the generated turnaround and sitting sheets `cut` works from; not committed | `map_sprites.py make` |
| `office/` | the office map, the chair backrest and `layout.json` | `scripts/art/office.py` |

How to add an emotion, a pose or a new look: `docs/character_art.md`.

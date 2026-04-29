# Assets

Put character-pack portraits under:

```text
assets/
  characters/
    猫娘/
      正常.png
      开心.png
      思考中.png
```

`desktop_pet_next` now scans `assets/characters/<outfit>/<emotion>.png` inside
this pack. If no images are present, it falls back to the bundled catgirl assets
inside `desktop_pet_next`.

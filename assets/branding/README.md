# DAPManager branding

`dapmanager-logo.png` is the exact, unmodified logo supplied by the user. It is
the canonical source for every app icon. Do not redraw, recolour, crop, or add
elements to it.

Source SHA-256: `6f0062c2f2aef776c12dac2b70608a0743b2c94a35f88cbefd8bb7b6b05596dd`

Regenerate native desktop icons from the repository root with:

```sh
cd desktop
npm run icons
```

Platform-required resized PWA icons are generated from the same source and checked into
`web/static/icons/` so iOS and browsers do not depend on SVG icon support.

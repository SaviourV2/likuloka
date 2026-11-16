# likuloka

## Layout

- `plan3d/` is Roomlines, a floor plan editor with a 3D view. `plan3d/README.md` documents the plan format, the state machines and the `window.roomlines` API.
- `scripts/floorcraft_api/` is a Python client for a third-party site's API. It is unrelated to `plan3d/`.
- `.agents/skills/` holds installed agent skills. Do not edit them by hand.

## Working on plan3d

- Edit `plan3d/app.html` only. `plan3d/index.html` is generated from it by `./plan3d/build.sh`; never edit it directly, and rebuild before committing.
- After any change, run the tests and keep them green:

  ```bash
  ./plan3d/build.sh && cd plan3d/tests && npm install && npm test
  ```

- The page is one file with no bundler. Libraries load from a CDN as classic scripts, pinned to an exact version. Add a library only if it ships a browser (UMD) build.
- Flows with ordered steps or interruptible work are XState machines in the "state machines" section of `app.html`. Add a state or a machine for new behaviour of that kind; do not add a boolean flag. Plain settings stay in `state`.
- Change rooms through `addRoom`, `updateRoom` and `removeRoom`. They validate input and are what `window.roomlines` exposes.
- To check behaviour in a browser, read `roomlines.getState()` or the `data-tool`, `data-drawing`, `data-describe` and `data-read` attributes on `.wrap` instead of inferring state from the DOM.
- All lengths are metres on a 0.25 m grid. `x` runs left to right and `y` top to bottom.

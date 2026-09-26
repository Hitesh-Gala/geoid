# Geoid Globe

A ready-made 3-D globe as JSON, plus a drop-in renderer, for plotting satellites
from your own TLEs. Drag to rotate, scroll or pinch to zoom.

The globe is the **GRACE GGM02C geoid** — the same field NASA renders in
[SVS #3655](https://svs.gsfc.nasa.gov/3655/), where blue is a mass deficit
(weaker gravity) and red a mass excess.

## Why this was rebuilt rather than converted

SVS #3655 publishes no 3-D model. Everything on that page is a *rendering* — a
rotating `.mov`/`.mp4` and two 3840×2160 stills. There is no mesh, no
equirectangular texture, and nothing convertible to JSON. Scraping frames would
have given a globe with the lighting and camera baked in.

So the field was resynthesised from its source instead: the published GGM02C
spherical-harmonic coefficients, evaluated onto a lat/lon grid. The result is
real data — you can query the geoid height at any point, recolour it, change the
resolution, or push it to a higher degree — not a picture of data.

Sanity check against the published geoid, which the output reproduces:

| Feature | Published | This model |
|---|---|---|
| Indian Ocean low (0° N, 78° E) | ≈ −100 m | −101.8 m |
| New Guinea high (5° S, 147° E) | ≈ +75 m | +74.1 m |
| North Atlantic high (58° N, 20° W) | ≈ +60 m | +61.1 m |
| Global range | −106 … +85 m | −106.01 … +84.20 m |

## Layout

```
geoid-globe/
├── index.html               the site itself
├── dist/geoid-globe.json    the globe model (0.40 MB)  <- ship this
├── dist/geoid-globe.js      renderer + satellite layer  <- and this
├── data/                    sample TLEs + coastline GeoJSON
├── build/synth_geoid.py     spherical-harmonic synthesis, no dependencies
└── source/GGM02C.gfc        raw coefficients from ICGEM (1.5 MB, input only)
```

Only the two files in `dist/` are needed to embed the globe elsewhere.

## Quick start

```html
<script src="https://unpkg.com/three@0.157.0/build/three.min.js"></script>
<script src="https://unpkg.com/satellite.js@5.0.0/dist/satellite.min.js"></script>
<script src="geoid-globe.js"></script>
<script>
  const globe = GeoidGlobe.create({
    container: '#globe',
    modelUrl: 'geoid-globe.json'
  });

  globe.ready.then(async () => {
    const tle = await fetch('my-satellites.tle').then(r => r.text());
    globe.loadTLEText(tle);
  });
</script>
```

That's the whole integration. Satellites propagate in real time via SGP4 and
ride at their true altitude.

## The JSON model

```jsonc
{
  "format": "geoid-globe/1.0",
  "grid": {
    "units": "meters",
    "nLat": 181, "latStart": 90.0,  "latStep": -1.0,
    "nLon": 361, "lonStart": -180.0, "lonStep": 1.0,
    "order": "row-major: latitude outer (north to south), longitude inner",
    "wrapsLongitude": true
  },
  "stats":    { "min": -106.01, "max": 84.2, "mean": -0.842, "count": 65341 },
  "colormap": { "domain": [-106.01, 84.2], "center": 0.0, "stops": [ /* ... */ ] },
  "earth":    { "meanRadiusKm": 6371.0087714, /* ... */ },
  "source":   { /* model, degree, credits */ },
  "undulation": [ [ /* 361 values */ ], /* × 181 rows */ ]
}
```

`undulation[j][i]` is the geoid height in metres at
`lat = 90 − j`, `lon = −180 + i`. Longitude `−180` is repeated at `+180` so the
mesh closes without a seam.

The colormap is diverging over lopsided data, so `center` marks the value that
must land on the neutral stop — each half of the ramp is stretched
independently. A renderer that ignores `center` will paint sea level orange.

## API

### Creating

```js
GeoidGlobe.create({
  container: '#globe',        // element or selector
  modelUrl: 'geoid-globe.json',
  radius: 100,                // scene units for the Earth's surface
  relief: 0.035,              // bump height as a fraction of radius
  coastlinesUrl: null,        // optional GeoJSON, draped on the surface
  graticule: true,
  initialLat: 15, initialLon: 20, initialZoom: 3.0,
  satSize: 0.02,
  background: 0x05070d,       // or null for a transparent canvas
  subdivision: 1,             // 2 or 3 to decimate the mesh on weak hardware
  autoRotate: false,
  timeScale: 1                // simulated seconds per real second
})
```

`globe.ready` is a promise resolving once the model has loaded.

### Satellites

```js
globe.loadTLEText(text, {
  limit: 2000,
  filter: (name, noradId) => true,
  color: (name, id) => 0x67c8ff        // or a plain hex int
});                                     // -> number added

globe.addSatellite({ id, name, tle: [line1, line2], color });   // propagated
globe.addSatellite({ id, name, lat, lon, altKm, color });       // static point

globe.showOrbit(id, { minutes, steps, color });        // wavy ground track
globe.showOrbitEllipse(id, { minutes, steps, ... });   // the orbit in space
globe.hideOrbit(id);
globe.hideAllOrbits();
globe.removeSatellite(id);
globe.clearSatellites();
globe.getSatellite(id);      // { id, name, lat, lon, altKm, color, satrec }
globe.satellites();
```

Satellite positions are computed from the true Earth-fixed vector, so a dot
always lands exactly on its own orbit curve, and relief exaggeration never
distorts an altitude. A satellite at 400 km sits at 400 km however lumpy you
make the globe.

### Orbits in space, and the gravity distortion

`showOrbitEllipse()` draws the orbit as a closed loop around the globe rather
than as a ground track, by holding Earth's orientation fixed at the current
instant. It returns a `THREE.Group` of up to three curves:

| Curve | Colour | What it is |
|---|---|---|
| `orbit-ideal` | green, thicker | the two-body Kepler ellipse through the same position and velocity — the orbit a perfectly spherical Earth would give |
| `orbit-actual` | red, thinner | where SGP4 really flies over one revolution |
| `orbit-deviation` | red, translucent | the same real path with its departure from the ideal magnified, so the distortion is visible at all |

Colours and widths are overridable per call via `idealColor`, `color`,
`deviationColor`, `idealWidth` and `realWidth` (widths as a fraction of globe
radius). All three are drawn as **swept tubes, not lines**: WebGL ignores
`LineBasicMaterial.linewidth` on essentially every platform, so a `Line` can
never be made thicker. A tube costs a few thousand triangles and actually
honours the width.

`group.userData` carries `periodMin`, `semiMajorKm`, `eccentricity`,
`perigeeKm`, `apogeeKm`, `maxDeviationKm` and `deviationScale`.

**Two honest caveats.** First, the real deviation is tiny — around 9 km on a
1000 km LEO orbit, roughly 0.1% of the orbital radius — so at true scale the
solid and dashed curves sit on top of each other. The amber curve exists because
otherwise you would see nothing; `deviationScale` is the magnification applied,
and it is reported in the UI so the amplified curve is never mistaken for the
real one. Set `deviationTarget: 0` to suppress it.

Second, and more important: that deviation is Earth's **low-order zonal
gravity**, dominated by J2 oblateness, because that is what SGP4 models. It is
*not* the full GGM02C field the globe is coloured with. A TLE does not carry
enough information to reproduce fine geoid structure. So the amber curve shows
the dominant way gravity bends the orbit, not the whole of it.

Deviation is measured point-to-*segment* against the ideal polyline. Measuring
to the nearest vertex instead would report half the sample spacing as
"deviation" — about 276 km of pure discretisation noise on a GEO orbit, which
would swamp the real 3 km effect.

### Querying the field

```js
globe.geoidAt(lat, lon);        // metres, bilinear interpolation
globe.colorAt(lat, lon);        // hex int from the colormap
globe.toVector(lat, lon, altKm); // THREE.Vector3 in scene space
globe.surfaceRadius(lat, lon);   // displaced radius, for your own overlays
```

### View and time

```js
globe.pointOfView(lat, lon, zoom);   // zoom is a multiple of radius
globe.setExaggeration(0.09);         // rebuilds mesh + draped overlays
globe.setSatelliteSize(0.035);       // dot size; the pick radius follows it
globe.setAutoRotate(true, 0.06);
globe.setTime(new Date()); globe.setTimeScale(60);
globe.play(); globe.pause(); globe.isPaused();
globe.dispose();
```

### Events

```js
globe.on('select', sat => { /* sat is null when clicking empty space */ });
globe.on('hover',  sat => { /* for tooltips */ });
globe.on('tick',   date => { /* every frame, with the simulated time */ });
```

## Controls

Hand-rolled, so there's no OrbitControls dependency and nothing competes for
touch gestures:

- **one finger / left drag** — rotate
- **two fingers** — pinch to zoom, drag to rotate
- **wheel** — zoom, ~9.5% per notch, normalised across mice and trackpads

Zoom is tracked in **log distance**, not linear distance. Zoom is inherently
multiplicative — "twice as close" means the same thing from anywhere — so a
gesture changes distance by a constant *percentage* rather than a constant
number of units. Linear tracking makes the same input move you a little near
the surface and a lot far out, which is what reads as lurching. It also makes
the gesture exactly reversible: pinch out and back and you land on the distance
you started from, to the digit.

`pinchGain` (default 0.5) sets how much of a pinch becomes zoom. It is
deliberately below 1: a 1:1 "globe sticks to your fingers" mapping sounds
right, but the usable distance range is only about 11× end to end, so one
ordinary pinch would consume nearly all of it. At 0.5 a 3× finger spread zooms
√3 ≈ 1.73×, leaving room to work.

Tunable at creation: `pinchGain`, `wheelStep` (default 0.1), `zoomEasing`
(default 0.1 — slower than the 0.18 used for rotation, because a longer settle
reads as smooth on zoom but as unresponsive on rotation).

Zoom is clamped above the tallest bump, so the camera can't end up inside the
terrain when relief is exaggerated.

## Regenerating the model

```bash
python build/synth_geoid.py                      # degree 200, 1° grid, ~20 s
python build/synth_geoid.py --degree 60          # smoother, broad features only
python build/synth_geoid.py --step 0.5           # 4× the samples, ~1.6 MB
```

Pure standard library — no numpy. Coefficients are read from
`source/GGM02C.gfc`; any ICGEM `.gfc` model (EGM96, EIGEN-GL04C, …) drops in
unchanged.

Note that degree 200 on a 1° grid is already past Nyquist for the grid, so
`--step 0.5` genuinely resolves more; going beyond degree 200 needs a different
coefficient file, since that's GGM02C's limit.

## Performance

The default mesh is 65,341 vertices / 129,600 triangles, which is comfortable on
a phone. `subdivision: 2` quarters that if you're also drawing tens of thousands
of satellites. Satellites render as a single `THREE.Points` object — one draw
call regardless of count.

## Running locally

```bash
python -m http.server 8095 --directory geoid-globe
```

Then open <http://localhost:8095/>. It loads a 300-object sample catalogue and
accepts your own `.tle` file through the file picker. Static files only — no
build step, no bundler, no backend.

## Credits

Gravity model **GGM02C** — Center for Space Research, University of Texas at
Austin, from GRACE (NASA/DLR). Coefficients distributed by
[ICGEM, GFZ Potsdam](https://icgem.gfz.de/).

Visual design after **NASA/Goddard Space Flight Center Scientific Visualization
Studio**, [SVS #3655](https://svs.gsfc.nasa.gov/3655/) — animation by Adam
Martin, produced by Paul Reddish, science by John C. Ries and Scott Luthcke.

NASA SVS material is generally public domain; credit NASA/GSFC SVS if you reuse
the look. Check the source page for the specific terms before publishing.

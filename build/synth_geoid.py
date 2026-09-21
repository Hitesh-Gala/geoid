#!/usr/bin/env python3
"""
Synthesise a geoid-undulation grid from GGM02C spherical-harmonic coefficients
and emit it as a web-ready JSON globe model.

This is the data behind NASA SVS #3655 ("GRACE Gravity Model"), which ships only
rendered video -- so we rebuild the field from the published coefficients instead.

Undulation relative to the WGS84 ellipsoid, to first order (Bruns):

    N(phi, lam) = R * sum_{n=2}^{Nmax} sum_{m=0}^{n}
                    ( dC_nm cos(m*lam) + S_nm sin(m*lam) ) * Pbar_nm(sin phi)

where dC_nm is the model coefficient minus the reference ellipsoid's even zonal
harmonics. Accurate to roughly a metre, which is far below what any renderer can
show -- the field spans about -106 m to +85 m.

No third-party dependencies: fully-normalised Legendre functions are built with
the standard column-wise recursion, which is stable well past degree 200.

Usage:  python synth_geoid.py [--degree 200] [--step 1.0]
"""

import argparse
import json
import math
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GFC = os.path.join(ROOT, "source", "GGM02C.gfc")
OUT = os.path.join(ROOT, "dist", "geoid-globe.json")

# WGS84 reference ellipsoid, unnormalised even zonal harmonics.
WGS84_J = {2: 1.08262982131e-3, 4: -2.37091120053e-6, 6: 6.08346498882e-9,
           8: -1.42681087920e-11, 10: 1.21439275882e-14}


def read_gfc(path, max_degree):
    """Parse an ICGEM .gfc file -> (meta, C, S) with C/S as dicts keyed (n, m)."""
    meta, C, S = {}, {}, {}
    in_head = True
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            if in_head:
                if parts[0] == "end_of_head":
                    in_head = False
                elif len(parts) >= 2:
                    meta.setdefault(parts[0], parts[1])
                continue
            # 'gfc' is a static coefficient; 'gfct' is one with a reference
            # epoch (the time-variable terms 'dot'/'trnd'/'asin'/'acos' that
            # follow it are drift rates we deliberately ignore -- we want the
            # mean field, not its 2002-2003 secular trend).
            if parts[0] not in ("gfc", "gfct"):
                continue
            n, m = int(parts[1]), int(parts[2])
            if n > max_degree:
                continue
            C[(n, m)] = float(parts[3].replace("D", "E").replace("d", "e"))
            S[(n, m)] = float(parts[4].replace("D", "E").replace("d", "e"))
    return meta, C, S


def subtract_ellipsoid(C, max_degree):
    """Remove WGS84's own zonal field so what remains is undulation, not radius."""
    for n, J in WGS84_J.items():
        if n > max_degree:
            continue
        # Fully-normalised form of an unnormalised zonal: Cbar_n0 = -J_n / sqrt(2n+1)
        C[(n, 0)] = C.get((n, 0), 0.0) + J / math.sqrt(2.0 * n + 1.0)


def legendre_column(t, u, max_degree):
    """
    Fully-normalised associated Legendre functions Pbar_nm(t), t = sin(lat).

    Returns a flat list indexed by idx(n, m) = n*(n+1)//2 + m.
    """
    size = (max_degree + 1) * (max_degree + 2) // 2
    P = [0.0] * size

    def idx(n, m):
        return n * (n + 1) // 2 + m

    P[idx(0, 0)] = 1.0
    if max_degree >= 1:
        P[idx(1, 0)] = math.sqrt(3.0) * t
        P[idx(1, 1)] = math.sqrt(3.0) * u

    # Sectorials first: Pbar_mm = u * sqrt((2m+1)/(2m)) * Pbar_(m-1)(m-1)
    for m in range(2, max_degree + 1):
        P[idx(m, m)] = u * math.sqrt((2.0 * m + 1.0) / (2.0 * m)) * P[idx(m - 1, m - 1)]

    # Then each column upward in degree.
    for m in range(0, max_degree + 1):
        if m + 1 <= max_degree:
            P[idx(m + 1, m)] = math.sqrt(2.0 * m + 3.0) * t * P[idx(m, m)]
        for n in range(m + 2, max_degree + 1):
            a = math.sqrt(((2.0 * n - 1.0) * (2.0 * n + 1.0)) /
                          ((n - m) * (n + m)))
            b = math.sqrt(((2.0 * n + 1.0) * (n + m - 1.0) * (n - m - 1.0)) /
                          ((n - m) * (n + m) * (2.0 * n - 3.0)))
            P[idx(n, m)] = a * t * P[idx(n - 1, m)] - b * P[idx(n - 2, m)]
    return P


def synth(C, S, R, max_degree, step):
    """Evaluate the expansion on a regular lat/lon grid. Returns (rows, nLat, nLon)."""
    n_lon = int(round(360.0 / step)) + 1          # +1 duplicates lon=-180 at +180
    n_lat = int(round(180.0 / step)) + 1          # pole to pole inclusive
    lons = [(-180.0 + i * step) for i in range(n_lon)]

    # cos(m*lam) / sin(m*lam) are the same for every row, so build them once.
    cos_ml = [[math.cos(math.radians(m * lam)) for lam in lons]
              for m in range(max_degree + 1)]
    sin_ml = [[math.sin(math.radians(m * lam)) for lam in lons]
              for m in range(max_degree + 1)]

    def idx(n, m):
        return n * (n + 1) // 2 + m

    rows = []
    for j in range(n_lat):
        lat = 90.0 - j * step
        phi = math.radians(lat)
        t, u = math.sin(phi), math.cos(phi)
        P = legendre_column(t, u, max_degree)

        # Collapse the degree sum first: A_m = sum_n Cbar_nm * Pbar_nm.
        A = [0.0] * (max_degree + 1)
        B = [0.0] * (max_degree + 1)
        for n in range(2, max_degree + 1):
            base = n * (n + 1) // 2
            for m in range(0, n + 1):
                p = P[base + m]
                if p == 0.0:
                    continue
                c = C.get((n, m))
                if c:
                    A[m] += c * p
                s = S.get((n, m))
                if s:
                    B[m] += s * p

        # Then the order sum, across longitude.
        row = []
        for i in range(n_lon):
            total = A[0]
            for m in range(1, max_degree + 1):
                am, bm = A[m], B[m]
                if am or bm:
                    total += am * cos_ml[m][i] + bm * sin_ml[m][i]
            row.append(round(R * total, 2))
        rows.append(row)

        if j % 20 == 0:
            print(f"  lat {lat:+6.1f}  ({j + 1}/{n_lat})", flush=True)
    return rows, n_lat, n_lon


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degree", type=int, default=200,
                    help="truncate the expansion at this degree (default 200 = full GGM02C)")
    ap.add_argument("--step", type=float, default=1.0,
                    help="grid spacing in degrees (default 1.0)")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    t0 = time.time()
    print(f"Reading {GFC} ...")
    meta, C, S = read_gfc(GFC, args.degree)
    GM = float(meta["earth_gravity_constant"].replace("D", "E"))
    R = float(meta["radius"].replace("D", "E"))
    print(f"  model={meta.get('modelname')}  max_degree={meta.get('max_degree')}"
          f"  GM={GM:.6e}  R={R:.1f} m  ({len(C)} coefficients kept)")

    subtract_ellipsoid(C, args.degree)
    C.pop((0, 0), None)  # degree 0 is the mean radius, not undulation

    print(f"Synthesising {args.step} deg grid to degree {args.degree} ...")
    rows, n_lat, n_lon = synth(C, S, R, args.degree, args.step)

    flat = [v for row in rows for v in row]
    vmin, vmax = min(flat), max(flat)
    mean = sum(flat) / len(flat)

    model = {
        "format": "geoid-globe/1.0",
        "name": "GRACE GGM02C geoid",
        "description": (
            "Geoid undulation relative to the WGS84 ellipsoid, synthesised from the "
            "GGM02C spherical-harmonic gravity model. Positive values are where the "
            "geoid stands above the ellipsoid (stronger local gravity / excess mass)."
        ),
        "source": {
            "model": meta.get("modelname", "GGM02C"),
            "maxDegreeAvailable": int(meta.get("max_degree", 200)),
            "maxDegreeUsed": args.degree,
            "tideSystem": meta.get("tide_system", "zero_tide"),
            "coefficients": "https://icgem.gfz.de/ (ICGEM, GFZ Potsdam)",
            "originator": "Center for Space Research, University of Texas at Austin",
            "inspiration": "https://svs.gsfc.nasa.gov/3655/ - NASA/Goddard SVS, GRACE Gravity Model",
            "credit": "NASA/Goddard Space Flight Center Scientific Visualization Studio; GRACE (NASA/DLR)",
        },
        "earth": {
            "modelRadiusM": R,
            "GM": GM,
            "wgs84SemiMajorKm": 6378.137,
            "wgs84FlatteningInv": 298.257223563,
            "meanRadiusKm": 6371.0087714,
        },
        "grid": {
            "units": "meters",
            "nLat": n_lat,
            "nLon": n_lon,
            "latStart": 90.0,
            "latStep": -args.step,
            "lonStart": -180.0,
            "lonStep": args.step,
            "order": "row-major: latitude outer (north to south), longitude inner (west to east)",
            "wrapsLongitude": True,
        },
        "stats": {
            "min": round(vmin, 2),
            "max": round(vmax, 2),
            "mean": round(mean, 3),
            "count": len(flat),
        },
        # Blue (mass deficit) through green to red (mass excess) -- the SVS palette.
        # Diverging, and the data is lopsided (-106 m vs +84 m), so the ramp is
        # pinned piecewise: `center` is the value that must land on the neutral
        # stop. Each half then uses its full half of the palette, which keeps the
        # ellipsoid at green without throwing away contrast on the positive side.
        "colormap": {
            "domain": [round(vmin, 2), round(vmax, 2)],
            "center": 0.0,
            "stops": [
                [0.00, [12, 20, 90]],
                [0.18, [20, 70, 190]],
                [0.36, [40, 160, 220]],
                [0.50, [70, 200, 140]],
                [0.64, [230, 220, 90]],
                [0.82, [235, 130, 45]],
                [1.00, [170, 20, 25]],
            ],
        },
        "undulation": rows,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(model, fh, separators=(",", ":"))

    size = os.path.getsize(args.out)
    print(f"\nWrote {args.out}")
    print(f"  {n_lat} x {n_lon} = {len(flat)} samples, {size / 1e6:.2f} MB")
    print(f"  undulation {vmin:.2f} .. {vmax:.2f} m (mean {mean:.3f})")
    print(f"  {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

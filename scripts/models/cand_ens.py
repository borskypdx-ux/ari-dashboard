"""Candidate "ens": horizon-weighted ensemble of built-in models in log space.

    log yhat_h = wP_h * profile_h + wC_h * clim_anomaly_h + wN_h * seasonal_naive_h

Members are the unchanged built-ins from scripts/forecast.py.  Weights were fitted
ONLY on the tune split (origins in seasons 2012-2018): for each horizon h a grid
search over the simplex (step 0.05) minimising the mean |log error| (mean rather
than median |error| because it is a much less noisy estimator of the weights;
leave-one-season-out CV on tune: 8.88 vs 9.01 for median-fitted per-h weights).

Shape of the fitted weights: at h=1-2 a roughly equal mix of all three (local
level / last year's shape / climatology); from h>=3 climatology+anomaly dominates
(profile compounds weekly growth with no mean reversion, which hurts at long h).

If last year's data needed by seasonal_naive is missing (gaps), its weight is
redistributed proportionally to the other two members.  If the origin week itself
is missing from `hist`, the forecast is made from the last available week and the
extra steps are dropped.

Pure stdlib, deterministic, reads no files: uses only `hist` (data up to origin).
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forecast as F  # noqa: E402

# (w_profile, w_clim_anomaly, w_seasonal_naive) per horizon h = 1..8 — fitted on tune only
WEIGHTS = {
    1: (0.35, 0.35, 0.30),
    2: (0.35, 0.35, 0.30),
    3: (0.10, 0.65, 0.25),
    4: (0.00, 0.80, 0.20),
    5: (0.00, 0.85, 0.15),
    6: (0.00, 0.85, 0.15),
    7: (0.00, 0.85, 0.15),
    8: (0.00, 0.90, 0.10),
}


def _w(h):
    return WEIGHTS.get(h, WEIGHTS[max(WEIGHTS)])


def _core(hist, origin, H):
    lp = F.m_profile(hist, origin, H)
    lc = F.m_clim_anomaly(hist, origin, H)
    ln = F.m_seasonal_naive(hist, origin, H)
    y0, w0 = origin
    base = hist.get((y0 - 1, F.wk(w0)))
    out = []
    for h in range(1, H + 1):
        wp, wc, wn = _w(h)
        fy, fw = F.week_add(y0, w0, h)
        if wn and not (base and hist.get((fy - 1, F.wk(fw)))):
            s = wp + wc  # seasonal_naive degenerated to persistence -> drop it
            wp, wc, wn = wp / s, wc / s, 0.0
        out.append(wp * lp[h - 1] + wc * lc[h - 1] + wn * ln[h - 1])
    return out


def predict(hist, origin, H):
    if hist.get(origin):
        return _core(hist, origin, H)
    # origin week missing: anchor on the last available week before it
    oi = F.week_index(*origin)
    avail = [k for k, v in hist.items() if v and F.week_index(*k) <= oi]
    last = max(avail, key=lambda k: F.week_index(*k))
    gap = oi - F.week_index(*last)
    return _core(hist, last, H + gap)[gap:]

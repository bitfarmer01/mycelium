/* Pure prior math, mirrored from core/learn.py prior_update — feature 35.
   This module must stay dependency-free: the parity test imports it directly
   with node, so it cannot touch the DOM or any sibling module. The arithmetic
   mirrors the Python op-for-op (same order of operations) so a client preview
   matches the server to bit-exact float precision. */

/* Python:
   p = [pi + rate * ((1.0 if i == role_idx else 0.0) - pi) for i, pi in enumerate(prior)]
   s = sum(p); p = [x / s for x in p]
   then floor-pinning loop: entries below floor are fixed, the rest rescaled to fill 1 - floor*len(fixed). */
function predictPrior(prior, roleIdx, rate, floor) {
  if (floor == null) floor = 0.05;
  var p = prior.map(function (pi, i) { return pi + rate * ((i === roleIdx ? 1.0 : 0.0) - pi); });
  var s = p.reduce(function (a, x) { return a + x; }, 0);
  p = p.map(function (x) { return x / s; });
  var fixed = [];
  for (;;) {
    var low = [];
    p.forEach(function (x, i) { if (fixed.indexOf(i) < 0 && x < floor) low.push(i); });
    if (!low.length) break;
    fixed = fixed.concat(low);
    var free = p.map(function (_, i) { return i; }).filter(function (i) { return fixed.indexOf(i) < 0; });
    var rest = 1.0 - floor * fixed.length;
    var fs = free.reduce(function (a, i) { return a + p[i]; }, 0);
    p = p.map(function (x, i) {
      if (fixed.indexOf(i) >= 0) return floor;
      return fs > 0 ? p[i] / fs * rest : rest / Math.max(1, free.length);
    });
  }
  return p;
}

export { predictPrior };

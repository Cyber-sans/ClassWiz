"""Steady-state cached throughput: time two cached runs of different size and take the marginal rate
(removes the fixed ~2-3 s cost of starting 12 worker processes and loading the model in each)."""
import os

from extract import split_samples
from run_meta import run

if __name__ == "__main__":
    total, (dev, _b, _s6, _s24, bench) = split_samples(sizes=(4000, 3000, 1500, 1500, 3000))
    t_small, r1 = run(bench, os.devnull)
    t_big, r2 = run(dev + bench, os.devnull)
    assert r1 == r2 == 0, "sample is not fully cached"
    rate = len(dev) / (t_big - t_small)
    startup = t_small - len(bench) / rate
    print(f"3000 docs: {t_small:.2f}s   7000 docs: {t_big:.2f}s")
    print(f"steady-state cached rate: {rate:.0f} docs/sec (fixed startup ~{startup:.1f}s)")
    print(f"est cached pass over {total} PDFs: {startup + total / rate:.0f}s")

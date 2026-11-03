"""Deterministic simulation of the scene situation model (no camera/model/network)."""
import os, random, sqlite3, sys, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
from companion.scene_model import SceneSituationModel

class Mem:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False); self._db_lock = threading.RLock()
        self.db.execute("CREATE TABLE companion_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, trigger TEXT, reason TEXT, meta TEXT, nudge_id TEXT)")

SITS = {
 "desk": ["A person sitting at a desk with a laptop and a mug in a bedroom.", "A man working on a laptop at a wooden desk, a lamp and books nearby.",
          "Someone typing on a laptop at a desk in a room.", "A young person sits at a desk looking at a laptop screen, notebooks beside the laptop."],
 "bed": ["A person lying on a bed in a bedroom with a window.", "A man lies on a bed under a blanket, pillows behind him.",
         "Someone is lying down on the bed in a dim bedroom.", "A person laying on a bed near a window with curtains."],
 "meal": ["A person eating food from a plate at a table in a kitchen.", "A man is eating a meal at a small table with a plate of rice.",
          "Someone sits and eats a meal, a plate and a glass of water in front of them."],
}
def make_fake_embedder(seed=7, dim=64):
    """Stand-in for a semantic embedder: same situation -> nearby vectors (cos~0.8), different -> far.
    It exists only to exercise the model's pipeline; real embedding quality must be checked on the PC."""
    proto = {}
    for k in SITS:
        pr = random.Random(f"{seed}{k}"); proto[k] = [pr.gauss(0, 1) for _ in range(dim)]
    def embed(text):
        key = next(k for k, v in SITS.items() if text in v)
        r = random.Random(text)
        return [p + r.gauss(0, 0.45) for p in proto[key]]
    return embed

def run(seed=1):
    rng = random.Random(seed); mem = Mem(); t = 1_762_000_000.0
    m = SceneSituationModel(mem, {}, rng=random.Random(seed), embedder=make_fake_embedder())
    nudges = []
    for day in range(10):
        day0 = t + day * 86400
        plan = [("desk", 9, 50), ("meal", 13, 15), ("desk", 14, 70), ("bed", 22, 15)]
        if day == 8: plan = [("desk", 9, 50), ("meal", 13, 15), ("bed", 15, 120), ("desk", 20, 40)]   # unusually long bed visit
        for sit, hour, minutes in plan:
            start = day0 + hour * 3600; jitter = rng.uniform(.8, 1.2)
            for k in range(int(minutes * jitter * 6)):
                ts = start + k * 10
                m.observe(rng.choice(SITS[sit]) if rng.random() > .05 else rng.choice(SITS[rng.choice(list(SITS))]), ts=ts)
                sig = m.dwell_signal(ts)
                if sig and sig.should_nudge:
                    nudges.append((day, sit, hour, round(sig.dwell_s / 60), round(sig.median_s / 60), round(sig.z, 1))); m.mark_nudged(sig)
            m.end_session()
    return m, nudges
if __name__ == "__main__":
    m, nudges = run()
    print("clusters:", len(m.clusters), "obs:", m.n_obs)
    print("visits per cluster:", dict(m.memory.db.execute("SELECT cluster_id,COUNT(*) FROM scene_visits GROUP BY cluster_id").fetchall()) if hasattr(m,'memory') else "")
    print("nudges (day, truth, hour, dwell_min, median_min, z):", nudges)

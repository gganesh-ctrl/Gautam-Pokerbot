from .exploit import ExploitBot
from .field import ChartNit, ChenMC, GTOLite, Maniac, MultiwayMC, PotOddsStation, RandomBot, Sloppy

REGISTRY = {cls.name: cls for cls in
            (ExploitBot, ChenMC, MultiwayMC, PotOddsStation, ChartNit, Maniac, GTOLite, Sloppy, RandomBot)}
DEFAULT_FIELD = ["chen_mc", "mc_multi", "station", "nit", "maniac", "gto_lite", "sloppy"]

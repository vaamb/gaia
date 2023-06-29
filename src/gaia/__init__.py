__version__ = "0.6.0"

from gaia.config import EcosystemConfig, EngineConfig, get_base_dir, get_config
from gaia.ecosystem import Ecosystem
from gaia.engine import Engine
from gaia.main import Gaia, main
from gaia.shared_resources import scheduler, start_scheduler

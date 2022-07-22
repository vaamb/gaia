from src import Gaia

from config import Config


def test_gaia_init(temp_dir):
    Config.BASE_DIR = temp_dir
    gaia = Gaia(connect_to_ouranos=True, use_database=True)

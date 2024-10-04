from pathlib import Path

from gaia.dependencies.camera import check_dependencies, np, skimage


def load_picture_array(path: Path) -> np.ndarray:
    check_dependencies()
    with path.open("rb") as handler:
        return np.load(handler)


def dump_picture_array(array: np.ndarray, path: Path) -> None:
    check_dependencies()
    with path.open("wb") as handler:
        np.save(handler, array)


def resize(array: np.ndarray, size: tuple[int, int]) -> np.array:
    check_dependencies()
    return skimage.transform.resize(array, size)


def compute_mse(array0: np.ndarray, array1: np.ndarray) -> float:
    check_dependencies()
    return skimage.metrics.mean_squared_error(array0, array1)

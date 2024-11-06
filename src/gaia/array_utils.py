from __future__ import annotations

from pathlib import Path

from gaia.dependencies.camera import check_dependencies, np, cv2


def load_picture_array(path: Path) -> np.ndarray:
    check_dependencies(check_cv2=False)
    with path.open("rb") as handler:
        return np.load(handler)


def dump_picture_array(array: np.ndarray, path: Path) -> None:
    check_dependencies(check_cv2=False)
    with path.open("wb") as handler:
        np.save(handler, array)


def resize(array: np.ndarray, size: tuple[int, int]) -> np.array:
    check_dependencies(check_cv2=True)
    return cv2.resize(array, size)


def compute_mse(array0: np.ndarray, array1: np.ndarray) -> float:
    check_dependencies(check_cv2=False)
    if not array0.shape == array1.shape:
        raise ValueError("The two arrays must have the same shape")
    return np.mean(
        (array0.astype(np.float64) - array1.astype(np.float64)) ** 2,
        dtype=np.float64,
    )


def rgb_to_gray(array: np.ndarray) -> np.ndarray:
    check_dependencies(check_cv2=True)
    return cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)

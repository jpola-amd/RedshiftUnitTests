"""Image comparison of a results folder with the references or with another results folder."""

import json
from dataclasses import asdict, dataclass
from multiprocessing import Pool
from pathlib import Path

import cv2 as cv
import matplotlib
import numpy as np
from colorama import Fore
from skimage.metrics import structural_similarity

from .utils import colored, print_warning

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (the backend must be selected first)
from matplotlib.colors import PowerNorm  # noqa: E402

# redshiftBenchmark appends an info strip (time, GPUs) below the render. It differs between runs, so it is cut off
# before comparing: image height -> height of the rendered part.
BENCHMARK_RENDER_HEIGHT = {
    256: 163,
    341: 254,
    360: 267,
    431: 238,
    450: 357,
    480: 387,
    486: 393,
    512: 420,
    540: 436,
    562: 470,
    576: 484,
    600: 507,
    720: 628,
    729: 637,
    800: 708,
    900: 808,
    917: 814,
    1024: 932,
    1075: 983,
    1080: 988,
    1125: 1032,
    1200: 1097,
    1440: 1336,
    1500: 1408,
    1800: 1708,
    2048: 1956,
    2160: 2057,
    4500: 4408,
}


@dataclass
class ImageComparison:
    name: str
    reference: Path
    result: Path
    mse: float | None = None  # mean squared error over the RGB channels, 0-255 scale
    ssim: float | None = None  # structural similarity, 1 = identical
    error: str | None = None  # the images could not be compared
    failed: bool = False


def load_rgb(path: Path, crop_benchmark_footer: bool) -> np.ndarray:
    image = cv.imread(str(path), cv.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot read {path.name}")
    image = cv.cvtColor(image, cv.COLOR_BGR2RGB)
    if crop_benchmark_footer:
        height = BENCHMARK_RENDER_HEIGHT.get(image.shape[0])
        if height is None:
            print_warning(f"no benchmark crop height for {image.shape[0]} px, comparing the full image {path.name}")
        else:
            image = image[:height]
    return image


def compare_images(reference: np.ndarray, result: np.ndarray) -> tuple[float, float]:
    """(mse, ssim) of two RGB images of the same size."""
    if reference.shape != result.shape:
        raise ValueError(
            f"size differs: reference {reference.shape[1]}x{reference.shape[0]}, "
            f"result {result.shape[1]}x{result.shape[0]}"
        )
    diff = reference.astype(np.float64) - result.astype(np.float64)
    mse = float(np.mean(diff * diff))
    ssim = float(structural_similarity(reference, result, channel_axis=2, data_range=255))
    return mse, ssim


def save_diff_plot(reference: np.ndarray, result: np.ndarray, comparison: ImageComparison, plot_file: Path) -> None:
    diff = np.abs(reference.astype(np.int16) - result.astype(np.int16)).max(axis=2)
    height, width = diff.shape
    fig, axes = plt.subplots(
        ncols=3, figsize=(19.2, max(3.0, 19.2 / 3 * height / width + 1.2)), sharex=True, sharey=True
    )
    axes[0].imshow(reference)
    axes[0].set_title("Reference")
    # Square-root scale so that sparse, small differences stay visible next to large ones.
    norm = PowerNorm(gamma=0.5, vmin=0, vmax=max(1, int(diff.max())))
    shown = axes[1].imshow(diff, cmap="inferno", norm=norm)
    axes[1].set_title(f"Difference (max channel, max {int(diff.max())})")
    fig.colorbar(shown, ax=axes[1], fraction=0.046)
    axes[2].imshow(result)
    axes[2].set_title("Result")
    axes[2].set_xlabel(f"MSE: {comparison.mse:.3f}, SSIM: {comparison.ssim:.4f}")
    plt.tight_layout()
    plt.savefig(str(plot_file), dpi=100)
    plt.close(fig)


def _run_comparison(job: tuple) -> ImageComparison:
    comparison, crop, threshold, min_ssim, plot_folder = job
    try:
        reference = load_rgb(comparison.reference, crop)
        result = load_rgb(comparison.result, crop)
        comparison.mse, comparison.ssim = compare_images(reference, result)
        comparison.failed = comparison.mse > threshold or (min_ssim is not None and comparison.ssim < min_ssim)
        if comparison.failed and plot_folder:
            plot_folder.mkdir(parents=True, exist_ok=True)
            save_diff_plot(reference, result, comparison, plot_folder / f"{comparison.name}.diff.png")
    except Exception as err:  # noqa: BLE001  any problem with one image must not stop the others
        comparison.error = str(err)
        comparison.failed = True
    return comparison


class ImageAnalyzer:
    """Pairs <name>.result.png in <results>/images with <name>.<reference_suffix>.png in <reference_path>/images."""

    def __init__(
        self,
        reference_path: Path,
        results_path: Path,
        threshold: float = 0.95,
        min_ssim: float | None = None,
        crop_benchmark_footer: bool = False,
        reference_suffix: str = "reference",
        plot_folder: Path | None = None,
    ):
        self.reference_path = reference_path
        self.results_path = results_path
        self.threshold = threshold
        self.min_ssim = min_ssim
        self.crop = crop_benchmark_footer
        self.reference_suffix = reference_suffix
        self.plot_folder = plot_folder
        self.comparisons: list[ImageComparison] = []
        self.missing_references: list[Path] = []

    @property
    def failures(self) -> list[ImageComparison]:
        return [c for c in self.comparisons if c.failed]

    def _pair_images(self) -> list[ImageComparison]:
        pairs = []
        for result in sorted((self.results_path / "images").glob("*.result.png")):
            name = result.name[: -len(".result.png")]
            reference = self.reference_path / "images" / f"{name}.{self.reference_suffix}.png"
            if reference.is_file():
                pairs.append(ImageComparison(name, reference, result))
            else:
                self.missing_references.append(reference)
        return pairs

    def analyze(self) -> list[ImageComparison]:
        print(f"{colored('Analyzing images', Fore.MAGENTA)} of {self.results_path} against {self.reference_path}")
        pairs = self._pair_images()
        for missing in self.missing_references:
            print_warning(f"no reference image {missing}")
        if not pairs:
            print("There is nothing to compare")
            return []
        if self.plot_folder and self.plot_folder.is_dir():
            for old_plot in self.plot_folder.glob("*.diff.png"):
                old_plot.unlink()
        jobs = [(pair, self.crop, self.threshold, self.min_ssim, self.plot_folder) for pair in pairs]
        with Pool() as pool:
            self.comparisons = sorted(pool.imap_unordered(_run_comparison, jobs), key=lambda c: c.name)
        for c in self.comparisons:
            if c.error:
                print(f"\t{colored(c.name, Fore.RED)}: {c.error}")
            else:
                color = Fore.RED if c.failed else Fore.GREEN
                print(f"\t{colored(c.name, color)}: mse={c.mse:.3f}, ssim={c.ssim:.4f}")
        self.print_summary()
        return self.comparisons

    def print_summary(self) -> None:
        failures = sorted(self.failures, key=lambda c: (c.error is None, -(c.mse or 0.0)))
        criteria = f"mse > {self.threshold}" + (f" or ssim < {self.min_ssim}" if self.min_ssim is not None else "")
        print(
            f"\n{colored(len(failures), Fore.BLUE)} of {len(self.comparisons)} images need inspection ({criteria}), "
            f"{len(self.missing_references)} without reference"
        )
        for c in failures:
            detail = c.error or f"mse={c.mse:.3f} ssim={c.ssim:.4f}"
            print(f"\t{colored(c.name, Fore.YELLOW)}: {detail}")
        if failures and self.plot_folder:
            print(f"Diff plots: {self.plot_folder}")

    @staticmethod
    def _record(c: ImageComparison) -> dict:
        record = {k: v for k, v in asdict(c).items() if k in ("mse", "ssim", "error", "failed")}
        return {k: v for k, v in record.items() if v is not None}

    def save(self, file: Path) -> None:
        file.write_text(json.dumps({c.name: self._record(c) for c in self.comparisons}, indent=2), encoding="utf-8")

    def save_mismatch(self, file: Path) -> None:
        if self.failures:
            file.write_text(json.dumps({c.name: self._record(c) for c in self.failures}, indent=2), encoding="utf-8")

"""
OpenCV scan / phone-capture simulation for benchmark documents.

Every augmentation is sampled from a seeded RNG and returned as a plain dict so
results can be correlated with e.g. rotation angle or JPEG quality.

Render modes are `digital` plus one per entry in PROFILES, so adding a scan style
is a single new ScanProfile here; generation, runs and reports pick it up.
"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ScanProfile:
    """Ranges (lo, hi) that each augmentation parameter is sampled from."""

    angle_deg: tuple[float, float]
    perspective: tuple[float, float]  # max corner jitter as a fraction of page size
    blur_sigma: tuple[float, float]
    motion_blur_px: tuple[int, int]  # 0 = none
    noise_std: tuple[float, float]  # gaussian noise std in 0-255 units
    speckle_density: tuple[float, float]  # salt-and-pepper fraction
    jpeg_quality: tuple[int, int]
    contrast: tuple[float, float]
    brightness: tuple[float, float]
    shadow_strength: tuple[float, float]  # uneven illumination gradient
    paper_tint: tuple[float, float]  # 0 = white paper, >0 = warm yellowed paper
    border_px: tuple[int, int]  # dark scanner-bed border around the page
    fold_line_prob: float
    grayscale: bool


PROFILES: dict[str, ScanProfile] = {
    # Clean flatbed scan: tiny skew, light noise.
    "scanned": ScanProfile(
        angle_deg=(-2.0, 2.0),
        perspective=(0.0, 0.0),
        blur_sigma=(0.0, 0.5),
        motion_blur_px=(0, 0),
        noise_std=(2.0, 6.0),
        speckle_density=(0.0, 0.001),
        jpeg_quality=(80, 95),
        contrast=(0.95, 1.05),
        brightness=(-5.0, 5.0),
        shadow_strength=(0.0, 0.05),
        paper_tint=(0.0, 0.03),
        border_px=(0, 0),
        fold_line_prob=0.0,
        grayscale=True,
    ),
    # Old office copier: visible skew, blur, speckle, compression, dark edges.
    "scanned_noisy": ScanProfile(
        angle_deg=(-6.0, 6.0),
        perspective=(0.0, 0.01),
        blur_sigma=(0.4, 1.1),
        motion_blur_px=(0, 3),
        noise_std=(6.0, 16.0),
        speckle_density=(0.001, 0.006),
        jpeg_quality=(35, 70),
        contrast=(0.75, 1.15),
        brightness=(-25.0, 15.0),
        shadow_strength=(0.05, 0.2),
        paper_tint=(0.02, 0.10),
        border_px=(8, 40),
        fold_line_prob=0.35,
        grayscale=True,
    ),
    # Phone photo of a page: perspective, strong lighting gradient, colour cast.
    "photo": ScanProfile(
        angle_deg=(-10.0, 10.0),
        perspective=(0.02, 0.07),
        blur_sigma=(0.3, 1.2),
        motion_blur_px=(0, 5),
        noise_std=(4.0, 12.0),
        speckle_density=(0.0, 0.0),
        jpeg_quality=(50, 85),
        contrast=(0.8, 1.1),
        brightness=(-30.0, 10.0),
        shadow_strength=(0.15, 0.45),
        paper_tint=(0.03, 0.15),
        border_px=(30, 90),
        fold_line_prob=0.2,
        grayscale=False,
    ),
}


DIGITAL = "digital"
"""Render mode for the native text PDF (no augmentation, one variant)."""


def render_modes() -> list[str]:
    return [DIGITAL, *PROFILES]


def parse_modes(names: list[str]) -> list[str]:
    """Validates mode names (empty = all modes)."""
    available = render_modes()
    unknown = [n for n in names if n not in available]
    if unknown:
        raise ValueError(f"Unknown render mode(s): {unknown}. Available: {available}")
    return names or available


def _u(rng: np.random.Generator, r: tuple[float, float]) -> float:
    return float(rng.uniform(r[0], r[1])) if r[1] > r[0] else float(r[0])


def _i(rng: np.random.Generator, r: tuple[int, int]) -> int:
    return int(rng.integers(r[0], r[1] + 1)) if r[1] > r[0] else int(r[0])


def sample_params(profile_name: str, seed: int) -> dict:
    """Draws one concrete augmentation parameter set from a named profile."""
    p = PROFILES[profile_name]
    rng = np.random.default_rng(seed)
    params = {
        "profile": profile_name,
        "seed": seed,
        "angle_deg": round(_u(rng, p.angle_deg), 2),
        "perspective": round(_u(rng, p.perspective), 4),
        "blur_sigma": round(_u(rng, p.blur_sigma), 2),
        "motion_blur_px": _i(rng, p.motion_blur_px),
        "motion_blur_angle": round(float(rng.uniform(0, 180)), 1),
        "noise_std": round(_u(rng, p.noise_std), 2),
        "speckle_density": round(_u(rng, p.speckle_density), 4),
        "jpeg_quality": _i(rng, p.jpeg_quality),
        "contrast": round(_u(rng, p.contrast), 3),
        "brightness": round(_u(rng, p.brightness), 1),
        "shadow_strength": round(_u(rng, p.shadow_strength), 3),
        "shadow_angle": round(float(rng.uniform(0, 360)), 1),
        "paper_tint": round(_u(rng, p.paper_tint), 3),
        "border_px": _i(rng, p.border_px),
        "fold_line": bool(rng.random() < p.fold_line_prob),
        "grayscale": p.grayscale,
    }
    # Perspective corner offsets are derived here so they are reproducible from the dict.
    params["corner_jitter"] = [
        [round(float(rng.uniform(-1, 1)), 3), round(float(rng.uniform(-1, 1)), 3)] for _ in range(4)
    ]
    params["fold_pos"] = round(float(rng.uniform(0.3, 0.7)), 3)
    return params


def _motion_kernel(length: int, angle: float) -> np.ndarray:
    k = np.zeros((length, length), dtype=np.float32)
    k[length // 2, :] = 1.0
    m = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), angle, 1.0)
    k = cv2.warpAffine(k, m, (length, length))
    return k / max(k.sum(), 1e-6)


def augment(img_rgb: np.ndarray, params: dict) -> np.ndarray:
    """
    Applies the sampled scan effects to an RGB uint8 page image.
    Order follows the physical process: paper -> lighting -> geometry -> optics -> sensor -> codec.
    """
    seed = int(params["seed"])
    rng = np.random.default_rng(seed + 1)
    img = img_rgb.astype(np.float32)
    h, w = img.shape[:2]

    # 1. Paper tint (warm/yellowed paper shows only where the page is light)
    tint = params["paper_tint"]
    if tint > 0:
        paper = np.array(
            [255 * (1 - tint * 0.2), 255 * (1 - tint * 0.5), 255 * (1 - tint)], np.float32
        )
        img = img * (paper / 255.0)

    # 2. Fold line: thin darker crease with a soft shadow
    if params["fold_line"]:
        y = int(h * params["fold_pos"])
        band = np.ones((h, 1), np.float32)
        ys = np.arange(h, dtype=np.float32)
        band[:, 0] -= 0.18 * np.exp(-((ys - y) ** 2) / (2 * 3.0**2))
        img = img * band[:, :, None]

    # 3. Uneven illumination: linear gradient at a random angle
    s = params["shadow_strength"]
    if s > 0:
        theta = np.deg2rad(params["shadow_angle"])
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        proj = (xx / w - 0.5) * np.cos(theta) + (yy / h - 0.5) * np.sin(theta)
        light = 1.0 - s * (proj - proj.min()) / max(float(np.ptp(proj)), 1e-6)
        img = img * light[:, :, None]

    img = np.clip(img, 0, 255).astype(np.uint8)

    # 4. Geometry: place on a dark "scanner bed", then rotate + perspective warp
    border = params["border_px"]
    bed_colour = (45, 45, 48) if border else (255, 255, 255)
    if border:
        img = cv2.copyMakeBorder(
            img, border, border, border, border, cv2.BORDER_CONSTANT, value=bed_colour
        )
        h, w = img.shape[:2]

    j = params["perspective"]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + np.float32(params["corner_jitter"]) * np.float32([w * j, h * j])
    persp = cv2.getPerspectiveTransform(src, dst)
    rot = np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), params["angle_deg"], 1.0), [0, 0, 1]])
    img = cv2.warpPerspective(
        img,
        rot @ persp,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=bed_colour if border else (255, 255, 255),
    )

    # 5. Optics: defocus + motion blur
    if params["blur_sigma"] > 0:
        img = cv2.GaussianBlur(img, (0, 0), params["blur_sigma"])
    if params["motion_blur_px"] >= 2:
        img = cv2.filter2D(
            img, -1, _motion_kernel(params["motion_blur_px"], params["motion_blur_angle"])
        )

    # 6. Sensor: contrast/brightness, gaussian noise, salt-and-pepper speckle
    img = cv2.convertScaleAbs(img, alpha=params["contrast"], beta=params["brightness"])
    if params["noise_std"] > 0:
        noise = rng.normal(0, params["noise_std"], img.shape).astype(np.float32)
        img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    d = params["speckle_density"]
    if d > 0:
        mask = rng.random(img.shape[:2])
        img[mask < d / 2] = 0
        img[mask > 1 - d / 2] = 255

    if params["grayscale"]:
        img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)

    # 7. Codec: JPEG round-trip for block artefacts
    ok, buf = cv2.imencode(
        ".jpg",
        cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
        [cv2.IMWRITE_JPEG_QUALITY, params["jpeg_quality"]],
    )
    if not ok:
        raise RuntimeError("JPEG encoding failed during scan augmentation")
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def params_summary(params: dict) -> dict:
    """Compact subset of parameters worth showing in reports."""
    keys = (
        "profile",
        "angle_deg",
        "perspective",
        "blur_sigma",
        "noise_std",
        "jpeg_quality",
        "shadow_strength",
        "fold_line",
    )
    return {k: params[k] for k in keys if k in params}

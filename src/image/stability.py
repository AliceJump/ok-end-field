"""帧稳定性度量：imagehash(phash/dhash) 与 skimage(ssim) 的 numpy/cv2 等价实现。

仅依赖 numpy 与 opencv，避免为单一功能引入 scipy/scikit-image 传递依赖树
（scipy+pywavelets+networkx 等，安装体积约 160MB）。

- perceptual_hash: 对应 imagehash.phash / imagehash.dhash
- hamming_distance: 对应 ImageHash.__sub__（不同比特数）
- ssim_score: 对应 skimage.metrics.structural_similarity（灰度图，高斯窗）
"""
from typing import Union

import cv2
import numpy as np

ArrayLike = Union[np.ndarray, "cv2.Mat"]


def _to_gray(img: ArrayLike) -> np.ndarray:
    if img.ndim == 3:
        if img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _bits_to_int(bits: np.ndarray) -> int:
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def _dct_hash(gray_small: np.ndarray, hash_size: int) -> int:
    """DCT 低频块中值二值化（phash 核心）。"""
    dct = cv2.dct(np.float32(gray_small))
    low_freq = dct[:hash_size, :hash_size].flatten()
    median = np.median(low_freq)
    return _bits_to_int(low_freq > median)


def perceptual_hash(img: ArrayLike, method: str = "phash", hash_size: int = 8) -> int:
    """计算感知哈希整数。

    method="phash": 32x32 DCT 左上 8x8 中值哈希（与 imagehash.phash 一致）。
    method="dhash": 相邻像素梯度哈希（与 imagehash.dhash 一致）。
    """
    gray = _to_gray(img)
    if method == "phash":
        size = hash_size * 4
        small = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
        return _dct_hash(small, hash_size)
    if method == "dhash":
        small = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
        diff = small[:, 1:] > small[:, :-1]
        return _bits_to_int(diff.astype(np.uint8))
    raise ValueError(f"Unknown method {method}")


def hamming_distance(h1: int, h2: int) -> int:
    """两个哈希整数的汉明距离。"""
    return (h1 ^ h2).bit_count()


def _gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    ksize = int(2 * round(3 * sigma) + 1)
    return cv2.GaussianBlur(img, (ksize, ksize), sigmaX=sigma, borderType=cv2.BORDER_REPLICATE)


def ssim_score(img1: ArrayLike, img2: ArrayLike) -> float:
    """灰度图 SSIM（Wang et al. 高斯窗 σ=1.5, K1=0.01, K2=0.03）。

    与 skimage.metrics.structural_similarity 默认参数（均匀 7x7 窗）略有差异，
    但对「界面是否稳定」的判定语义一致。
    """
    a = np.float64(_to_gray(img1))
    b = np.float64(_to_gray(img2))
    if a.shape != b.shape:
        raise ValueError("ssim inputs must have the same shape")

    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2

    mu_a = _gaussian_blur(a, 1.5)
    mu_b = _gaussian_blur(b, 1.5)
    mu_aa, mu_bb, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b

    sigma_aa = _gaussian_blur(a * a, 1.5) - mu_aa
    sigma_bb = _gaussian_blur(b * b, 1.5) - mu_bb
    sigma_ab = _gaussian_blur(a * b, 1.5) - mu_ab

    num = (2 * mu_ab + C1) * (2 * sigma_ab + C2)
    den = (mu_aa + mu_bb + C1) * (sigma_aa + sigma_bb + C2)
    return float(np.mean(num / den))

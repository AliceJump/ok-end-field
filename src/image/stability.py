"""甯хǔ瀹氭€у害閲忥細imagehash(phash/dhash) 涓?skimage(ssim) 鐨?numpy/cv2 绛変环瀹炵幇銆?
浠呬緷璧?numpy 涓?opencv锛岄伩鍏嶄负鍗曚竴鍔熻兘寮曞叆 scipy/scikit-image 浼犻€掍緷璧栨爲
锛坰cipy+pywavelets+networkx 绛夛紝瀹夎浣撶Н绾?160MB锛夈€?
- perceptual_hash: 瀵瑰簲 imagehash.phash / imagehash.dhash
- hamming_distance: 瀵瑰簲 ImageHash.__sub__锛堜笉鍚屾瘮鐗规暟锛?- ssim_score: 瀵瑰簲 skimage.metrics.structural_similarity锛堢伆搴﹀浘锛岄珮鏂獥锛?"""
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
    """DCT 浣庨鍧椾腑鍊间簩鍊煎寲锛坧hash 鏍稿績锛夈€?""
    dct = cv2.dct(np.float32(gray_small))
    low_freq = dct[:hash_size, :hash_size].flatten()
    median = np.median(low_freq)
    return _bits_to_int(low_freq > median)


def perceptual_hash(img: ArrayLike, method: str = "phash", hash_size: int = 8) -> int:
    """璁＄畻鎰熺煡鍝堝笇鏁存暟銆?
    method="phash": 32x32 DCT 宸︿笂 8x8 涓€煎搱甯岋紙涓?imagehash.phash 涓€鑷达級銆?    method="dhash": 鐩搁偦鍍忕礌姊害鍝堝笇锛堜笌 imagehash.dhash 涓€鑷达級銆?    """
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
    """涓や釜鍝堝笇鏁存暟鐨勬眽鏄庤窛绂汇€?""
    return (h1 ^ h2).bit_count()


def _gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    ksize = int(2 * round(3 * sigma) + 1)
    return cv2.GaussianBlur(img, (ksize, ksize), sigmaX=sigma, borderType=cv2.BORDER_REPLICATE)


def ssim_score(img1: ArrayLike, img2: ArrayLike) -> float:
    """鐏板害鍥?SSIM锛圵ang et al. 楂樻柉绐?蟽=1.5, K1=0.01, K2=0.03锛夈€?
    涓?skimage.metrics.structural_similarity 榛樿鍙傛暟锛堝潎鍖€ 7x7 绐楋級鐣ユ湁宸紓锛?    浣嗗銆岀晫闈㈡槸鍚︾ǔ瀹氥€嶇殑鍒ゅ畾璇箟涓€鑷淬€?    """
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

import base64  # 解码测试内嵌的实测图标裁剪。
import unittest  # 使用仓库统一的单元测试入口。
from pathlib import Path  # 使用仓库内的运行模板路径。
from types import SimpleNamespace  # 构造离线执行器。
from unittest.mock import Mock  # 固定相机界面状态。

import cv2  # 复现截图缩放和构造反例。
import numpy as np  # 在内存中构造不同分辨率截图。
from ok.feature.FeatureSet import FeatureSet  # 使用实际模板加载与匹配。

from src.tasks.onetime.EnvironmentMonitoringPhotoTask import EnvironmentMonitoringPhotoTask  # 测试生产识别流程。

# 正例取自 3d96f6f988804b93bc83d1b67f82de37.png 的（47，720）至（83，756）区域。
CHECKED_PNG = (  # 保存实测图标 PNG 小裁剪，测试不依赖本地 logs。
    "iVBORw0KGgoAAAANSUhEUgAAACQAAAAkCAIAAABuYg/PAAAMhUlEQVRIDXXBeYxd1XkA8O8759xzl3fffdvMeGY83u2xwcBgbMOAzRY7wSaBACE4"  # 图标 PNG 数据片段。
    "lEAjUQIlJE2grdxGVAkhqasABUWAmj+KWpE0pU0RbQw1i4sBYwfswTYxiwcvs9izL2+77957lq9q1Ur9p78ffi4qBpyn1mREHmMpkUYqOdwYOmnj"  # 图标 PNG 数据片段。
    "MvM6A4hbIH2iDIM8IiDjYCzMxcYLYKKmWgkwSZljZxsUEJMMA+BF7hhLhojD/wgYx+VStjuy03UVUZV0HplAzAA4xxZXgkNXKKwlAPJCAmJCAAC2"  # 图标 PNG 数据片段。
    "EgRumomdaWWZIkNMMUgUkILI4VYBs+gxPqYyIlrt5hBIE+FKzyWCi3J5zeys1m0uk4LVYmsJUFIDsoV5GZWMjnmlg5pVFhYgzdAYkoGtzcOcaiUZ"  # 图标 PNG 数据片段。
    "JClPDDRaoBREjDMAjsxjfFaZmlJrvZABGSDcWiw5hDWrCsKpG80ZRpx7edtKbUeJA4dJnVY8IR0ol8lx0Cg0hMqAl9OTkzCfZcBYrKhZx9l5tECu"  # 图标 PNG 数据片段。
    "tL4WBS6VBjLYyHSbdCyRIcD+XNguZdNo6WIYYLUFRkMlAiaoHLKgZKaNKnLR0U06ZWFIxmCzgV7eVqugiMamtGGQKarWUClQhpDA1UKkDhmQDKwG"  # 图标 PNG 数据片段。
    "wVAAS8HiVWG+ImVdG+naXMAyTjMts9iXlYplOZOrGFXjYYm6l63vWdW7eM2lXcs2KNUgMnFtujY7/NGxt44een/wxIwy1hLWapAlUHKFagFo5gFD"  # 图标 PNG 数据片段。
    "IIncEtMGcX0u6A6dnOMobRtkFpRABGQ4lXIIzC5dZTuWbO675ubFa27i3GFcMiaElAAERFqlzUa1URs9PvDCvj0vfDY4pTOKY3Q148B0xlpVdATx"  # 图标 PNG 数据片段。
    "TISOg4C4LvAV0OLAR4eYoJ5uYAxyRSKFXcvkltvvW33Z9zj3hSPh/0VEdujkod2/euijDw9PjIOZ5wyYSVmrBo4PnhIuCSCG15fK8yItSIy49CJD"  # 图标 PNG 数据片段。
    "no0CbGunck/7Tffs6lpxPTIHABBp/NS7Y6cPnRs51KjPSlYJ8uHKvk3lhZvLHUuJ4PjAL3a/8JOzQ8PjkygSZjJImtzWME0wijBtMbKAN3aUm1oR"  # 图标 PNG 数据片段。
    "BymoeyGCRDdvO9vZdXd8q2/zg46MiEyzemrfi09MDL5enZ3VFlJFFoADME6FJe39197fapn9r/3t+NmRYls5F3XMDJ8bORU3qoyqCERScCJIE8Qv"  # 图标 PNG 数据片段。
    "t1e8HMVeKy8xEo7yTVjGr93zzQuvfMj1y0R6cmTv7ue/N3lm3EUkIsZ4rWUyJIlIzDRdIxAdAq2RrHNx/3XXfumHR/a/+Oo/PTs3UaudY1aTdDhj"  # 图标 PNG 数据片段。
    "UK8h3tZTZsx6BQxCy6UlB1ZefN4d33mmbeG6LGlNDO3e+/wD07V6bQZRCbIYJ7aVgBeg61rpYNXLJAe0UKr4l1z+lUuv+UFUXNhsVF96bufr//xr"  # 图标 PNG 数据片段。
    "U83qVfRcrM4y4RLe3F7xAxO1kSsxV9F+lL/xmzvP779bOP7s+Acv/91Xm+OTNa1mpjlvOq5PM3MEhgkHvNDkQsx8JV30Qq9vw40brn6kUFqkVWty"  # 图标 PNG 数据片段。
    "dN9vnv/p/j1HqGG1oizmcROFJLy1q+w4xi9RRweKvO5cvuaWe/+6a/kmrVof7Hng6NsvZqnSxp45BUYLtKgUMMRcZAnAK9jM1fmyv6rv2i3bdpXL"  # 图标 PNG 数据片段。
    "q1QWD5149c1//dHhtwdnJsC1jDGI57nSkKSIty8tM6ETx3T3QFSA/q1fvPKWnxQqy8ZG3nvz+R2zU1MpWRdg+BP086LagDgBX3JlbKFoHR/cilh/"  # 图标 PNG 数据片段。
    "1fUbrvyLUmWVUfHIyT0HXnvs7MnjSquTHzNTExypNiUIIVOAOxaVvcjWedrTySoF3HLHH2zY+rDrFz869PTAnh/Fca3YuSitpR8dmS7m+PQUY8QU"  # 图标 PNG 数据片段。
    "N1YQd+3CRf7qdVuv+MKPS23L0qR+9syrA/sfPz14DBVEUcexgbnWtAVL1XHHIjRSwB2LysbTTkF3LaC2iH/+G3/Ud9VORwZH3vzuxwN/L/3Oy7ft"  # 图标 PNG 数据片段。
    "mh4bfuPFx1R1Zr4JnnEyqbhnw9Bfd8V16695tNS2LE3qI6defn//k2dP/S7LsrCwbH3/vS//8qmJwTmjwSZsLqWxBuHti8tuYP2yDktQKNKX7/uz"  # 图标 PNG 数据片段。
    "8y57iHP5/p47pibf6dv0WPfSm5GJw/ueOrbv6bnpcwSgFfNDd8VF115+3aPljt40qY+e3HNg/2MnBo/mGLa1d6265J4L+37/mYevOnV0ggyqmA3V"  # 图标 PNG 数据片段。
    "zWllccfCcuxly7q4CC165uvf/dMLL/8TxuTAG384e/at8/ofWth7FzKpVPrxb39+9O2fxbUxN/BWXPzFtf3fL7WtNDoePbPn8IGnRocHWnFWCiuX"  # 图标 PNG 数据片段。
    "Xn1vb9/9QuQf++OLThye4EDNOT5h7Cg3ePvishNZirLAh6Bg77jv2xds2ilE+NnRJ88MPC78Su+GB3tWfwORWZMNDvx88Mhz3avOX73+r3L5bqPj"  # 图标 PNG 数据片段。
    "sZHXBg4+fuKzw9xoj2PXoo39Wx5t67yiPj/6zCOfH/xgkgwkTT6Z2jFt8avd5czPSjmslNAt2StuvH7zlx4NC0vi6qH3/u22WmMmFy1dc+mDi9fc"  # 图标 PNG 数据片段。
    "DQBGx6On/r29a22QX61VfG7olWMHHx85/aHWijEQHNdcdOMlV/2ln1t04sOXnn3q2zPDDZXgdKrjKpeZi7csqLRkms9TdzsPC3bVxvO3ff2JSvdG"  # 图标 PNG 数据片段。
    "gPTI3t87+9k+sCpfWLK6f2f3qrvgfxndGht67ej+XUOnjlhrEIA45gtdGzd/p7fvXgBn38s//vU/Pj03lTSbODGjUTm6JXBHT1kExnJTLjC/Yksd"  # 图标 PNG 数据片段。
    "uZvu/v6Ki+/kwp+ZeH3gjftVY5oB+fnFazc/3LboVgACSJrVTw/s3Tl84qDONAACgJHs/HXbLrtyVxj1VufHXnrunoNv7z85kRABjbtTCc4KwB09"  # 图标 PNG 数据片段。
    "ZTe0zCILTVC2noebtm+6+pafhsWViPr0Rz87+d4TrbRBCvMdyy+44geVru2t+Pjx3z4yNPhOtRorRQjAGK90X3DZtTuXrroBwDn01lO7/+XJI7+b"  # 图标 PNG 数据片段。
    "amQmSyhf90ZnYQws3lSpgGvdwIQhZvmsPcfLnbntdz5w3sZ7vaCssvmRT/7mk8PPtuamMoaVBStXrr3z3PDe4cGDTRuniigBR3qLl65bu+GBriXb"  # 图标 PNG 数据片段。
    "XC+cmTi2+xffeufAkU/HjVbUmIEFKIemTdVavKFYIc/kPPICaHmqWIRCwMsLo9vu27Xk/K9w4Wbp/NTZl0c+/oex4fdaSTPMl1tZXatUaWOM44dd"  # 图标 PNG 数据片段。
    "y3u3LOn9Wrljg+uF0xOn3vzNnx87+PpnZ5LhOdusUg5FrWXrNah4Am8oVqRngxxkZECSLBuHQ1cnLl3Ru/WuH/Ys34LMIZMmzTPN+gdjZ96dGTtB"  # 图标 PNG 数据片段。
    "UAPGpd8WldeWO/rbu9ZLbwFjYn5u5MBbu9555cXxc804MUNjhJqrFOqZZQn3CfGGYkVIkK4dz9KeiqPAZmj8Eixawru7Orbe+mBv312MSfgvKkvn"  # 图标 PNG 数据片段。
    "VVrl3AIgco+LSDh5ziUAjJ09/vorPzry7n+cm2ppY5MqNOs0OYvMoTCVOiGygNcXygxBCCDPNo0OXc6irLsoDJDIUVvR3bR92+pLb+9a9gX4b4jI"  # 图标 PNG 数据片段。
    "OIf/Y35u+Nh7v3xr768GTwylyihLaQy1cRSM5rSdmoMFRhBBgoTbCyUk8Dwk13gOoEthDupNygUQVazggB4vFbwL+je2Lf/cwhVXdnZ3Z9oBQJU2"  # 图标 PNG 数据片段。
    "x0ePnfx0/4kPXxs9fbJZM/UEmhKyedJVaLXAKDrboCSlHi5SgDlOuD0qMQSHoyZyHUp96zqYD4hxmNfWMFsKMCqDn0cOaKRFzrKEQp9ZsFpRM7Gk"  # 图标 PNG 数据片段。
    "rVEUN1EBm6yDTtFDyFKcrVGLDKSCiJQhhojboxICOByVJbI05SnS0OVwLsAghJ0mQ9NRZpQwS1DsMlNVCBiGEcWpaTawnoJVkJOQGphrslrMfCZa"  # 图标 PNG 数据片段。
    "ddVUWLfgKCCiBlgfWAiI2wslJJQc540GD0hRitaPgBOGxBo8k4HNu45KcEERghydrtoFPgNuh6cJBcZcJ3W0KTiC+wFvpr5u4GSjEeSIIc7XTUpg"  # 图标 PNG 数据片段。
    "OEbEIou4PSohgMPZjNURZ2NZ5vuYZ5xLajYALAlXS3SKHZTUMZ+HGaHbPZxvWSuhnmAjM60YlYEcCm1YFPjVNNFGkUGmYVYbjSARC4wzA7g9KgGA"  # 图标 PNG 数据片段。
    "5JhYEgBzVgMnQdxKI1POHaJAMcVZztZi5uapMQcsb4RABVSrotBoPZSMmRYhYEKoQVskTEFanDGKEBZIKTmvksFtUQkAXI5NYwHAACCQBkAGLmHq"  # 图标 PNG 数据片段。
    "2Iaj0yo4DLwii1OQ3DLPpgk2YxAOugizGXDJCgw4sVpqFBkkAEQLRBYIIJSCiLS1/wm/veArab5T2gAAAABJRU5ErkJggg=="  # 图标 PNG 数据片段。
)  # 结束截图裁剪数据。

# 负例取自 0dd572f2e12345fc9978d44504d3b27d.png 的相同区域。
UNCHECKED_PNG = (  # 保存实测图标 PNG 小裁剪，测试不依赖本地 logs。
    "iVBORw0KGgoAAAANSUhEUgAAACQAAAAkCAIAAABuYg/PAAAG9klEQVRIDVXBcY9md1nG8e91n/M8M9MS/tLt7Mx2VYi0QMSYqLtTSiM2W7sEii8U"  # 图标 PNG 数据片段。
    "WkgwrdG63UZfhPo2gJ3nnN99XZ4zuy34+ehPrt4AQQghQiCSEEACBAGBiIEQAgqUpCSAJEKwIOzErjQh4gTrT68uIQkSILSBgCiVncRspNgkCBIg"  # 图标 PNG 数据片段。
    "ICkJICkJXxEExC6QILHRvatLClFJCNM8Q9gIhfaOTcJGQCQBNiISDrsk7ASSkrCpAoRKpZLuXV+CNgHEJh0pQBJeEUQ7hBwnBIhjVLIDVFE1AbYl"  # 图标 PNG 数据片段。
    "gaqKgKiSkO5dXwKSkhCc6A5kA0kQUFNinBDCH5RKYieg7tjmzjRPPRqQ2Oje9SWQRIhNVWKCSkKxwy52ElWVdmykqSZE2gGVhEJIJAGO3UYiAVTS"  # 图标 PNG 数据片段。
    "Gw+ugNpNCXYDUxVhM80zAlI1VRVJ1TTNk7ttCyWpw5S2JLvXdUVKYrfb7kZiIzZ6+Bd/rqqaqjQlmapA8+EAqZqOx2OVgB4dkjBV1VSgkLGOdV0Q"  # 图标 PNG 数据片段。
    "3nRzp3vEGT0Sd3uTRAVB3/3BD87OziZVTdNrr782qUC3p1vbIPdQFbtM01xVbi/LCSFpjGGbJNA94rS7R9dUPTq47dPpNgmxqvT43ffmwxxHAlQl"  # 图标 PNG 数据片段。
    "QhJJr3/jdcKyLGMMSNtnx7MQD7dH1ZR4HU3s2GEsp9HtTTzPc/dw9+l0SmwHovfef780JSZUle2aap5n4Oy4K5Vjt3/7u9/bbWeep9Ede5oPY11G"  # 图标 PNG 数据片段。
    "D3e3nQ1IrMvSY0hUTd09eoQAev+DD4OJEEJVE0qC2xBVXVycxzkej1VaTmsI5HRa1nU5np3HXtfVbjtJL+uaxHaPcTjO0zS7vSyL7dFD73/wIXB2"  # 图标 PNG 数据片段。
    "cV6q7hbarGMdY8RxrGQ6HH/8D++9dn5h9/F4REoy1jV3xugqPXv+HLSuy8Z2Etvruh4Oh+4x3MvppJ989M9VSjgcDj2G7XaDfKcEKRXvvvvO2eEg"  # 图标 PNG 数据片段。
    "SlWHeUYESHwnybMvnxOSjDFOp1P36I3T7nU5nU636zr09KOfx5FEQGzsFoqDZLuqbL/zzuOzw2HY//Pf/4t0fnZEAi7v3fvmN7+R5D++eA5ZdusY"  # 图标 PNG 数据片段。
    "w3H3uL29Xcc61rGOJYn+8ck/hbBJpJI0H+Z8xU7VlPjdH75zcX4+1vXL//yv29vbqjocjiJ/+e1vXT940GN8+q+fjR7dHmO0vY71dPtiXVZEbBNA"  # 图标 PNG 数据片段。
    "P37yQQKkquKwEXYEqiqVSolvHj2+OD/r7s+fPbMzeohU1ffefvv66qrtf/ns0+W0LGOxvS7rGGuP4QQBkkjQhz/9CDLWAUxTIfXobiOqFDthc3Pz"  # 图标 PNG 数据片段。
    "+Px47NGfP/+ipO5OLPH2W29dXl726N989unt71/cLrfublsigThJ1ZQY0E8++nmc7gFImqZZUlUljB5jXbuH0M3No4uLC3d/8eWXy2lpj+5e1/Xt"  # 图标 PNG 数据片段。
    "t75z+calk19+8nHvhlAgRCCJnYDEevLhU0kBEqmmKpWqym3H0zR19xjj5vHj4zydluXzZ89evHgR9+1y6jG+//3vPbh+EOcXn3xcUwkSEoMOxxk0"  # 图标 PNG 数据片段。
    "1mG3wKAnT5/O8+EwH8YYEEnuBiXehKzLmvjRo0fzNHWP7gbWMdzdbkISSR//6hOVNoGaap6m7l7XQQLhjp7+9Gc92glJsEAqdlnWdVlOtscYNzc3"  # 图标 PNG 数据片段。
    "F2dnYwy7TXptCBIvhY9//QmoqqapQtw7oKoSICA9efp0jHY3uyTuMdpelrV72G67e/z93/7d4TB3D7cRQkhsElDIv33+70iCJChxJIEgIO7o5r0f"  # 图标 PNG 数据片段。
    "ebTvJF7Wxbt2p+3EY4zE67qGECQgUiGRDa9ISUQQBBCQAGEXQH/1N389RscGRq9jDMC73Gk7IewiJAHilRBFIgZBQBJ2eCWoyE5C3/rOt0c7NpB4"  # 图标 PNG 数据片段。
    "E2JbmlBiJ5EQ4g8kIRIgUlVC7BABUgIEwk5VFSDR5ZtXAWJ2kQRKIkAScUKQxB2BVOwSdklAkISXJLELElHNEzagyzfv80oICBCbBImk7Xk6VMk2"  # 图标 PNG 数据片段。
    "ICEIu8QJdwRJ2AkhXhEkQALR5Zv3JfFKurORNE1TVcV2PE1zbHaBJCRA+FrYOBaqaUqcBMQmCUhsdP/hFTtJIbQ3qdI0zSQhRAhiIImkbNiEnQCF"  # 图标 PNG 数据片段。
    "l0L4WkAQEISN7j+8SqgqcBwnkkDEIJWEEgMJJGwEEgkigSAVcdiElwKCgPia7j+8EpIIkJ1UgN0glYQSJ+ErIUSgCCUIwp0QQoIAsQkgiWDQ9Z89"  # 图标 PNG 数据片段。
    "gEhyTABxx9lpQzZOSrVxHDtBEhIYSEgiQTAUAkL4Iwm6enjNLnwlG14SRKQ7kuZ5TuLuEAIlsYlDwh8JOyVhIxQQBN1/eA2Il+Kk25IO82wHgkjC"  # 图标 PNG 数据片段。
    "JiRhF0AiIQkQxC5sQtgJELuASKKrhw9QCAhCuwGpqgTE4U4SNiFkI0AkAYWA2CTh/5GAJLz0f5y7IcfnC5n8AAAAAElFTkSuQmCC"  # 图标 PNG 数据片段。
)  # 结束截图裁剪数据。


def marker_crop(encoded):  # 从实测裁剪恢复 BGR 像素。
    return cv2.imdecode(
        np.frombuffer(base64.b64decode(encoded), dtype=np.uint8), cv2.IMREAD_COLOR
    )  # 不读取外部截图文件。


def photo_frame(crop, size=(1920, 1080)):  # 把真实标记放回参考截图位置后进行整体缩放。
    frame = np.full((1080, 1920, 3), 35, dtype=np.uint8)  # 周围内容不参与标记识别。
    frame[720:756, 47:83] = crop  # 保留实测图标的颜色、抗锯齿与局部背景。
    return cv2.resize(frame, size, interpolation=cv2.INTER_LINEAR)  # 模拟常见截图分辨率。


class TestEnvironmentMonitoringPhoto(unittest.TestCase):  # 验证真实正负例及可能误触发的近似图形。
    @classmethod
    def setUpClass(cls):  # 使用和应用相同的打包及独立模板源。
        root = Path(__file__).resolve().parents[1]
        cls.features = FeatureSet(
            False,
            str(root / "assets/coco_annotations.json"),
            0.002,
            0.002,
            default_threshold=0.8,
            template_folder=str(root / "assets/templates"),
        )

    def check_marker(self, frame):  # 使用实际任务方法和模板匹配验证标记。
        height, width = frame.shape[:2]
        self.features.check_size(frame)
        task = object.__new__(EnvironmentMonitoringPhotoTask)
        task._executor = SimpleNamespace(
            frame=frame,
            method=SimpleNamespace(width=width, height=height),
            device_manager=SimpleNamespace(supported_ratio=16 / 9),
            feature_set=self.features,
        )
        task._camera_open = Mock(return_value=True)
        task.draw_boxes = Mock()
        return task.photo_condition_met(frame=frame)

    def test_observed_markers_at_multiple_resolutions(self):  # 避免只对原始 1080p 样本有效。
        for size in (
            (1280, 720),
            (1366, 768),
            (1600, 900),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
        ):  # 覆盖缩小与放大。
            for encoded, expected in ((CHECKED_PNG, True), (UNCHECKED_PNG, False)):  # 使用实际黄白勾和灰方块。
                with self.subTest(size=size, checked=expected):  # 报告失败的具体分辨率与状态。
                    self.assertEqual(self.check_marker(photo_frame(marker_crop(encoded), size)), expected)  # 验证结果。

    def test_gray_check_is_not_a_satisfied_marker(self):  # 排除普通灰白 UI 图标造成的误判。
        gray = cv2.cvtColor(marker_crop(CHECKED_PNG), cv2.COLOR_BGR2GRAY)  # 保留勾形而移除满足条件的黄色。
        self.assertFalse(self.check_marker(photo_frame(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))))  # 无黄色时拒绝。

    def test_yellow_ring_without_check_is_rejected(self):  # 单独的亮圆环不能代表拍摄条件已满足。
        crop = marker_crop(CHECKED_PNG)  # 从真实正例保留外圈结构。
        cv2.circle(crop, (18, 18), 9, (35, 35, 35), thickness=-1)  # 清除内部勾形。
        self.assertFalse(self.check_marker(photo_frame(crop)))  # 缺少勾线时必须拒绝。

    def test_solid_yellow_patch_is_rejected(self):  # 场景中的黄色面片也不能代替勾选标记。
        crop = np.full((36, 36, 3), (40, 240, 250), dtype=np.uint8)  # 构造高亮黄色实心区域。
        self.assertFalse(self.check_marker(photo_frame(crop)))  # 实心颜色不能代替图标模板。

    def test_checked_icon_elsewhere_is_ignored(self):  # 识别范围必须限于目标条件图标。
        frame = photo_frame(marker_crop(UNCHECKED_PNG))  # 条件位置仍保持未勾选状态。
        frame[800:836, 100:136] = marker_crop(CHECKED_PNG)  # 在区域外放置完全相同的正例图标。
        self.assertFalse(self.check_marker(frame))  # 区域外正例不能影响结果。

    def test_detection_does_not_change_input(self):  # 纯识别函数不能影响调用方后续 OCR 或截图。
        frame = photo_frame(marker_crop(CHECKED_PNG))  # 使用有效正例。
        original = frame.copy()  # 保存调用前的像素。
        self.assertTrue(self.check_marker(frame))  # 触发完整识别路径。
        np.testing.assert_array_equal(frame, original)  # 原截图保持逐像素一致。

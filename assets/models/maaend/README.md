# Experimental MaaEnd visual dodge

`autofightv13.onnx` is copied unchanged from
[MaaEnd/MaaEnd-AI](https://github.com/MaaEnd/MaaEnd-AI/blob/c0620cd4f06a1a106a734891bd22b4b487ad7f9e/detect/AutoFight/autofightv13.onnx).
Model and source are licensed under **AGPL-3.0**; retain the repository root
`LICENSE`. Model metadata also identifies Ultralytics AGPL-3.0 licensing.
Original model authors: MaaEnd contributors / Ultralytics. No model retraining
or model binary modification is included in this adaptation.

SHA-256: `84e3322e37a475e8729132d5f0b3c212b669f09e686df9ec2c8788c1d139572d`

The trigger and compatibility rectangle are adapted from MaaEnd commit
[`56eaf176719c8f3c7bdc283ac92ec0ee29903b38`](https://github.com/MaaEnd/MaaEnd/tree/56eaf176719c8f3c7bdc283ac92ec0ee29903b38/agent/go-service/autofight)
(`screenanalyzer.go` and `autofight.go`). The Python adaptation adds default-off
configuration, timing-scheduler action guards, class-local NMS, a 0.6 second
cooldown, a deadline-bounded 150 ms recovery wait, stale-inference rejection
and per-battle failure isolation.

## Model contract

- YOLO11s, opset 17, float32 RGB normalized input `[1,3,640,640]`.
- Output `[1,26,8400]`: xywh plus 22 class scores, without embedded NMS.
- Class 12: `EnemyAttackGroundDodge`; class 14: `EnemyDodge`.
- Compatibility mode ignores class 14 centers in `[500,780) x [265,590)`
  relative to 1280x720; class 12 remains eligible. All candidate boxes are checked.
- Detection threshold 0.7, existing OpenVINO letterbox preprocessing.

## Use and limitations

Enable **MaaEnd自动闪避（实验）** in battle settings. It is off by default and
works in both normal and timing combat. The configured Dodge Key is used.
High effects quality is required by the upstream model. Timing combat defers
dodges during pending actions and uncommitted skill windows. Normal combat
checks between synchronous action steps; it cannot interrupt a running skill.
Held normal attack is released before a dodge and resumed by the next loop.
Pursuit dodges and camera centering share a short guard with reactive dodges.
The detector is separate from battle-end recognition and released per battle.

Model compilation, missing assets or inference errors disable this feature for
the current battle, without stopping ordinary combat. Inference results older
than 0.5 seconds are not acted upon. This is experimental: visual warnings can
be missed or mistaken, and synchronous CPU inference can add significant latency.
It does not guarantee survival or perfect timing.

Initialization, a failed inference and stale inference consume the current
loop iteration without pressing dodge. The next iteration refreshes the frame
and checks the combat deadline before making new skill decisions.
Three consecutive slow inferences disable the feature for that battle so that
a slow device does not indefinitely starve the ordinary combat loop.
Rotation `normal_N` filler loops reuse the same battle-local detector.

ONNX structure and a synthetic zero-frame CPU inference were checked in a
cloud environment. No real game-frame accuracy, Windows input, GPU/NPU behavior
or live gameplay effectiveness has been verified. Unit tests use fake frames
and input devices, not the user's computer.

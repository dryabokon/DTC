SiamBAN (MobileNetV3-small backbone, BAN head) trained in https://github.com/Yemen-Romanian/DTC_Training.
Inference code: tools/DL/siamban_pytorch (vendored from DTC_Training). Used as click-to-track model SIAMBAN.

siamban_mobilenetv3.pth = the checkpoint published as
siamban_iou_0.6590_iog_0.7671_center_dist_5.9358_center_dist_norm_0.1915_lost_tracks_2.9000_evaluated_frames_626.8000.pth
(md5 e4a71089d1f385ca3772c8e886ac18e3; the name holds its validation metrics in DTC_Training).

from contextos.config_dim.identify import fuse_config_table


def test_fusion_high_needs_two_paths():
    # B(0.40)+C(0.30)=0.70 >=0.6 且 2 路 -> high
    v = fuse_config_table(path_a=0.0, path_b=1.0, path_c=1.0, path_d=0.0)
    assert v["score"] == 0.70 and v["verdict"] == "high"


def test_fusion_single_path_below_threshold():
    # 只 B(0.40) 单路 -> 0.40 在 [0.3,0.6) -> needs_review
    v = fuse_config_table(path_a=0.0, path_b=1.0, path_c=0.0, path_d=0.0)
    assert v["verdict"] == "needs_review"


def test_fusion_low_skip():
    # 只 A(0.10) -> 0.10 < 0.3 -> skip
    v = fuse_config_table(path_a=1.0, path_b=0.0, path_c=0.0, path_d=0.0)
    assert v["verdict"] == "skip"

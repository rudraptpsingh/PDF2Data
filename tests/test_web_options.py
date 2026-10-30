import pytest

from local_vector_batch.web import embedding_options


def test_cpu_parallel_controls_are_bounded_and_passed_to_pipeline():
    options = embedding_options({"backend": "torch", "device": "cpu", "workers": 2,
                                 "threads_per_worker": 3, "pooling": "mean", "offline": True})
    assert options["overflow_policy"] == "split" and options["token_overlap"] == 64
    assert options["workers"] == 2 and options["threads_per_worker"] == 3
    assert options["pooling"] == "mean" and options["offline"] is True


@pytest.mark.parametrize("options,match", [
    ({"backend": "torch", "device": "gpu", "workers": 2}, "parallel workers require"),
    ({"workers": 64}, "workers must be"),
    ({"batch_size": True}, "batch_size must be"),
    ({"chunk_chars": 100, "overlap": 100}, "overlap must be smaller"),
    ({"backend": "mlx", "pooling": "mean"}, "MLX supports CLS"),
    ({"backend": "mlx", "offline": True}, "MLX supports CLS"),
])
def test_invalid_controls_fail_before_model_loading(options, match):
    with pytest.raises(ValueError, match=match):
        embedding_options(options)

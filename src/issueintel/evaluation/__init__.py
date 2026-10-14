from .metrics import localization_metrics


def open_test_split(*args, **kwargs):
    from .access import open_test_split as gate
    return gate(*args, **kwargs)


__all__ = ["localization_metrics", "open_test_split"]

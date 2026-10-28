def test_package_imports() -> None:
    import collectors
    from collectors import http

    assert collectors.__version__
    assert callable(http.fetch)

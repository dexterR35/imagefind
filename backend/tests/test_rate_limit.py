from app.rate_limit import SlidingWindowRateLimiter


def test_sliding_window_rate_limiter_allows_limit_then_reports_retry():
    now = [100.0]
    limiter = SlidingWindowRateLimiter(2, 10.0, clock=lambda: now[0])

    assert limiter.retry_after("client") is None
    assert limiter.retry_after("client") is None
    assert limiter.retry_after("client") == 10.0

    now[0] = 110.0
    assert limiter.retry_after("client") is None


def test_sliding_window_rate_limiter_separates_clients():
    limiter = SlidingWindowRateLimiter(1, 10.0, clock=lambda: 1.0)

    assert limiter.retry_after("first") is None
    assert limiter.retry_after("second") is None
    assert limiter.retry_after("first") is not None

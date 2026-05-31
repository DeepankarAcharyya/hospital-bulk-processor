import pytest
import httpx
from internal.clients.circuit_breaker import CircuitBreaker, CircuitOpenError


class TestCircuitOpenError:
    def test_stores_retry_after(self):
        err = CircuitOpenError(retry_after=30.0)
        assert err.retry_after == 30.0

    def test_is_exception(self):
        assert isinstance(CircuitOpenError(retry_after=0.0), Exception)


class TestCircuitBreakerClosed:
    async def test_initial_state_is_closed(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        assert breaker.state == "closed"

    async def test_successful_call_stays_closed(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        async with breaker:
            pass
        assert breaker.state == "closed"

    async def test_failure_below_threshold_stays_closed(self):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("timeout")
        assert breaker.state == "closed"

    async def test_failures_at_threshold_open_circuit(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("timeout")
        assert breaker.state == "open"

    async def test_aexit_does_not_suppress_exceptions(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("real error")

    async def test_fatal_4xx_does_not_trip_circuit(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        for _ in range(3):  # more than threshold — still stays closed
            with pytest.raises(httpx.HTTPStatusError):
                async with breaker:
                    raise httpx.HTTPStatusError(
                        "400 Bad Request",
                        request=httpx.Request("POST", "http://test"),
                        response=httpx.Response(400),
                    )
        assert breaker.state == "closed"

    async def test_transient_429_trips_circuit_at_threshold(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        for _ in range(2):
            with pytest.raises(httpx.HTTPStatusError):
                async with breaker:
                    raise httpx.HTTPStatusError(
                        "429 Too Many Requests",
                        request=httpx.Request("POST", "http://test"),
                        response=httpx.Response(429),
                    )
        assert breaker.state == "open"

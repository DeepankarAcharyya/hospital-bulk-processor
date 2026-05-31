import pytest
import httpx
from unittest.mock import patch
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


class TestCircuitBreakerOpen:
    async def test_open_raises_circuit_open_error(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("down")
        assert breaker.state == "open"
        with pytest.raises(CircuitOpenError):
            async with breaker:
                pass

    async def test_open_does_not_execute_body(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("down")
        executed = False
        with pytest.raises(CircuitOpenError):
            async with breaker:
                executed = True
        assert not executed

    async def test_retry_after_is_remaining_timeout(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 110.0  # 10s elapsed
            with pytest.raises(CircuitOpenError) as exc_info:
                async with breaker:
                    pass
        assert exc_info.value.retry_after == pytest.approx(20.0)  # 30 - 10

    async def test_retry_after_never_negative(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 29.9
            with pytest.raises(CircuitOpenError) as exc_info:
                async with breaker:
                    pass
        assert exc_info.value.retry_after >= 0.0


class TestCircuitBreakerHalfOpen:
    async def test_transitions_to_half_open_after_timeout(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
            async with breaker:
                assert breaker.state == "half_open"

    async def test_half_open_success_closes_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
            async with breaker:
                pass
        assert breaker.state == "closed"

    async def test_half_open_success_resets_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            for _ in range(2):
                with pytest.raises(httpx.ConnectError):
                    async with breaker:
                        raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
            async with breaker:
                pass  # probe succeeds → CLOSED, count reset to 0
        # one more failure should NOT reopen (threshold=2, count was reset)
        with pytest.raises(httpx.ConnectError):
            async with breaker:
                raise httpx.ConnectError("one failure")
        assert breaker.state == "closed"

    async def test_half_open_failure_reopens_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("still down")
        assert breaker.state == "open"

    async def test_half_open_fatal_error_stays_half_open(self):
        """Fatal errors don't change circuit state — circuit only tracks availability."""
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        with patch("internal.clients.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            with pytest.raises(httpx.ConnectError):
                async with breaker:
                    raise httpx.ConnectError("down")
            mock_time.monotonic.return_value = 31.0
            with pytest.raises(httpx.HTTPStatusError):
                async with breaker:
                    raise httpx.HTTPStatusError(
                        "422 Unprocessable",
                        request=httpx.Request("POST", "http://test"),
                        response=httpx.Response(422),
                    )
        assert breaker.state == "half_open"  # neither closed (no success) nor open (not transient)

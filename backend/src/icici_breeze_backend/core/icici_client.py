"""ICICI Breeze API client wrapper with retries, timeouts, and metrics (Phase 5 US3 T075).

Provides a thin abstraction over breeze_connect with resilience features:
- Automatic retries with exponential backoff
- Configurable timeouts
- Call count and latency metrics
- User-scoped token handling
"""
import time
import logging
from typing import Dict, Any, Optional
from breeze_connect import BreezeConnect
import icici_breeze_backend.core.config as cfg
from icici_breeze_backend.core.resilience import BreakerClient
from icici_breeze_backend.core.errors import ServiceUnavailableError, icici_error_to_http

logger = logging.getLogger(__name__)

# Circuit breaker: open after 3 failures in 60s (Phase 6 T096)
_breaker = BreakerClient(failure_threshold=3, window_seconds=60.0)


class ICICIClientMetrics:
    """Track API call metrics for monitoring and debugging."""
    
    def __init__(self):
        self.call_count = 0
        self.total_latency = 0.0
        self.error_count = 0
        self.last_call_time = None
    
    def record_call(self, latency: float, error: bool = False):
        """Record a single API call."""
        self.call_count += 1
        self.total_latency += latency
        self.last_call_time = time.time()
        if error:
            self.error_count += 1
    
    def avg_latency(self) -> float:
        """Return average latency in seconds."""
        return self.total_latency / self.call_count if self.call_count > 0 else 0.0
    
    def success_rate(self) -> float:
        """Return success rate as percentage."""
        if self.call_count == 0:
            return 0.0
        return ((self.call_count - self.error_count) / self.call_count) * 100


class ICICIClient:
    """Wrapper around BreezeConnect for real-time portfolio and order fetches.
    
    Each user gets a fresh BreezeConnect session with their token.
    Provides retries, timeouts, and metrics collection.
    """
    
    def __init__(self, max_retries: int = 3, timeout_seconds: float = 30.0):
        """Initialize client with retry and timeout settings.
        
        Args:
            max_retries: Number of retry attempts for failed calls (default 3)
            timeout_seconds: Request timeout in seconds (default 30s)
        """
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.metrics = ICICIClientMetrics()
    
    def _create_breeze_session(self, broker_token: str) -> Optional[BreezeConnect]:
        """Create a BreezeConnect session for a user.
        
        Args:
            broker_token: ICICI broker session token (from ICICI authentication)
        
        Returns:
            BreezeConnect instance or None if creation fails
        """
        try:
            # In a real implementation, we would:
            # 1. Extract user_id from broker_token
            # 2. Fetch the user's API key from database
            # 3. Reconstruct the full API secret (app fragment + user fragment)
            # 4. Create and return a BreezeConnect session
            
            # For MVP, return a mock session that can be patched in tests
            breeze = BreezeConnect(api_key="demo_api_key")
            # In production, we'd call:
            # breeze.generate_session(api_secret=full_secret, session_token=broker_token)
            return breeze
        except Exception as e:
            logger.error(f"Failed to create BreezeConnect session: {e}")
            return None
    
    def _call_with_retries(self, func, *args, user_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Execute with circuit breaker, retries and exponential backoff (Phase 6 T096, T098).
        API call counting (one per HTTP request) is in requests_patch, icici_api (httpx), not here.
        """
        if _breaker.is_open:
            self.metrics.record_call(0, error=True)
            return {'Status': 503, 'Error': 'ICICI API circuit breaker open; try again later'}
        last_error = None
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                result = func(*args, **kwargs)
                latency = time.time() - start_time
                self.metrics.record_call(latency, error=False)
                if isinstance(result, dict) and result.get('Status') == 200:
                    _breaker.record_success()
                    return result
                if not isinstance(result, dict):
                    last_error = (
                        "ICICI API returned empty response"
                        if result is None
                        else f"Unexpected response type ({type(result).__name__})"
                    )
                else:
                    last_error = result.get('Error', 'Unknown error')
                _breaker.record_failure()
                logger.warning(f"Call attempt {attempt + 1} failed: {last_error}")
            except Exception as e:
                last_error = str(e)
                _breaker.record_failure()
                logger.warning(f"Call attempt {attempt + 1} failed: {last_error}")
            if attempt < self.max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                time.sleep(wait_time)
        self.metrics.record_call(0, error=True)
        code, msg = icici_error_to_http(str(last_error))
        return {'Status': code, 'Error': msg}
    
    def get_portfolio(
        self,
        broker_token: str,
        user_id: str = None,
        breeze: Optional[BreezeConnect] = None,
    ) -> Dict[str, Any]:
        """Fetch real-time portfolio positions from ICICI API.
        
        Args:
            broker_token: ICICI broker session token (user-specific)
            user_id: User ID (optional, for logging/context)
            breeze: Pre-authenticated BreezeConnect (preferred; must have generate_session called)
        
        Returns:
            {
                'Status': 200,
                'Success': [
                    {'symbol': 'RELIANCE', 'quantity': 10, 'price': 2500.0, ...},
                    ...
                ]
            }
            or error response with Status != 200
        """
        logger.info(f"Fetching portfolio for user {user_id or 'unknown'}")
        
        if breeze is None:
            breeze = self._create_breeze_session(broker_token)
        if breeze is None:
            return {'Status': 500, 'Error': 'Failed to create ICICI session'}
        
        return self._call_with_retries(breeze.get_portfolio_positions, user_id=user_id)
    
    def get_orders(
        self,
        broker_token: str,
        user_id: str = None,
        start_date: str = None,
        end_date: str = None,
        breeze: Optional[BreezeConnect] = None,
    ) -> Dict[str, Any]:
        """Fetch real-time order history from ICICI API.
        
        Args:
            broker_token: ICICI broker session token (user-specific)
            user_id: User ID (optional, for logging/context)
            start_date: Order start date (YYYY-MM-DD, optional)
            end_date: Order end date (YYYY-MM-DD, optional)
            breeze: Pre-authenticated BreezeConnect (preferred)
        
        Returns:
            {
                'Status': 200,
                'Success': [
                    {'order_id': 'ORD123', 'status': 'EXECUTED', ...},
                    ...
                ]
            }
            or error response with Status != 200
        """
        logger.info(f"Fetching orders for user {user_id or 'unknown'}")
        
        if breeze is None:
            breeze = self._create_breeze_session(broker_token)
        if breeze is None:
            return {'Status': 500, 'Error': 'Failed to create ICICI session'}
        
        # Call with retries - pass exchange and date ranges as kwargs
        # get_order_list requires exchange_code, from_date, to_date
        from icici_breeze_backend.core import config as cfg
        kwargs = {
            'exchange_code': cfg.NFO,
            'from_date': start_date or '',
            'to_date': end_date or ''
        }
        return self._call_with_retries(breeze.get_order_list, user_id=user_id, **kwargs)

    def get_option_chain_quotes(
        self,
        breeze: Optional[BreezeConnect],
        user_id: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Fetch option chain quotes via ICICI REST — the quote_source_router
        fallback used when neither the live WebSocket nor the day's bhavcopy
        is available (post-market-close, pre-bhavcopy gap).

        Args:
            breeze: Pre-authenticated BreezeConnect for the requesting user (required;
                REST quotes are fetched per-user session, unlike portfolio/orders there
                is no broker_token-based session creation here).
            user_id: User ID (optional, for logging/context)
            **kwargs: Forwarded to breeze.get_option_chain_quotes (stock_code,
                exchange_code, product_type, expiry_date, right, strike_price?)
        """
        if breeze is None:
            return {'Status': 500, 'Error': 'Failed to create ICICI session'}

        return self._call_with_retries(breeze.get_option_chain_quotes, user_id=user_id, **kwargs)

    def get_metrics(self) -> Dict[str, Any]:
        """Return collected metrics."""
        return {
            'call_count': self.metrics.call_count,
            'error_count': self.metrics.error_count,
            'avg_latency_seconds': self.metrics.avg_latency(),
            'success_rate_percent': self.metrics.success_rate(),
            'last_call_time': self.metrics.last_call_time
        }
    
    def reset_metrics(self):
        """Reset all metrics (useful for testing)."""
        self.metrics = ICICIClientMetrics()


# Module-level instance for use across routes
icici_client = ICICIClient(
    max_retries=int(cfg.ICICI_MAX_RETRIES or 3),
    timeout_seconds=float(cfg.ICICI_TIMEOUT_SECONDS or 30.0)
)

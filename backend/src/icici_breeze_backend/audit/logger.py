"""Audit logging for all operations with user context."""
import logging
from enum import Enum
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Any
import json

from icici_breeze_backend.app.core.timezone import SQLITE_NOW_IST

_logger = logging.getLogger(__name__)


class OperationType(str, Enum):
    """Types of operations to audit."""
    # Authentication events
    LOGIN = "login"
    LOGOUT = "logout"
    # TOKEN_REFRESH removed – refresh tokens no longer supported
    CREDENTIAL_RECONSTRUCTION = "credential_reconstruction"
    
    # Order operations
    ORDER_PLACE = "order_place"
    ORDER_CANCEL = "order_cancel"
    ORDER_MODIFY = "order_modify"
    ORDER_VIEW = "order_view"
    
    # Portfolio operations
    PORTFOLIO_VIEW = "portfolio_view"
    PORTFOLIO_UPDATE = "portfolio_update"

    # Profit/loss triggered square-off rules (Portfolio > group Exit Rule)
    SQUAREOFF_RULE_ARMED = "squareoff_rule_armed"
    SQUAREOFF_RULE_DISARMED = "squareoff_rule_disarmed"
    SQUAREOFF_RULE_FIRED = "squareoff_rule_fired"
    SQUAREOFF_RULE_FIRE_FAILED = "squareoff_rule_fire_failed"

    # Per-leg GTT OCO exit orders (Portfolio > individual leg > Exit Rule)
    GTT_EXIT_ORDER_PLACED = "gtt_exit_order_placed"
    GTT_EXIT_ORDER_PLACE_FAILED = "gtt_exit_order_place_failed"
    GTT_EXIT_ORDER_CANCELLED = "gtt_exit_order_cancelled"

    # Credential operations
    CREDENTIAL_ROTATION = "credential_rotation"
    CREDENTIAL_REVOCATION = "credential_revocation"
    
    # System operations
    SYSTEM_PERMISSION_CHANGE = "permission_change"
    HTTP_REQUEST = "http_request"  # Phase 6 T106: request logging to audit


@dataclass
class AuditLogEntry:
    """Single audit log entry."""
    user_id: str
    operation_type: OperationType
    resource_type: str
    resource_id: Optional[str]
    action_status: str  # success, failure
    timestamp: datetime
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    error_details: Optional[str] = None
    metadata: Optional[dict] = None


class AuditLogger:
    """Log all operations with user context for compliance."""
    
    def __init__(self, db_session):
        """Initialize audit logger.
        
        Args:
            db_session: Database session for audit log storage
        """
        self.db_session = db_session
    
    def log_operation(self, user_id: str, operation_type: OperationType,
                     resource_type: str, resource_id: Optional[str] = None,
                     action_status: str = "success", 
                     error_details: Optional[str] = None,
                     request_id: Optional[str] = None,
                     ip_address: Optional[str] = None) -> bool:
        """Log an operation with user context.
        
        Args:
            user_id: User performing operation
            operation_type: Type of operation (login, order_place, etc.)
            resource_type: Type of resource affected (Order, Portfolio, etc.)
            resource_id: ID of resource affected
            action_status: 'success' or 'failure'
            error_details: Error message if failed
            request_id: Request correlation ID
            ip_address: IP address of request
        
        Returns:
            True if logged successfully
        """
        if not user_id:
            user_id = "anonymous"

        # Persist audit entry into the audit_log table.
        try:
            from icici_breeze_backend.app.repositories.base import get_sync_connection

            conn = get_sync_connection()
            try:
                conn.execute(
                    f"INSERT INTO audit_log (user_id, operation_type, resource_type, resource_id, action_status, request_id, ip_address, error_details, metadata, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {SQLITE_NOW_IST})",
                    (
                        user_id,
                        operation_type.value if isinstance(operation_type, OperationType) else str(operation_type),
                        resource_type,
                        resource_id,
                        action_status,
                        request_id,
                        ip_address,
                        error_details,
                        None,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            return True
        except Exception as e:
            _logger.warning("Audit log write failed: user_id=%s op=%s: %s", user_id, operation_type, e)
            return False
    
    def log_login(self, user_id: str, request_id: str = None, 
                 ip_address: str = None) -> bool:
        """Log user login event."""
        return self.log_operation(
            user_id, OperationType.LOGIN, "User", user_id,
            request_id=request_id, ip_address=ip_address
        )
    
    def log_logout(self, user_id: str, request_id: str = None,
                  ip_address: str = None) -> bool:
        """Log user logout event."""
        return self.log_operation(
            user_id, OperationType.LOGOUT, "User", user_id,
            request_id=request_id, ip_address=ip_address
        )
    
    def log_order_placement(self, user_id: str, order_id: str,
                           request_id: str = None, ip_address: str = None) -> bool:
        """Log order placement."""
        return self.log_operation(
            user_id, OperationType.ORDER_PLACE, "Order", order_id,
            request_id=request_id, ip_address=ip_address
        )
    
    def log_portfolio_access(self, user_id: str, request_id: str = None,
                            ip_address: str = None) -> bool:
        """Log portfolio view."""
        return self.log_operation(
            user_id, OperationType.PORTFOLIO_VIEW, "Portfolio", user_id,
            request_id=request_id, ip_address=ip_address
        )
    
    def log_credential_access(self, user_id: str, request_id: str = None,
                             ip_address: str = None) -> bool:
        """Log credential reconstruction/access."""
        return self.log_operation(
            user_id, OperationType.CREDENTIAL_RECONSTRUCTION, "Credential", user_id,
            request_id=request_id, ip_address=ip_address
        )

    def log_order_access(self, user_id: str, order_id: str,
                         request_id: str = None, ip_address: str = None) -> bool:
        """Log when a user accesses an order or order list."""
        return self.log_operation(
            user_id, OperationType.ORDER_VIEW, "Order", order_id,
            request_id=request_id, ip_address=ip_address
        )
